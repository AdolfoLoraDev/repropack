"""Logic for reproducing a .rpk package."""

from __future__ import annotations

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


def run_package(
    rpk_path: Path,
    tag: str | None = None,
    skip_manual: bool = False,
) -> None:
    """Execute a reproducible .rpk package.

    Steps:
    1. Unpack the .rpk into a temporary directory.
    2. Read the manifest.
    3. Build the Docker image (or reuse cache).
    4. Execute automatic steps in order.
    5. Show instructions for manual steps.

    Args:
        rpk_path: Path to the .rpk file.
        tag: Docker image tag (default uses manifest name).
        skip_manual: If True, skip manual steps.
    """
    rpk_path = rpk_path.resolve()
    if not rpk_path.exists():
        raise FileNotFoundError(f"Package not found: {rpk_path}")

    with tempfile.TemporaryDirectory() as tmpdir:
        extract_dir = Path(tmpdir) / "extracted"
        extract_dir.mkdir()

        console.print(f"[bold]Unpacking[/bold] {rpk_path.name}...")
        with zipfile.ZipFile(rpk_path, "r") as zf:
            zf.extractall(extract_dir)

        manifest_path = extract_dir / "repropack.yml"
        if not manifest_path.exists():
            raise ValueError("Package does not contain repropack.yml")

        manifest = ReproPackManifest.from_file(manifest_path)
        image_tag = tag or f"repropack/{manifest.metadata.name}:latest"

        project_dir = extract_dir / "project"
        dockerfile_path = extract_dir / "Dockerfile"

        # Build Docker image
        _build_docker_image(dockerfile_path, project_dir, image_tag)

        # Execute steps
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
                    _run_step_in_docker(image_tag, step, project_dir)
                    progress.update(task, completed=True)
                    console.print(f"[green]✔[/green] {step.id} completed")
                elif step.type == StepType.MANUAL:
                    progress.stop()
                    console.print("")
                    console.print(
                        Panel(
                            f"[bold yellow]Manual step:[/bold yellow] {step.id}\n"
                            f"{step.description or ''}\n"
                            f"[bold]Instructions:[/bold] {step.instructions or 'N/A'}",
                            title="⚠️ Action required",
                            border_style="yellow",
                        )
                    )
                    if not skip_manual:
                        if Confirm.ask("Have you completed this manual step?"):
                            console.print("[green]Continuing...[/green]")
                        else:
                            console.print("[red]Reproduction stopped.[/red]")
                            return
                    else:
                        console.print(
                            "[yellow]Skipping manual step (--skip-manual)[/yellow]"
                        )

        console.print("[bold green]✔ Reproduction completed.[/bold green]")


def _build_docker_image(
    dockerfile_path: Path,
    build_context: Path,
    tag: str,
) -> None:
    """Build Docker image from the package Dockerfile."""
    if not shutil.which("docker"):
        raise RuntimeError("Docker is not installed or not in PATH")

    console.print(f"[bold]Building Docker image[/bold] {tag}...")
    try:
        subprocess.run(
            [
                "docker",
                "build",
                "-t",
                tag,
                "-f",
                str(dockerfile_path),
                str(build_context),
            ],
            check=True,
            capture_output=False,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Error building Docker image: {exc}") from exc


def _run_step_in_docker(
    image_tag: str,
    step: Step,
    project_dir: Path,
) -> None:
    """Run an automatic step inside a Docker container.

    Args:
        image_tag: Docker image tag.
        step: Manifest step.
        project_dir: Project directory (mounted as volume).
    """
    if not step.command:
        raise ValueError(f"Step {step.id} has no command defined")

    cmd = [
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
            f"Step {step.id} failed with code {result.returncode}:\n" f"{result.stderr}"
        )
    if result.stdout:
        console.print(result.stdout)
