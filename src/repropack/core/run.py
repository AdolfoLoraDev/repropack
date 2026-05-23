"""Logic for reproducing a .rpk package.

Provides the :class:`Reproducer` which handles the full reproduction
pipeline: unpack → validate → build → execute → report.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
import zipfile
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
    ) -> None:
        """Initialize the reproducer.

        Args:
            rpk_path: Path to the ``.rpk`` package.
            tag: Docker image tag (default: ``repropack/<name>:latest``).
            skip_manual: If ``True``, skip manual steps without prompting.
            lite: If ``True``, execute steps directly in the host environment
                without Docker.
            no_cache: If ``True``, pass ``--no-cache`` to ``docker build``.
        """
        self.rpk_path = rpk_path.resolve()
        self.tag = tag
        self.skip_manual = skip_manual
        self.lite = lite
        self.no_cache = no_cache
        self._report_lines: list[str] = []

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
                dockerfile_path = extract_dir / "Dockerfile"
                self._build_docker(dockerfile_path, project_dir, image_tag)
            else:
                console.print(
                    "[bold yellow]Lite mode:[/bold yellow] "
                    "Skipping Docker, executing directly in host environment"
                )
                self._report_lines.append("Mode: lite (no container)")

            self._run_steps(manifest, project_dir, image_tag)
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
                    if self.lite:
                        output = self._run_step_lite(step, project_dir)
                    else:
                        output = self._run_step_in_docker(step, image_tag, project_dir)
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
                    if not self.skip_manual:
                        if Confirm.ask("Have you completed this manual step?"):
                            console.print("[green]Continuing...[/green]")
                            self._report_lines.append(
                                f"Step {step.id}: completed manually"
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
) -> None:
    """Execute a reproducible ``.rpk`` package.

    Thin wrapper around :class:`Reproducer`.

    Args:
        rpk_path: Path to the ``.rpk`` file.
        tag: Docker image tag.
        skip_manual: Skip manual steps.
        lite: Run without Docker.
        no_cache: Disable Docker build cache.
    """
    reproducer = Reproducer(
        rpk_path=rpk_path,
        tag=tag,
        skip_manual=skip_manual,
        lite=lite,
        no_cache=no_cache,
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
