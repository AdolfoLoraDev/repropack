"""ReproPack CLI interface with Typer and Rich."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from repropack import __version__
from repropack.core.capture import capture_project
from repropack.core.inspect import inspect_package
from repropack.core.manifest import Step, StepType
from repropack.core.provenance import ProvenanceGraph
from repropack.core.run import run_package
from repropack.core.validate import validate_package

app = typer.Typer(
    name="repropack",
    help="ReproPack: reproducible research packages (.rpk)",
    no_args_is_help=True,
)
console = Console()


@app.command()
def capture(
    project: Path = typer.Option(
        ".",
        "--project",
        "-p",
        help="Path to the project to capture",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
    ),
    output: Path = typer.Option(
        ...,
        "--output",
        "-o",
        help="Output path for the .rpk package",
        resolve_path=True,
    ),
    manual_step: list[str] | None = typer.Option(
        None,
        "--manual-step",
        "-m",
        help="Add a manual step to the manifest (can be repeated).",
    ),
) -> None:
    """Capture a project into a reproducible .rpk package."""
    extra_steps: list[Step] = []
    if manual_step:
        for idx, desc in enumerate(manual_step):
            extra_steps.append(
                Step(
                    id=f"manual_{idx}",
                    type=StepType.MANUAL,
                    description=desc,
                    instructions=desc,
                )
            )

    try:
        result = capture_project(project, output, extra_steps=extra_steps or None)
        console.print(f"[bold green]Success:[/bold green] {result}")
    except Exception as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc


@app.command()
def run(
    rpk: Path = typer.Argument(
        ...,
        help="Path to the .rpk package",
        exists=True,
        dir_okay=False,
        resolve_path=True,
    ),
    tag: str | None = typer.Option(
        None,
        "--tag",
        "-t",
        help="Docker image tag (default: repropack/<name>:latest)",
    ),
    skip_manual: bool = typer.Option(
        False,
        "--skip-manual",
        help="Skip manual steps during reproduction",
    ),
    lite: bool = typer.Option(
        False,
        "--lite",
        help="Run steps directly in the host environment without Docker",
    ),
    no_cache: bool = typer.Option(
        False,
        "--no-cache",
        help="Disable Docker build cache",
    ),
) -> None:
    """Reproduce a .rpk package."""
    try:
        run_package(
            rpk,
            tag=tag,
            skip_manual=skip_manual,
            lite=lite,
            no_cache=no_cache,
        )
    except Exception as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc


@app.command()
def graph(
    rpk: Path = typer.Argument(
        ...,
        help="Path to the .rpk package",
        exists=True,
        dir_okay=False,
        resolve_path=True,
    ),
    format: str = typer.Option(
        "mermaid",
        "--format",
        "-f",
        help="Output format: dot, mermaid, html, png",
    ),
    output: Path = typer.Option(
        ...,
        "--output",
        "-o",
        help="Output file",
        resolve_path=True,
    ),
) -> None:
    """Generate a provenance graph visualization from a .rpk package."""
    import json
    import zipfile

    with zipfile.ZipFile(rpk, "r") as zf:
        prov_data = json.loads(zf.read("provenance.json"))

    # Rebuild PROV document
    from prov.model import ProvDocument

    doc = ProvDocument.deserialize(content=prov_data)
    prov = ProvenanceGraph()
    prov.doc = doc

    if format == "dot":
        text = prov.to_dot()
        output.write_text(text, encoding="utf-8")
    elif format == "mermaid":
        text = prov.to_mermaid()
        output.write_text(text, encoding="utf-8")
    elif format == "html":
        text = prov.to_html(title=f"Provenance: {rpk.name}")
        output.write_text(text, encoding="utf-8")
    elif format == "png":
        dot = prov.to_dot()
        # Requires Graphviz installed on the system
        import graphviz

        src = graphviz.Source(dot)
        src.render(str(output.with_suffix("")), format="png", cleanup=True)
    else:
        console.print(f"[bold red]Unsupported format:[/bold red] {format}")
        raise typer.Exit(code=1)

    console.print(f"[bold green]Graph saved:[/bold green] {output}")


@app.command()
def inspect(
    rpk: Path = typer.Argument(
        ...,
        help="Path to the .rpk package",
        exists=True,
        dir_okay=False,
        resolve_path=True,
    ),
) -> None:
    """Pretty-print the contents of a .rpk package."""
    try:
        inspect_package(rpk)
    except Exception as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc


@app.command()
def validate(
    rpk: Path = typer.Argument(
        ...,
        help="Path to the .rpk package",
        exists=True,
        dir_okay=False,
        resolve_path=True,
    ),
) -> None:
    """Validate the structure and integrity of a .rpk package."""
    try:
        result = validate_package(rpk)
        if result.valid:
            console.print("[bold green]✔ Package is valid[/bold green]")
        else:
            console.print("[bold red]✘ Package validation failed[/bold red]")
        for err in result.errors:
            console.print(f"  [red]ERROR:[/red] {err}")
        for warn in result.warnings:
            console.print(f"  [yellow]WARN:[/yellow] {warn}")
        if not result.valid:
            raise typer.Exit(code=1)
    except Exception as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc


@app.command()
def version() -> None:
    """Show ReproPack version."""
    console.print(f"ReproPack [bold]{__version__}[/bold]")


def main() -> None:
    """Main entry point."""
    app()


if __name__ == "__main__":
    main()
