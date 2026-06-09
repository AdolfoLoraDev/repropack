"""Logic for capturing a project into a reproducible .rpk package.

This module provides the :class:`CaptureOrchestrator` which coordinates the
entire capture pipeline: environment detection, lockfile generation, step
inference, Dockerfile generation, PROV graph construction, file hashing and
final ZIP packaging.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from repropack.core.apptainer_generator import generate_apptainer_def
from repropack.core.data import build_data_manifest, save_data_manifest
from repropack.core.docker_generator import generate_dockerfile, get_base_image_digest
from repropack.core.gitinfo import get_git_info
from repropack.core.manifest import (
    EnvironmentSpec,
    GitInfo,
    Metadata,
    ReproPackManifest,
    Step,
    StepType,
)
from repropack.core.provenance import ProvenanceGraph
from repropack.core.secrets import scan_secrets
from repropack.utils.environment import (
    EnvType,
    detect_env_type,
    detect_julia_project,
    detect_r_renv,
    generate_conda_lock,
    generate_pip_lock,
    list_project_files,
)

console = Console()

RPK_EXTENSION = ".rpk"

# Minimum date ZIP supports; used when SOURCE_DATE_EPOCH is unset so archives
# are still byte-stable across captures.
_DEFAULT_ZIP_DATE = (1980, 1, 1, 0, 0, 0)


def _source_date_epoch() -> int | None:
    """Return the ``SOURCE_DATE_EPOCH`` value if set and valid, else ``None``."""
    raw = os.environ.get("SOURCE_DATE_EPOCH")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _resolve_created_at() -> datetime:
    """Resolve the manifest timestamp, honouring ``SOURCE_DATE_EPOCH``."""
    epoch = _source_date_epoch()
    if epoch is not None:
        return datetime.fromtimestamp(epoch, tz=timezone.utc)
    return datetime.now(timezone.utc)


def _zip_date_time() -> tuple[int, int, int, int, int, int]:
    """Resolve the fixed ZIP entry timestamp for deterministic packaging."""
    epoch = _source_date_epoch()
    if epoch is None:
        return _DEFAULT_ZIP_DATE
    t = time.gmtime(epoch)
    return (t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec)


class CaptureOrchestrator:
    """Orchestrates the capture of a project into a ``.rpk`` package.

    The orchestrator follows a strict pipeline:

    1. Validate the project path.
    2. Detect the environment type and generate a lockfile.
    3. Infer automatic steps from common script names (including Jupyter).
    4. Build the ``repropack.yml`` manifest (Pydantic validated).
    5. Generate a strict Dockerfile with a pinned base image.
    6. Build the W3C PROV provenance graph.
    7. Compute SHA256 hashes for every project file.
    8. Package everything into a ``.rpk`` ZIP archive.
    """

    def __init__(
        self,
        project_path: Path,
        output_path: Path,
        extra_steps: list[Step] | None = None,
        base_image: str | None = None,
        container: str = "docker",
        exclude_data: bool = False,
        data_threshold_mb: float = 50.0,
        data_refs: dict[str, str] | None = None,
        allow_secrets: bool = False,
    ) -> None:
        """Initialize the orchestrator.

        Args:
            project_path: Path to the project folder to capture.
            output_path: Desired output path for the ``.rpk`` file.
            extra_steps: Optional additional manual/automatic steps to append.
            base_image: Optional Docker base image override (e.g. ``python:3.11-slim``).
            container: Container backend to target: ``docker`` (default),
                ``apptainer`` (also emit ``apptainer.def``), or ``both``.
            exclude_data: If ``True``, files larger than ``data_threshold_mb``
                are excluded from the archive and recorded in
                ``data_manifest.json`` instead.
            data_threshold_mb: Size threshold (in MB) for ``exclude_data``.
            data_refs: Mapping of relative path to an external data source
                (DOI/Zenodo/S3/DVC/URL) recorded in ``data_manifest.json``.
            allow_secrets: If ``True``, do not exclude files flagged as secrets.
        """
        self.project_path = project_path.resolve()
        self.output_path = output_path.resolve()
        self.extra_steps = extra_steps or []
        self.base_image_override = base_image
        self.container = container
        self.exclude_data = exclude_data
        self.data_threshold_bytes = int(data_threshold_mb * 1024 * 1024)
        self.data_refs = data_refs or {}
        self.allow_secrets = allow_secrets
        self._env_type: EnvType | None = None
        self._lockfile_path: Path | None = None
        self._r_renv_path: Path | None = None
        self._julia_project_path: Path | None = None
        self._workdir: Path | None = None
        self._included_files: list[str] = []
        self._excluded_files: list[str] = []
        self._secret_files: list[str] = []
        self._git_info: GitInfo | None = None
        self._user_manifest: ReproPackManifest | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> Path:
        """Execute the full capture pipeline.

        Returns:
            Path to the generated ``.rpk`` file.
        """
        self._validate_project()

        with tempfile.TemporaryDirectory() as workdir:
            self._workdir = Path(workdir)
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                self._user_manifest = self._load_user_manifest()
                self._git_info = get_git_info(self.project_path)
                self._env_type = self._detect_environment(progress)
                self._lockfile_path = self._generate_lockfile(progress)
                self._detect_extra_languages()
                self._select_files()
                manifest = self._build_manifest()
                dockerfile_content = self._generate_dockerfile(progress, manifest)
                file_hashes = self._compute_hashes(progress)
                provenance = self._build_provenance(
                    progress, manifest, file_hashes=file_hashes
                )
                # Inject hashes into the manifest before packaging
                manifest.file_hashes = file_hashes
                self._package(
                    progress,
                    manifest=manifest,
                    dockerfile_content=dockerfile_content,
                    provenance=provenance,
                )

        console.print(f"[bold green]Package generated:[/bold green] {self.output_path}")
        return self.output_path

    # ------------------------------------------------------------------
    # Pipeline steps
    # ------------------------------------------------------------------

    def _validate_project(self) -> None:
        """Ensure the project path exists and is not a ``.rpk`` file."""
        if not self.project_path.exists():
            raise FileNotFoundError(f"Project path does not exist: {self.project_path}")
        if self.project_path.suffix == RPK_EXTENSION:
            raise ValueError(
                f"Project path cannot be a {RPK_EXTENSION} file: {self.project_path}"
            )
        if not self.project_path.is_dir():
            raise NotADirectoryError(
                f"Project path must be a directory: {self.project_path}"
            )
        # Enforce .rpk extension on output
        if not self.output_path.name.endswith(RPK_EXTENSION):
            self.output_path = self.output_path.with_suffix(RPK_EXTENSION)

    def _detect_environment(self, progress: Progress) -> EnvType:
        """Detect the project's dependency manager."""
        task = progress.add_task("Detecting environment...", total=None)
        env_type = detect_env_type(self.project_path)
        progress.update(task, completed=True)
        console.print(f"[green]Environment detected:[/green] {env_type.value}")
        return env_type

    def _generate_lockfile(self, progress: Progress) -> Path | None:
        """Generate a lockfile based on the detected environment."""
        task = progress.add_task("Generating lockfile...", total=None)
        lockfile_path: Path | None = None
        # Generate lockfiles into the orchestrator's temp dir so we never
        # write artifacts back into the user's project directory.
        assert self._workdir is not None
        if self._env_type is not None:
            if self._env_type.value in ("pip", "poetry"):
                # Poetry resolves into a pip-compatible environment, so a
                # pip lockfile captures the installed packages either way.
                lockfile_path = generate_pip_lock(
                    self.project_path, self._workdir / "requirements.lock"
                )
            elif self._env_type.value == "conda":
                lockfile_path = generate_conda_lock(
                    self.project_path, self._workdir / "conda-lock.yml"
                )
        progress.update(task, completed=True)
        if lockfile_path:
            console.print(f"[green]Lockfile:[/green] {lockfile_path.name}")
        return lockfile_path

    def _detect_extra_languages(self) -> None:
        """Detect non-Python language ecosystems (R, Julia) in the project."""
        self._r_renv_path = detect_r_renv(self.project_path)
        self._julia_project_path = detect_julia_project(self.project_path)
        if self._r_renv_path:
            console.print(f"[green]R renv detected:[/green] {self._r_renv_path.name}")
        if self._julia_project_path:
            console.print(
                "[green]Julia project detected:[/green] "
                f"{self._julia_project_path.name}"
            )

    def _load_user_manifest(self) -> ReproPackManifest | None:
        """Load a user-authored ``repropack.yml`` from the project, if present.

        When a project ships its own manifest, its steps, authors, description
        and environment hints take precedence over auto-inference.

        Returns:
            The parsed manifest, or ``None`` if absent or invalid.
        """
        manifest_path = self.project_path / "repropack.yml"
        if not manifest_path.exists():
            return None
        try:
            manifest = ReproPackManifest.from_file(manifest_path)
        except Exception as exc:  # noqa: BLE001 - tolerate a malformed file
            console.print(f"[yellow]Ignoring invalid repropack.yml:[/yellow] {exc}")
            return None
        console.print(
            "[green]Using project repropack.yml[/green] "
            f"({len(manifest.steps)} declared step(s))"
        )
        return manifest

    def _select_files(self) -> None:
        """Partition project files into included, excluded data and secrets.

        When ``exclude_data`` is enabled, files larger than the configured
        threshold are excluded and recorded in ``data_manifest.json``. Files
        declared as external ``data_refs`` are always excluded. Files flagged
        as secrets are dropped from the package unless ``allow_secrets`` is set.
        """
        all_files = list_project_files(self.project_path)

        self._secret_files = (
            [] if self.allow_secrets else scan_secrets(self.project_path, all_files)
        )
        secret_set = set(self._secret_files)
        if self._secret_files:
            console.print(
                f"[bold red]Excluding {len(self._secret_files)} likely "
                "secret file(s)[/bold red] (use --allow-secrets to keep them): "
                + ", ".join(self._secret_files)
            )

        included: list[str] = []
        excluded: list[str] = []
        for rel in all_files:
            if rel in secret_set:
                continue
            src = self.project_path / rel
            too_big = (
                self.exclude_data and src.stat().st_size > self.data_threshold_bytes
            )
            is_ref = rel in self.data_refs
            if too_big or is_ref:
                excluded.append(rel)
            else:
                included.append(rel)
        self._included_files = included
        self._excluded_files = excluded
        if excluded:
            console.print(
                f"[yellow]Excluding {len(excluded)} data file(s) "
                "→ data_manifest.json[/yellow]"
            )

    def _build_manifest(self) -> ReproPackManifest:
        """Build the Pydantic-validated manifest.

        Honours a user-authored ``repropack.yml`` (steps, authors, description,
        base image and system packages) when present, falling back to
        auto-inference otherwise. Always records the resolved base-image digest,
        generated lockfile, Git provenance and a reproducible timestamp.
        """
        user = self._user_manifest
        name = self.project_path.name or "experiment"
        if user is not None:
            metadata = Metadata(
                name=user.metadata.name or name,
                created_at=_resolve_created_at(),
                authors=user.metadata.authors,
                description=user.metadata.description
                or f"Reproducible package for {name}",
                git=self._git_info,
            )
        else:
            metadata = Metadata(
                name=name,
                created_at=_resolve_created_at(),
                authors=[],
                description=f"Reproducible package for {name}",
                git=self._git_info,
            )

        env_type_value = self._env_type.value if self._env_type else "unknown"
        python_req: str | None = None
        conda_env: str | None = None

        if self._lockfile_path:
            if env_type_value in ("pip", "poetry"):
                python_req = self._lockfile_path.name
            elif env_type_value == "conda":
                conda_env = self._lockfile_path.name

        # Fallback to requirements.txt if no lockfile was produced
        if not python_req and (self.project_path / "requirements.txt").exists():
            python_req = "requirements.txt"

        # Resolve base image with digest. Precedence: --base-image override,
        # then a user manifest's base image, then the default.
        if self.base_image_override:
            base_image = self.base_image_override
        elif user is not None:
            base_image = user.environment.base_image
        else:
            base_image = "python:3.11-slim"
        resolved_image = get_base_image_digest(base_image)

        if user is not None and user.environment.system_packages:
            system_packages = user.environment.system_packages
        else:
            system_packages = self._infer_system_packages()

        environment = EnvironmentSpec(
            base_image=resolved_image,
            python_requirements=python_req,
            conda_environment=conda_env,
            r_renv="renv.lock" if self._r_renv_path else None,
            julia_project="Project.toml" if self._julia_project_path else None,
            system_packages=system_packages,
        )

        steps = list(user.steps) if user is not None else self._infer_steps()
        if self.extra_steps:
            steps.extend(self.extra_steps)

        return ReproPackManifest(
            metadata=metadata,
            environment=environment,
            steps=steps,
        )

    def _generate_dockerfile(
        self, progress: Progress, manifest: ReproPackManifest
    ) -> str:
        """Generate the strict Dockerfile content."""
        task = progress.add_task("Generating Dockerfile...", total=None)
        content = generate_dockerfile(
            env=manifest.environment,
            project_files=self._included_files,
        )
        progress.update(task, completed=True)
        return content

    def _build_provenance(
        self,
        progress: Progress,
        manifest: ReproPackManifest,
        file_hashes: dict[str, str],
    ) -> ProvenanceGraph:
        """Construct the W3C PROV graph."""
        task = progress.add_task("Building PROV graph...", total=None)
        prov = ProvenanceGraph()
        prov.build_from_manifest(manifest, file_hashes=file_hashes)
        progress.update(task, completed=True)
        return prov

    def _compute_hashes(self, progress: Progress) -> dict[str, str]:
        """Calculate SHA256 for every project file."""
        task = progress.add_task("Computing file hashes...", total=None)
        hashes: dict[str, str] = {}
        for rel_path in self._included_files:
            src = self.project_path / rel_path
            hashes[rel_path] = _file_hash(src)
        progress.update(task, completed=True)
        console.print(f"[green]Hashed {len(hashes)} files[/green]")
        return hashes

    def _package(
        self,
        progress: Progress,
        manifest: ReproPackManifest,
        dockerfile_content: str,
        provenance: ProvenanceGraph,
    ) -> None:
        """Create the ``.rpk`` ZIP archive with the documented internal layout."""
        task = progress.add_task("Packaging .rpk...", total=None)
        with tempfile.TemporaryDirectory() as tmpdir:
            staging = Path(tmpdir) / "repropack_staging"
            staging.mkdir()

            # 1. Copy project files
            project_files = self._included_files
            for rel in project_files:
                src = self.project_path / rel
                dst = staging / "project" / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

            # 2. Save manifest (now includes file_hashes)
            manifest.to_file(staging / "repropack.yml")

            # 3. Save Dockerfile
            (staging / "Dockerfile").write_text(dockerfile_content, encoding="utf-8")

            # 3b. Optionally emit an Apptainer/Singularity definition file
            if self.container in ("apptainer", "both"):
                def_content = generate_apptainer_def(
                    env=manifest.environment,
                    project_files=self._included_files,
                )
                (staging / "apptainer.def").write_text(def_content, encoding="utf-8")

            # 4c. Emit data_manifest.json for excluded/external datasets
            if self._excluded_files or self.data_refs:
                data_manifest = build_data_manifest(
                    self.project_path,
                    self._excluded_files,
                    self.data_refs,
                )
                save_data_manifest(data_manifest, staging / "data_manifest.json")

            # 4. Save provenance
            provenance.save(staging / "provenance.json")

            # 5. Stage dependency artifacts (lockfiles, renv.lock, Julia files)
            self._stage_dependency_files(staging)

            # 6. Create .rpk (deterministic zip: stable order, fixed mtimes,
            #    normalised permissions) so the same input yields the same bytes.
            entries = sorted(
                (p for p in staging.rglob("*") if p.is_file()),
                key=lambda p: p.relative_to(staging).as_posix(),
            )
            date_time = _zip_date_time()
            with zipfile.ZipFile(self.output_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for fpath in entries:
                    arcname = fpath.relative_to(staging).as_posix()
                    info = zipfile.ZipInfo(arcname, date_time=date_time)
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o644 << 16
                    zf.writestr(info, fpath.read_bytes())

        progress.update(task, completed=True)

    def _stage_dependency_files(self, staging: Path) -> None:
        """Copy dependency artifacts into the package.

        Lockfiles and language manifests are placed inside ``project/`` so the
        Dockerfile ``COPY`` instructions resolve against the build context. The
        Python/Conda lockfile is additionally copied to the archive root for
        quick inspection without unpacking ``project/``.

        Args:
            staging: Root of the staging directory.
        """
        project_root = staging / "project"

        # Python / Conda lockfile
        if self._lockfile_path and self._lockfile_path.exists():
            shutil.copy2(self._lockfile_path, staging / self._lockfile_path.name)
            shutil.copy2(self._lockfile_path, project_root / self._lockfile_path.name)

        # R renv.lock (flattened to project/renv.lock)
        if self._r_renv_path and self._r_renv_path.exists():
            shutil.copy2(self._r_renv_path, project_root / "renv.lock")

        # Julia Project.toml (+ Manifest.toml when present)
        if self._julia_project_path and self._julia_project_path.exists():
            shutil.copy2(self._julia_project_path, project_root / "Project.toml")
            manifest_toml = self.project_path / "Manifest.toml"
            if manifest_toml.exists():
                shutil.copy2(manifest_toml, project_root / "Manifest.toml")

    def _infer_steps(self) -> list[Step]:
        """Infer automatic steps from common script names.

        Detects:
        - Python scripts: ``prepare.py``, ``train.py``, ``evaluate.py``, etc.
        - Jupyter notebooks: ``*.ipynb``
        - R scripts: ``*.R``
        - Julia scripts: ``*.jl``
        - MATLAB/Octave scripts: ``*.m``
        - Shell scripts: ``*.sh``
        - CMake projects: ``CMakeLists.txt``
        - Makefile targets

        Every inferred step is tagged with its :attr:`Step.language`.
        """
        steps: list[Step] = []
        candidates: list[tuple[str, list[str]]] = [
            ("prepare", ["prepare.py", "preparar.py", "01_prepare.py"]),
            ("train", ["train.py", "entrenar.py", "02_train.py"]),
            ("evaluate", ["evaluate.py", "evaluar.py", "03_evaluate.py"]),
        ]

        for step_id, filenames in candidates:
            for fname in filenames:
                if (self.project_path / fname).exists():
                    steps.append(
                        Step(
                            id=step_id,
                            type=StepType.AUTOMATIC,
                            command=f"python {fname}",
                            description=f"Detected step: {step_id}",
                            language="python",
                        )
                    )
                    break

        # Jupyter notebooks
        for notebook in sorted(self.project_path.glob("*.ipynb")):
            steps.append(
                Step(
                    id=f"jupyter_{notebook.stem}",
                    type=StepType.AUTOMATIC,
                    command=f"jupyter execute {notebook.name}",
                    description=f"Detected Jupyter notebook: {notebook.name}",
                    language="python",
                )
            )

        # R scripts
        for r_script in sorted(self.project_path.glob("*.R")):
            steps.append(
                Step(
                    id=f"r_{r_script.stem}",
                    type=StepType.AUTOMATIC,
                    command=f"Rscript {r_script.name}",
                    description=f"Detected R script: {r_script.name}",
                    language="r",
                )
            )

        # Julia scripts
        for jl_script in sorted(self.project_path.glob("*.jl")):
            steps.append(
                Step(
                    id=f"julia_{jl_script.stem}",
                    type=StepType.AUTOMATIC,
                    command=f"julia --project=. {jl_script.name}",
                    description=f"Detected Julia script: {jl_script.name}",
                    language="julia",
                )
            )

        # MATLAB / Octave scripts (executed with Octave for an open runtime)
        for m_script in sorted(self.project_path.glob("*.m")):
            steps.append(
                Step(
                    id=f"octave_{m_script.stem}",
                    type=StepType.AUTOMATIC,
                    command=f"octave --no-gui {m_script.name}",
                    description=f"Detected MATLAB/Octave script: {m_script.name}",
                    language="octave",
                )
            )

        # Shell scripts
        for sh_script in sorted(self.project_path.glob("*.sh")):
            steps.append(
                Step(
                    id=f"shell_{sh_script.stem}",
                    type=StepType.AUTOMATIC,
                    command=f"bash {sh_script.name}",
                    description=f"Detected shell script: {sh_script.name}",
                    language="shell",
                )
            )

        # CMake projects: configure + build
        if (self.project_path / "CMakeLists.txt").exists():
            steps.append(
                Step(
                    id="cmake_configure",
                    type=StepType.AUTOMATIC,
                    command="cmake -S . -B build",
                    description="Detected CMake project: configure",
                    language="cmake",
                )
            )
            steps.append(
                Step(
                    id="cmake_build",
                    type=StepType.AUTOMATIC,
                    command="cmake --build build",
                    description="Detected CMake project: build",
                    language="cmake",
                    depends_on=["cmake_configure"],
                )
            )

        # Makefile targets
        makefile = self.project_path / "Makefile"
        if makefile.exists():
            targets = _parse_makefile_targets(makefile)
            for target in targets:
                steps.append(
                    Step(
                        id=f"make_{target}",
                        type=StepType.AUTOMATIC,
                        command=f"make {target}",
                        description=f"Detected Makefile target: {target}",
                        language="make",
                    )
                )

        return steps

    def _infer_system_packages(self) -> list[str]:
        """Infer apt system packages required by the detected languages.

        Scans the project for source files that need a compiler toolchain or
        a non-Python runtime and returns the matching Debian package names.

        Returns:
            Sorted list of system package names (possibly empty).
        """
        packages: set[str] = set()

        def _has(*patterns: str) -> bool:
            return any(next(self.project_path.rglob(pat), None) for pat in patterns)

        # C / C++ sources or a build system that drives a compiler
        if (
            _has("*.c", "*.cpp", "*.cc", "*.cxx", "*.h", "*.hpp")
            or (self.project_path / "Makefile").exists()
        ):
            packages.add("build-essential")

        # Fortran sources
        if _has("*.f", "*.f90", "*.f95", "*.for"):
            packages.add("gfortran")

        # CMake build system
        if (self.project_path / "CMakeLists.txt").exists():
            packages.update({"build-essential", "cmake"})

        # MATLAB/Octave scripts
        if _has("*.m"):
            packages.add("octave")

        return sorted(packages)


# ------------------------------------------------------------------
# Convenience wrapper (kept for backward compatibility with CLI)
# ------------------------------------------------------------------


def capture_project(
    project_path: Path,
    output_path: Path,
    extra_steps: list[Step] | None = None,
    base_image: str | None = None,
    container: str = "docker",
    exclude_data: bool = False,
    data_threshold_mb: float = 50.0,
    data_refs: dict[str, str] | None = None,
    allow_secrets: bool = False,
) -> Path:
    """Capture a complete project into a ``.rpk`` package.

    This is a thin wrapper around :class:`CaptureOrchestrator`.

    Args:
        project_path: Path to the project folder.
        output_path: Output path for the ``.rpk`` file.
        extra_steps: Additional steps to include in the manifest.
        base_image: Optional Docker base image override.
        container: Container backend (``docker``, ``apptainer`` or ``both``).
        exclude_data: Exclude large files into ``data_manifest.json``.
        data_threshold_mb: Size threshold (MB) for ``exclude_data``.
        data_refs: Mapping of path to external data source.
        allow_secrets: Keep files flagged as secrets instead of excluding them.

    Returns:
        Path to the generated ``.rpk`` file.
    """
    orchestrator = CaptureOrchestrator(
        project_path=project_path,
        output_path=output_path,
        extra_steps=extra_steps,
        base_image=base_image,
        container=container,
        exclude_data=exclude_data,
        data_threshold_mb=data_threshold_mb,
        data_refs=data_refs,
        allow_secrets=allow_secrets,
    )
    return orchestrator.run()


# ------------------------------------------------------------------
# Utilities
# ------------------------------------------------------------------


def _file_hash(path: Path) -> str:
    """Calculate SHA256 of a file.

    Args:
        path: File path.

    Returns:
        Hexadecimal SHA256 digest.
    """
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _parse_makefile_targets(path: Path) -> list[str]:
    """Extract target names from a Makefile.

    Parses lines that look like ``target: dependencies`` while ignoring
    common non-target patterns (e.g. ``.PHONY``, variable assignments,
    conditional directives).

    Args:
        path: Path to the Makefile.

    Returns:
        Ordered list of target names.
    """
    targets: list[str] = []
    target_pattern = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_\-]*)\s*:")
    ignore_pattern = re.compile(r"^\s*[.#]|:=|\+=|\?=|!=|^\s*if|^\s*else|^\s*endif")

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if line.startswith((" ", "\t")):
            continue
        if stripped.startswith(("#", ".")):
            continue
        if ignore_pattern.search(stripped):
            continue
        match = target_pattern.match(stripped)
        if match:
            targets.append(match.group(1))

    return targets
