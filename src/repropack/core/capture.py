"""Logic for capturing a project into a reproducible .rpk package."""

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
    detect_env_type,
    generate_conda_lock,
    generate_pip_lock,
    list_project_files,
)

console = Console()

RPK_EXTENSION = ".rpk"


def capture_project(
    project_path: Path,
    output_path: Path,
    extra_steps: list[Step] | None = None,
) -> Path:
    """Capture a complete project into a .rpk package.

    This is the core of the `repropack capture` command. It performs:
    1. Environment detection (pip/conda/poetry).
    2. Lockfile generation.
    3. repropack.yml manifest creation.
    4. Strict Dockerfile generation.
    5. PROV graph construction.
    6. Packaging into .rpk (zip with internal structure).

    Args:
        project_path: Path to the project folder.
        output_path: Output path for the .rpk file (must end in .rpk).
        extra_steps: Additional steps to include in the manifest.

    Returns:
        Path to the generated .rpk file.
    """
    project_path = project_path.resolve()
    if not output_path.name.endswith(RPK_EXTENSION):
        output_path = output_path.with_suffix(RPK_EXTENSION)
    output_path = output_path.resolve()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task_detect = progress.add_task("Detecting environment...", total=None)
        env_type = detect_env_type(project_path)
        progress.update(task_detect, completed=True)
        console.print(f"[green]Environment detected:[/green] {env_type.value}")

        task_lock = progress.add_task("Generating lockfile...", total=None)
        lockfile_path: Path | None = None
        if env_type.value == "pip":
            lockfile_path = generate_pip_lock(project_path)
        elif env_type.value == "conda":
            lockfile_path = generate_conda_lock(project_path)
        progress.update(task_lock, completed=True)
        if lockfile_path:
            console.print(f"[green]Lockfile:[/green] {lockfile_path.name}")

        task_manifest = progress.add_task("Building manifest...", total=None)
        manifest = _build_manifest(
            project_path, env_type.value, lockfile_path, extra_steps
        )
        progress.update(task_manifest, completed=True)

        task_docker = progress.add_task("Generating Dockerfile...", total=None)
        dockerfile_content = generate_dockerfile(
            env=manifest.environment,
            project_files=list_project_files(project_path),
        )
        progress.update(task_docker, completed=True)

        task_prov = progress.add_task("Building PROV graph...", total=None)
        prov = ProvenanceGraph()
        prov.build_from_manifest(manifest)
        progress.update(task_prov, completed=True)

        task_pack = progress.add_task("Packaging .rpk...", total=None)
        _package_rpk(
            project_path=project_path,
            output_path=output_path,
            manifest=manifest,
            dockerfile_content=dockerfile_content,
            provenance=prov,
            lockfile_path=lockfile_path,
        )
        progress.update(task_pack, completed=True)

    console.print(f"[bold green]Package generated:[/bold green] {output_path}")
    return output_path


def _build_manifest(
    project_path: Path,
    env_type: str,
    lockfile_path: Path | None,
    extra_steps: list[Step] | None,
) -> ReproPackManifest:
    """Build the repropack.yml manifest from the project."""
    name = project_path.name or "experiment"
    metadata = Metadata(
        name=name,
        created_at=datetime.now(timezone.utc),
        authors=[],
        description=f"Reproducible package for {name}",
    )

    python_req = (
        str(lockfile_path.name) if lockfile_path and env_type == "pip" else None
    )
    conda_env = (
        str(lockfile_path.name) if lockfile_path and env_type == "conda" else None
    )

    # Fallback to requirements.txt if no lockfile generated
    if not python_req and (project_path / "requirements.txt").exists():
        python_req = "requirements.txt"

    environment = EnvironmentSpec(
        base_image="python:3.11-slim@sha256:placeholder",
        python_requirements=python_req,
        conda_environment=conda_env,
        system_packages=[],
    )

    # Default steps: look for typical scripts
    steps = _infer_steps(project_path)
    if extra_steps:
        steps.extend(extra_steps)

    return ReproPackManifest(
        metadata=metadata,
        environment=environment,
        steps=steps,
    )


def _infer_steps(project_path: Path) -> list[Step]:
    """Try to infer automatic steps from common script names."""
    steps: list[Step] = []
    candidates = [
        ("prepare", ["prepare.py", "preparar.py", "01_prepare.py"]),
        ("train", ["train.py", "entrenar.py", "02_train.py"]),
        ("evaluate", ["evaluate.py", "evaluar.py", "03_evaluate.py"]),
    ]
    for step_id, filenames in candidates:
        for fname in filenames:
            if (project_path / fname).exists():
                steps.append(
                    Step(
                        id=step_id,
                        type=StepType.AUTOMATIC,
                        command=f"python {fname}",
                        description=f"Detected step: {step_id}",
                    )
                )
                break
    return steps


def _package_rpk(
    project_path: Path,
    output_path: Path,
    manifest: ReproPackManifest,
    dockerfile_content: str,
    provenance: ProvenanceGraph,
    lockfile_path: Path | None,
) -> None:
    """Package everything into a .rpk file (internal zip format)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        staging = Path(tmpdir) / "repropack_staging"
        staging.mkdir()

        # 1. Copy project files
        project_files = list_project_files(project_path)
        for rel in project_files:
            src = project_path / rel
            dst = staging / "project" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

        # 2. Save manifest
        manifest.to_file(staging / "repropack.yml")

        # 3. Save Dockerfile
        (staging / "Dockerfile").write_text(dockerfile_content, encoding="utf-8")

        # 4. Save provenance
        provenance.save(staging / "provenance.json")

        # 5. Copy lockfile if it exists
        if lockfile_path and lockfile_path.exists():
            shutil.copy2(lockfile_path, staging / lockfile_path.name)

        # 6. Create .rpk (zip)
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for fpath in staging.rglob("*"):
                if fpath.is_file():
                    arcname = fpath.relative_to(staging).as_posix()
                    zf.write(fpath, arcname)


def _file_hash(path: Path) -> str:
    """Calculate SHA256 of a file."""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()
