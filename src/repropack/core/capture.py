"""Logic for capturing a project into a reproducible .rpk package.

This module provides the :class:`CaptureOrchestrator` which coordinates the
entire capture pipeline: environment detection, lockfile generation, step
inference, Dockerfile generation, PROV graph construction, file hashing and
final ZIP packaging.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from repropack.core.docker_generator import generate_dockerfile
from repropack.core.manifest import (
    EnvironmentSpec,
    Metadata,
    ReproPackManifest,
    Step,
    StepType,
)
from repropack.core.provenance import ProvenanceGraph
from repropack.utils.environment import (
    EnvType,
    detect_env_type,
    generate_conda_lock,
    generate_pip_lock,
    list_project_files,
)

console = Console()

RPK_EXTENSION = ".rpk"


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
    ) -> None:
        """Initialize the orchestrator.

        Args:
            project_path: Path to the project folder to capture.
            output_path: Desired output path for the ``.rpk`` file.
            extra_steps: Optional additional manual/automatic steps to append.
        """
        self.project_path = project_path.resolve()
        self.output_path = output_path.resolve()
        self.extra_steps = extra_steps or []
        self._env_type: EnvType | None = None
        self._lockfile_path: Path | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> Path:
        """Execute the full capture pipeline.

        Returns:
            Path to the generated ``.rpk`` file.
        """
        self._validate_project()

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            self._env_type = self._detect_environment(progress)
            self._lockfile_path = self._generate_lockfile(progress)
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
        if self._env_type is not None:
            if self._env_type.value == "pip":
                lockfile_path = generate_pip_lock(self.project_path)
            elif self._env_type.value == "conda":
                lockfile_path = generate_conda_lock(self.project_path)
        progress.update(task, completed=True)
        if lockfile_path:
            console.print(f"[green]Lockfile:[/green] {lockfile_path.name}")
        return lockfile_path

    def _build_manifest(self) -> ReproPackManifest:
        """Build the Pydantic-validated manifest."""
        name = self.project_path.name or "experiment"
        metadata = Metadata(
            name=name,
            created_at=datetime.now(timezone.utc),
            authors=[],
            description=f"Reproducible package for {name}",
        )

        env_type_value = self._env_type.value if self._env_type else "unknown"
        python_req: str | None = None
        conda_env: str | None = None

        if self._lockfile_path:
            if env_type_value == "pip":
                python_req = self._lockfile_path.name
            elif env_type_value == "conda":
                conda_env = self._lockfile_path.name

        # Fallback to requirements.txt if no lockfile was produced
        if not python_req and (self.project_path / "requirements.txt").exists():
            python_req = "requirements.txt"

        environment = EnvironmentSpec(
            base_image="python:3.11-slim@sha256:placeholder",
            python_requirements=python_req,
            conda_environment=conda_env,
            system_packages=[],
        )

        steps = self._infer_steps()
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
            project_files=list_project_files(self.project_path),
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
        for rel_path in list_project_files(self.project_path):
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
            project_files = list_project_files(self.project_path)
            for rel in project_files:
                src = self.project_path / rel
                dst = staging / "project" / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

            # 2. Save manifest (now includes file_hashes)
            manifest.to_file(staging / "repropack.yml")

            # 3. Save Dockerfile
            (staging / "Dockerfile").write_text(dockerfile_content, encoding="utf-8")

            # 4. Save provenance
            provenance.save(staging / "provenance.json")

            # 5. Copy lockfile if present
            if self._lockfile_path and self._lockfile_path.exists():
                shutil.copy2(self._lockfile_path, staging / self._lockfile_path.name)

            # 6. Create .rpk (zip)
            with zipfile.ZipFile(self.output_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for fpath in staging.rglob("*"):
                    if fpath.is_file():
                        arcname = fpath.relative_to(staging).as_posix()
                        zf.write(fpath, arcname)

        progress.update(task, completed=True)

    def _infer_steps(self) -> list[Step]:
        """Infer automatic steps from common script names.

        Detects:
        - Python scripts: ``prepare.py``, ``train.py``, ``evaluate.py``, etc.
        - Jupyter notebooks: ``*.ipynb``
        - R scripts: ``*.R``
        - Shell scripts: ``*.sh``
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
                )
            )

        return steps


# ------------------------------------------------------------------
# Convenience wrapper (kept for backward compatibility with CLI)
# ------------------------------------------------------------------


def capture_project(
    project_path: Path,
    output_path: Path,
    extra_steps: list[Step] | None = None,
) -> Path:
    """Capture a complete project into a ``.rpk`` package.

    This is a thin wrapper around :class:`CaptureOrchestrator`.

    Args:
        project_path: Path to the project folder.
        output_path: Output path for the ``.rpk`` file.
        extra_steps: Additional steps to include in the manifest.

    Returns:
        Path to the generated ``.rpk`` file.
    """
    orchestrator = CaptureOrchestrator(
        project_path=project_path,
        output_path=output_path,
        extra_steps=extra_steps,
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
