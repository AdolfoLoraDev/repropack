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
    base_image: str | None = typer.Option(
        None,
        "--base-image",
        "-b",
        help="Override the Docker base image (e.g. python:3.11-slim).",
    ),
    container: str = typer.Option(
        "docker",
        "--container",
        "-c",
        help="Container backend: docker, apptainer, or both.",
    ),
    exclude_data: bool = typer.Option(
        False,
        "--exclude-data",
        help="Exclude large data files into data_manifest.json instead.",
    ),
    data_threshold_mb: float = typer.Option(
        50.0,
        "--data-threshold-mb",
        help="Size threshold (MB) above which files are excluded as data.",
    ),
    data_ref: list[str] | None = typer.Option(
        None,
        "--data-ref",
        help="Declare an external dataset as 'path=source' (DOI/Zenodo/S3/URL).",
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
        if container not in ("docker", "apptainer", "both"):
            console.print(
                f"[bold red]Error:[/bold red] invalid --container '{container}' "
                "(choose docker, apptainer, or both)"
            )
            raise typer.Exit(code=1)
        from repropack.core.data import parse_data_refs

        refs = parse_data_refs(data_ref)
        result = capture_project(
            project,
            output,
            extra_steps=extra_steps or None,
            base_image=base_image,
            container=container,
            exclude_data=exclude_data,
            data_threshold_mb=data_threshold_mb,
            data_refs=refs or None,
        )
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
    strict: bool = typer.Option(
        False,
        "--strict",
        help="Verify reproduced outputs against capture-time hashes and fail on drift",
    ),
    container: str = typer.Option(
        "auto",
        "--container",
        "-c",
        help="Container backend: auto (Docker, fallback Apptainer), docker, apptainer.",
    ),
    profile: bool = typer.Option(
        False,
        "--profile",
        help="Record per-step timing to reproduction-profile.json.",
    ),
    fetch_data: bool = typer.Option(
        False,
        "--fetch-data",
        help="Download external datasets from data_manifest.json before running.",
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
            strict=strict,
            container=container,
            profile=profile,
            fetch_data=fetch_data,
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
        help="Output format: dot, mermaid, html, png, provxml",
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

    # Rebuild PROV document (avoids the serializer registry / rdflib).
    prov = ProvenanceGraph.from_prov_json(prov_data)

    if format == "dot":
        text = prov.to_dot()
        output.write_text(text, encoding="utf-8")
    elif format == "mermaid":
        text = prov.to_mermaid()
        output.write_text(text, encoding="utf-8")
    elif format == "html":
        text = prov.to_html(title=f"Provenance: {rpk.name}")
        output.write_text(text, encoding="utf-8")
    elif format in ("provxml", "xml"):
        text = prov.to_provxml()
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
def publish(
    rpk: Path = typer.Argument(
        ...,
        help="Path to the .rpk package",
        exists=True,
        dir_okay=False,
        resolve_path=True,
    ),
    to: str = typer.Option(
        "citation",
        "--to",
        help="Publish target: citation (CITATION.cff only), zenodo, osf.",
    ),
    token: str | None = typer.Option(
        None,
        "--token",
        help="API token (or set REPROPACK_<TARGET>_TOKEN).",
    ),
    sandbox: bool = typer.Option(
        False,
        "--sandbox",
        help="Use the provider sandbox instance (Zenodo).",
    ),
) -> None:
    """Publish a package: generate CITATION.cff and optionally deposit it."""
    from repropack.core.publish import publish_package

    try:
        result = publish_package(rpk, to=to, token=token, sandbox=sandbox)
        console.print(f"[bold green]CITATION.cff:[/bold green] {result['citation']}")
        if "url" in result:
            console.print(f"[bold green]Deposited:[/bold green] {result['url']}")
    except Exception as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc


@app.command()
def diff(
    rpk_a: Path = typer.Argument(
        ...,
        help="Baseline .rpk package",
        exists=True,
        dir_okay=False,
        resolve_path=True,
    ),
    rpk_b: Path = typer.Argument(
        ...,
        help="Package to compare against the baseline",
        exists=True,
        dir_okay=False,
        resolve_path=True,
    ),
) -> None:
    """Show step, environment, package and file differences between two .rpk."""
    from rich.table import Table

    from repropack.core.diff import diff_packages

    try:
        result = diff_packages(rpk_a, rpk_b)
    except Exception as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc

    if result.identical:
        console.print("[bold green]✔ Packages are equivalent[/bold green]")
        return

    if result.base_image_changed:
        old, new = result.base_image_changed
        console.print(f"[bold]Base image:[/bold] {old} [red]→[/red] {new}")

    table = Table(title="Differences")
    table.add_column("Category", style="bold")
    table.add_column("Added", style="green")
    table.add_column("Removed", style="red")
    table.add_column("Changed", style="yellow")
    table.add_row(
        "Steps",
        "\n".join(result.steps_added) or "-",
        "\n".join(result.steps_removed) or "-",
        "\n".join(result.steps_changed) or "-",
    )
    table.add_row(
        "Packages",
        "\n".join(result.packages_added) or "-",
        "\n".join(result.packages_removed) or "-",
        "\n".join(result.packages_changed) or "-",
    )
    table.add_row(
        "Files",
        "\n".join(result.files_added) or "-",
        "\n".join(result.files_removed) or "-",
        "\n".join(result.files_changed) or "-",
    )
    console.print(table)


@app.command()
def export(
    rpk: Path = typer.Argument(
        ...,
        help="Path to the .rpk package",
        exists=True,
        dir_okay=False,
        resolve_path=True,
    ),
    exporter: str | None = typer.Option(
        None,
        "--exporter",
        "-e",
        help="Exporter name (omit to list available exporters).",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Output file path.",
        resolve_path=True,
    ),
) -> None:
    """Export a .rpk via a (possibly third-party) exporter plugin."""
    from repropack.core.plugins import get_exporter, list_exporters

    if exporter is None:
        console.print("[bold]Available exporters:[/bold]")
        for name in list_exporters():
            console.print(f"  - {name}")
        return

    if output is None:
        console.print("[bold red]Error:[/bold red] --output is required")
        raise typer.Exit(code=1)

    try:
        func = get_exporter(exporter)
        result = func(rpk, output)
        console.print(f"[bold green]Exported:[/bold green] {result}")
    except Exception as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc


@app.command()
def sign(
    rpk: Path = typer.Argument(
        ...,
        help="Path to the .rpk package",
        exists=True,
        dir_okay=False,
        resolve_path=True,
    ),
    cosign: bool = typer.Option(
        False,
        "--cosign",
        help="Sign with sigstore cosign instead of a SHA256 attestation.",
    ),
    key: str | None = typer.Option(
        None,
        "--key",
        help="cosign private key path (keyless/OIDC if omitted).",
    ),
) -> None:
    """Sign a .rpk package (SHA256 attestation, or cosign with --cosign)."""
    from repropack.core import sign as sign_mod

    try:
        if cosign:
            out = sign_mod.sign_with_cosign(rpk, key=key)
            console.print(f"[bold green]Signature:[/bold green] {out}")
        else:
            out = sign_mod.attest_package(rpk)
            console.print(f"[bold green]Attestation:[/bold green] {out}")
    except Exception as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc


@app.command()
def verify(
    rpk: Path = typer.Argument(
        ...,
        help="Path to the .rpk package",
        exists=True,
        dir_okay=False,
        resolve_path=True,
    ),
    attestation: Path | None = typer.Option(
        None,
        "--attestation",
        help="Attestation JSON path (defaults to <rpk>.attestation.json).",
    ),
    cosign: bool = typer.Option(
        False,
        "--cosign",
        help="Verify a cosign signature instead of a SHA256 attestation.",
    ),
    signature: Path | None = typer.Option(
        None,
        "--signature",
        help="cosign signature path (with --cosign).",
    ),
    key: str | None = typer.Option(
        None,
        "--key",
        help="cosign public key path (with --cosign).",
    ),
) -> None:
    """Verify a .rpk package's signature or attestation."""
    from repropack.core import sign as sign_mod

    try:
        if cosign:
            if signature is None or key is None:
                console.print(
                    "[bold red]Error:[/bold red] --cosign requires "
                    "--signature and --key"
                )
                raise typer.Exit(code=1)
            sign_mod.verify_with_cosign(rpk, signature, str(key))
        else:
            sign_mod.verify_attestation(rpk, attestation)
        console.print("[bold green]✔ Verification succeeded[/bold green]")
    except typer.Exit:
        raise
    except Exception as exc:
        console.print(f"[bold red]✘ Verification failed:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc


@app.command()
def version() -> None:
    """Show ReproPack version."""
    console.print(f"ReproPack [bold]{__version__}[/bold]")


def main() -> None:
    """Main entry point."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
