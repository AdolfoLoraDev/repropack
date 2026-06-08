"""Logic for reproducing a .rpk package.

Provides the :class:`Reproducer` which handles the full reproduction
pipeline: unpack → validate → build → execute → report.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Confirm

from repropack.core.manifest import ReproPackManifest, Step, StepType

console = Console()


class Reproducer:
    """Reproduces a ReproPack ``.rpk`` package.

    The pipeline is:

    1. **Unpack** the ``.rpk`` into a temporary directory.
    2. **Validate** manifest schema and file hashes.
    3. **Build** the Docker image (or skip in ``--lite`` mode).
    4. **Execute** steps in order (automatic in container/host, manual with
       interactive prompts).
    5. **Report** a summary of the reproduction.
    """

    def __init__(
        self,
        rpk_path: Path,
        tag: str | None = None,
        skip_manual: bool = False,
        lite: bool = False,
        no_cache: bool = False,
        strict: bool = False,
        container: str = "auto",
        profile: bool = False,
    ) -> None:
        """Initialize the reproducer.

        Args:
            rpk_path: Path to the ``.rpk`` package.
            tag: Docker image tag (default: ``repropack/<name>:latest``).
            skip_manual: If ``True``, skip manual steps without prompting.
            lite: If ``True``, execute steps directly in the host environment
                without Docker.
            no_cache: If ``True``, pass ``--no-cache`` to ``docker build``.
            strict: If ``True``, re-hash declared step outputs after
                reproduction and fail if any differ from the capture-time
                hashes stored in the manifest.
            container: Container backend: ``auto`` (prefer Docker, fall back to
                Apptainer), ``docker`` or ``apptainer``.
            profile: If ``True``, record per-step duration and write a
                ``reproduction-profile.json`` next to the package.
        """
        self.rpk_path = rpk_path.resolve()
        self.tag = tag
        self.skip_manual = skip_manual
        self.lite = lite
        self.no_cache = no_cache
        self.strict = strict
        self.container = container
        self.profile = profile
        self._backend = "lite" if lite else container
        self._sif_path: Path | None = None
        self._report_lines: list[str] = []
        self._profile: list[dict[str, float | str]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Execute the full reproduction pipeline."""
        if not self.rpk_path.exists():
            raise FileNotFoundError(f"Package not found: {self.rpk_path}")

        with tempfile.TemporaryDirectory() as tmpdir:
            extract_dir = Path(tmpdir) / "extracted"
            self._unpack(extract_dir)
            manifest = self._validate_package(extract_dir)
            image_tag = self.tag or f"repropack/{manifest.metadata.name}:latest"
            project_dir = extract_dir / "project"

            if not self.lite:
                self._backend = self._select_backend(extract_dir)
                if self._backend == "docker":
                    dockerfile_path = extract_dir / "Dockerfile"
                    self._build_docker(dockerfile_path, project_dir, image_tag)
                else:  # apptainer
                    def_path = extract_dir / "apptainer.def"
                    self._sif_path = self._build_apptainer(
                        def_path, project_dir, manifest.metadata.name
                    )
            else:
                console.print(
                    "[bold yellow]Lite mode:[/bold yellow] "
                    "Skipping Docker, executing directly in host environment"
                )
                self._report_lines.append("Mode: lite (no container)")
                self._check_lite_environment(manifest, extract_dir)

            self._run_steps(manifest, project_dir, image_tag)
            if self.strict:
                self._verify_outputs(manifest, project_dir)
            if self.profile:
                self._write_profile()
            self._generate_report(manifest)

    # ------------------------------------------------------------------
    # Pipeline steps
    # ------------------------------------------------------------------

    def _unpack(self, extract_dir: Path) -> None:
        """Extract the ``.rpk`` ZIP archive safely."""
        console.print(f"[bold]Unpacking[/bold] {self.rpk_path.name}...")
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(self.rpk_path, "r") as zf:
            zf.extractall(extract_dir)
        console.print(f"[green]✔ Unpacked to[/green] {extract_dir}")

    def _validate_package(self, extract_dir: Path) -> ReproPackManifest:
        """Validate the package structure, manifest schema and file hashes.

        Args:
            extract_dir: Root of the extracted package.

        Returns:
            The validated manifest.

        Raises:
            ValueError: If the package is malformed or hashes do not match.
        """
        manifest_path = extract_dir / "repropack.yml"
        if not manifest_path.exists():
            raise ValueError("Package does not contain repropack.yml")

        manifest = ReproPackManifest.from_file(manifest_path)

        # Validate file hashes if present
        if manifest.file_hashes:
            project_dir = extract_dir / "project"
            for rel_path, expected_hash in manifest.file_hashes.items():
                file_path = project_dir / rel_path
                if not file_path.exists():
                    raise ValueError(f"Hash validation failed: missing file {rel_path}")
                actual_hash = _sha256_file(file_path)
                if actual_hash != expected_hash:
                    raise ValueError(
                        f"Hash mismatch for {rel_path}: "
                        f"expected {expected_hash}, got {actual_hash}"
                    )
            console.print(
                f"[green]✔ Hashes validated:[/green] "
                f"{len(manifest.file_hashes)} files"
            )

        return manifest

    def _select_backend(self, extract_dir: Path) -> str:
        """Choose the container backend based on availability and the package.

        Args:
            extract_dir: Root of the extracted package.

        Returns:
            ``"docker"`` or ``"apptainer"``.

        Raises:
            RuntimeError: If the requested (or any) backend is unavailable.
        """
        docker_ok = shutil.which("docker") is not None
        apptainer_ok = (
            shutil.which("apptainer") is not None
            or shutil.which("singularity") is not None
        )
        has_def = (extract_dir / "apptainer.def").exists()

        if self.container == "docker":
            if not docker_ok:
                raise RuntimeError(
                    "Docker is not installed or not in PATH. "
                    "Use --lite to run without containers."
                )
            return "docker"

        if self.container == "apptainer":
            if not apptainer_ok:
                raise RuntimeError(
                    "Apptainer/Singularity is not installed or not in PATH. "
                    "Use --lite to run without containers."
                )
            if not has_def:
                raise RuntimeError(
                    "Package has no apptainer.def; recapture with "
                    "--container apptainer."
                )
            return "apptainer"

        # auto: prefer Docker, fall back to Apptainer on HPC clusters
        if docker_ok:
            return "docker"
        if apptainer_ok and has_def:
            console.print(
                "[bold cyan]Docker unavailable;[/bold cyan] "
                "using Apptainer/Singularity."
            )
            return "apptainer"
        raise RuntimeError(
            "Docker is not installed or not in PATH, and no Apptainer fallback "
            "is available. Use --lite to run without containers."
        )

    def _build_apptainer(
        self,
        def_path: Path,
        build_context: Path,
        name: str,
    ) -> Path:
        """Build an Apptainer ``.sif`` image from the package definition.

        Args:
            def_path: Path to ``apptainer.def``.
            build_context: Project directory; ``%files`` paths resolve here.
            name: Experiment name (used for the ``.sif`` filename).

        Returns:
            Path to the built ``.sif`` image.

        Raises:
            RuntimeError: If the build fails.
        """
        binary = "apptainer" if shutil.which("apptainer") else "singularity"
        sif_path = build_context.parent / f"{name}.sif"
        console.print(f"[bold]Building Apptainer image[/bold] {sif_path.name}...")
        cmd = [
            binary,
            "build",
            "--fakeroot",
            str(sif_path),
            str(def_path),
        ]
        try:
            subprocess.run(cmd, check=True, cwd=str(build_context))
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"Error building Apptainer image: {exc}") from exc

        self._report_lines.append(f"Apptainer image: {sif_path.name}")
        console.print(f"[green]✔ Image built:[/green] {sif_path.name}")
        return sif_path

    def _build_docker(
        self,
        dockerfile_path: Path,
        build_context: Path,
        tag: str,
    ) -> None:
        """Build the Docker image from the package Dockerfile.

        Args:
            dockerfile_path: Path to the Dockerfile inside the extracted pkg.
            build_context: Path to the project directory (build context).
            tag: Image tag.

        Raises:
            RuntimeError: If Docker is unavailable or the build fails.
        """
        if not shutil.which("docker"):
            raise RuntimeError(
                "Docker is not installed or not in PATH. "
                "Use --lite to run without containers."
            )

        console.print(f"[bold]Building Docker image[/bold] {tag}...")
        cmd: list[str] = [
            "docker",
            "build",
            "-t",
            tag,
            "-f",
            str(dockerfile_path),
            str(build_context),
        ]
        if self.no_cache:
            cmd.append("--no-cache")

        try:
            subprocess.run(cmd, check=True, capture_output=False)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"Error building Docker image: {exc}") from exc

        self._report_lines.append(f"Docker image: {tag}")
        console.print(f"[green]✔ Image built:[/green] {tag}")

    def _run_steps(
        self,
        manifest: ReproPackManifest,
        project_dir: Path,
        image_tag: str,
    ) -> None:
        """Execute all reproduction steps in order.

        Args:
            manifest: Validated manifest.
            project_dir: Path to the ``project/`` folder.
            image_tag: Docker image tag (ignored in lite mode).
        """
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            for step in manifest.steps:
                if step.type == StepType.AUTOMATIC:
                    task = progress.add_task(
                        f"Running [cyan]{step.id}[/cyan]...", total=None
                    )
                    started = time.perf_counter()
                    if self._backend == "lite":
                        output = self._run_step_lite(step, project_dir)
                    elif self._backend == "apptainer":
                        output = self._run_step_in_apptainer(step, project_dir)
                    else:
                        output = self._run_step_in_docker(step, image_tag, project_dir)
                    duration = time.perf_counter() - started
                    if self.profile:
                        self._profile.append(
                            {"step": step.id, "seconds": round(duration, 4)}
                        )
                    progress.update(task, completed=True)
                    console.print(f"[green]✔[/green] {step.id} completed")
                    if output:
                        self._report_lines.append(f"Step {step.id}: success")
                elif step.type == StepType.MANUAL:
                    progress.stop()
                    console.print("")
                    console.print(
                        Panel(
                            f"[bold yellow]Manual step:[/bold yellow] "
                            f"{step.id}\n"
                            f"{step.description or ''}\n"
                            f"[bold]Instructions:[/bold] "
                            f"{step.instructions or 'N/A'}",
                            title="⚠️ Action required",
                            border_style="yellow",
                        )
                    )
                    if step.outputs:
                        console.print(
                            "[dim]Declared affected files:[/dim] "
                            + ", ".join(step.outputs)
                        )
                    if not self.skip_manual:
                        if Confirm.ask("Have you completed this manual step?"):
                            completed_at = datetime.now(timezone.utc).isoformat()
                            console.print("[green]Continuing...[/green]")
                            self._report_lines.append(
                                f"Step {step.id}: completed manually at {completed_at}"
                            )
                        else:
                            console.print("[red]Reproduction stopped.[/red]")
                            self._report_lines.append(
                                f"Step {step.id}: aborted by user"
                            )
                            return
                    else:
                        console.print(
                            "[yellow]Skipping manual step " "(--skip-manual)[/yellow]"
                        )
                        self._report_lines.append(
                            f"Step {step.id}: skipped (--skip-manual)"
                        )

        console.print("[bold green]✔ Reproduction completed.[/bold green]")

    def _run_step_in_docker(
        self,
        step: Step,
        image_tag: str,
        project_dir: Path,
    ) -> str:
        """Run an automatic step inside a Docker container.

        Args:
            step: Manifest step.
            image_tag: Docker image tag.
            project_dir: Project directory mounted as ``/workspace``.

        Returns:
            Captured stdout from the container.

        Raises:
            ValueError: If the step has no command.
            RuntimeError: If the container exits with non-zero code.
        """
        if not step.command:
            raise ValueError(f"Step {step.id} has no command defined")

        cmd: list[str] = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{project_dir.resolve()}:/workspace",
            "-w",
            "/workspace",
            image_tag,
            "sh",
            "-c",
            step.command,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"Step {step.id} failed with code "
                f"{result.returncode}:\n{result.stderr}"
            )
        if result.stdout:
            console.print(result.stdout)
        return result.stdout

    def _run_step_in_apptainer(self, step: Step, project_dir: Path) -> str:
        """Run an automatic step inside an Apptainer container.

        Args:
            step: Manifest step.
            project_dir: Project directory bound to ``/workspace``.

        Returns:
            Captured stdout.

        Raises:
            ValueError: If the step has no command.
            RuntimeError: If the container exits with a non-zero code.
        """
        if not step.command:
            raise ValueError(f"Step {step.id} has no command defined")
        if self._sif_path is None:
            raise RuntimeError("Apptainer image was not built")

        binary = "apptainer" if shutil.which("apptainer") else "singularity"
        cmd = [
            binary,
            "exec",
            "--bind",
            f"{project_dir.resolve()}:/workspace",
            "--pwd",
            "/workspace",
            str(self._sif_path),
            "sh",
            "-c",
            step.command,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"Step {step.id} failed with code "
                f"{result.returncode}:\n{result.stderr}"
            )
        if result.stdout:
            console.print(result.stdout)
        return result.stdout

    def _run_step_lite(self, step: Step, project_dir: Path) -> str:
        """Run an automatic step directly in the host environment.

        Args:
            step: Manifest step.
            project_dir: Working directory for the subprocess.

        Returns:
            Captured stdout.

        Raises:
            ValueError: If the step has no command.
            RuntimeError: If the command exits with non-zero code.
        """
        if not step.command:
            raise ValueError(f"Step {step.id} has no command defined")

        console.print(f"[dim]Lite exec:[/dim] {step.command} in {project_dir}")
        result = subprocess.run(
            step.command,
            shell=True,
            cwd=str(project_dir),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Step {step.id} failed with code "
                f"{result.returncode}:\n{result.stderr}"
            )
        if result.stdout:
            console.print(result.stdout)
        return result.stdout

    def _verify_outputs(
        self,
        manifest: ReproPackManifest,
        project_dir: Path,
    ) -> None:
        """Verify reproduced outputs against capture-time hashes (strict mode).

        For every path declared in a step's ``outputs``, re-hash the matching
        files under ``project_dir`` and compare them against the SHA256 digests
        stored in ``manifest.file_hashes`` at capture time. Output paths may be
        files or directories (directories match all captured files beneath
        them).

        Args:
            manifest: Validated manifest with capture-time ``file_hashes``.
            project_dir: Path to the reproduced ``project/`` folder.

        Raises:
            RuntimeError: If any output file is missing or its hash differs.
        """
        expected = manifest.file_hashes
        declared: list[str] = []
        for step in manifest.steps:
            declared.extend(step.outputs)

        if not declared:
            console.print(
                "[yellow]Strict mode:[/yellow] no step outputs declared; "
                "nothing to verify."
            )
            return

        mismatches: list[str] = []
        checked = 0
        for out in declared:
            norm = out.rstrip("/")
            matched = {
                rel: h
                for rel, h in expected.items()
                if rel == norm or rel.startswith(f"{norm}/")
            }
            if not matched:
                console.print(
                    f"[yellow]Strict mode:[/yellow] no captured hash for "
                    f"output '{out}'; cannot verify."
                )
                continue
            for rel, exp_hash in matched.items():
                fpath = project_dir / rel
                if not fpath.exists():
                    mismatches.append(f"{rel}: missing after reproduction")
                    continue
                actual = _sha256_file(fpath)
                checked += 1
                if actual != exp_hash:
                    mismatches.append(
                        f"{rel}: expected {exp_hash[:12]}…, got {actual[:12]}…"
                    )

        if mismatches:
            self._report_lines.append("Strict: FAILED")
            raise RuntimeError(
                "Strict reproducibility check failed:\n  " + "\n  ".join(mismatches)
            )

        console.print(
            f"[bold green]✔ Strict check passed:[/bold green] "
            f"{checked} output file(s) match capture-time hashes"
        )
        self._report_lines.append(f"Strict: {checked} output(s) verified")

    def _check_lite_environment(
        self,
        manifest: ReproPackManifest,
        extract_dir: Path,
    ) -> None:
        """Warn when the host environment diverges from the captured lockfile.

        In lite mode steps run against whatever is installed on the host, so a
        mismatched Python version or package version silently breaks
        reproducibility. This surfaces such drift as warnings.

        Args:
            manifest: The package manifest.
            extract_dir: Root of the extracted package.
        """
        # Python version: parse "python:X.Y..." from the base image.
        match = re.search(r"python:(\d+)\.(\d+)", manifest.environment.base_image)
        if match:
            want = (int(match.group(1)), int(match.group(2)))
            have = (sys.version_info.major, sys.version_info.minor)
            if want != have:
                console.print(
                    f"[yellow]⚠ Python mismatch:[/yellow] package expects "
                    f"{want[0]}.{want[1]}, host has {have[0]}.{have[1]}"
                )
                self._report_lines.append(
                    f"Warning: Python {want[0]}.{want[1]} expected, "
                    f"host {have[0]}.{have[1]}"
                )

        # Package versions: compare lockfile pins with installed distributions.
        lockfile = manifest.environment.python_requirements
        if not lockfile:
            return
        lock_path = extract_dir / lockfile
        if not lock_path.exists():
            return

        from importlib import metadata

        pin_re = re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*==\s*([^\s;#\\]+)")
        mismatches: list[str] = []
        for line in lock_path.read_text(encoding="utf-8").splitlines():
            m = pin_re.match(line)
            if not m:
                continue
            name, want_ver = m.group(1), m.group(2)
            try:
                have_ver = metadata.version(name)
            except metadata.PackageNotFoundError:
                mismatches.append(f"{name}: expected {want_ver}, not installed")
                continue
            if have_ver != want_ver:
                mismatches.append(f"{name}: expected {want_ver}, host has {have_ver}")

        if mismatches:
            shown = mismatches[:10]
            console.print(
                f"[yellow]⚠ {len(mismatches)} package mismatch(es) vs "
                "lockfile:[/yellow]"
            )
            for line in shown:
                console.print(f"  [yellow]-[/yellow] {line}")
            if len(mismatches) > len(shown):
                extra = len(mismatches) - len(shown)
                console.print(f"  [dim]... and {extra} more[/dim]")
            self._report_lines.append(
                f"Warning: {len(mismatches)} package(s) differ from lockfile"
            )

    def _write_profile(self) -> None:
        """Write per-step timing data next to the package as JSON."""
        total = round(sum(float(e["seconds"]) for e in self._profile), 4)
        payload = {
            "package": self.rpk_path.name,
            "backend": self._backend,
            "total_seconds": total,
            "steps": self._profile,
        }
        out = self.rpk_path.parent / "reproduction-profile.json"
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        console.print(f"[green]✔ Profile written:[/green] {out.name} ({total}s total)")
        self._report_lines.append(f"Profile: {total}s total → {out.name}")

    def _generate_report(self, manifest: ReproPackManifest) -> None:
        """Print a final reproduction report."""
        console.print("")
        console.print(
            Panel(
                "\n".join(
                    [
                        f"[bold]Experiment:[/bold] " f"{manifest.metadata.name}",
                        f"[bold]Steps executed:[/bold] " f"{len(manifest.steps)}",
                    ]
                    + self._report_lines
                ),
                title="Reproduction Report",
                border_style="green",
            )
        )


# ------------------------------------------------------------------
# Convenience wrapper (kept for backward compatibility with CLI)
# ------------------------------------------------------------------


def run_package(
    rpk_path: Path,
    tag: str | None = None,
    skip_manual: bool = False,
    lite: bool = False,
    no_cache: bool = False,
    strict: bool = False,
    container: str = "auto",
    profile: bool = False,
) -> None:
    """Execute a reproducible ``.rpk`` package.

    Thin wrapper around :class:`Reproducer`.

    Args:
        rpk_path: Path to the ``.rpk`` file.
        tag: Docker image tag.
        skip_manual: Skip manual steps.
        lite: Run without Docker.
        no_cache: Disable Docker build cache.
        strict: Verify reproduced outputs against capture-time hashes.
        container: Container backend (``auto``, ``docker`` or ``apptainer``).
        profile: Record per-step timing to ``reproduction-profile.json``.
    """
    reproducer = Reproducer(
        rpk_path=rpk_path,
        tag=tag,
        skip_manual=skip_manual,
        lite=lite,
        no_cache=no_cache,
        strict=strict,
        container=container,
        profile=profile,
    )
    reproducer.run()


# ------------------------------------------------------------------
# Utilities
# ------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    """Calculate SHA256 of a file.

    Args:
        path: File path.

    Returns:
        Hexadecimal SHA256 digest.
    """
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()
