"""Pretty-print inspection logic for .rpk packages."""

from __future__ import annotations

import zipfile
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

from repropack.core.manifest import ReproPackManifest

console = Console()


def inspect_package(rpk_path: Path) -> None:
    """Pretty-print the contents and metadata of a ``.rpk`` package.

    Args:
        rpk_path: Path to the ``.rpk`` file.

    Raises:
        FileNotFoundError: If the package does not exist.
        ValueError: If the package is malformed.
    """
    rpk_path = rpk_path.resolve()
    if not rpk_path.exists():
        raise FileNotFoundError(f"Package not found: {rpk_path}")

    try:
        zf = zipfile.ZipFile(rpk_path, "r")
    except zipfile.BadZipFile as exc:
        raise ValueError(f"File is not a valid ZIP archive: {rpk_path}") from exc

    with zf:
        namelist = zf.namelist()
        if "repropack.yml" not in namelist:
            raise ValueError("Package does not contain repropack.yml")

        manifest = ReproPackManifest.from_yaml(zf.read("repropack.yml").decode("utf-8"))

    # --- Header -------------------------------------------------------
    console.print(
        Panel.fit(
            f"[bold cyan]{manifest.metadata.name}[/bold cyan]\n"
            f"[dim]{manifest.metadata.description or 'No description'}[/dim]",
            title=f"📦 {rpk_path.name}",
            border_style="cyan",
        )
    )

    # --- Metadata table -----------------------------------------------
    meta_table = Table(title="Metadata", show_header=False)
    meta_table.add_column("Key", style="bold")
    meta_table.add_column("Value")
    meta_table.add_row("Version", manifest.repropack_version)
    meta_table.add_row(
        "Created",
        str(manifest.metadata.created_at),
    )
    meta_table.add_row(
        "Authors",
        ", ".join(manifest.metadata.authors) or "N/A",
    )
    console.print(meta_table)

    # --- Environment table --------------------------------------------
    env_table = Table(title="Environment", show_header=False)
    env_table.add_column("Key", style="bold")
    env_table.add_column("Value")
    env_table.add_row("Base image", manifest.environment.base_image)
    if manifest.environment.python_requirements:
        env_table.add_row("Python reqs", manifest.environment.python_requirements)
    if manifest.environment.conda_environment:
        env_table.add_row("Conda env", manifest.environment.conda_environment)
    if manifest.environment.r_renv:
        env_table.add_row("R renv", manifest.environment.r_renv)
    if manifest.environment.julia_project:
        env_table.add_row("Julia project", manifest.environment.julia_project)
    if manifest.environment.system_packages:
        env_table.add_row(
            "System packages",
            ", ".join(manifest.environment.system_packages),
        )
    console.print(env_table)

    # --- Steps table --------------------------------------------------
    steps_table = Table(title="Steps")
    steps_table.add_column("ID", style="bold")
    steps_table.add_column("Type")
    steps_table.add_column("Command / Instructions")
    for step in manifest.steps:
        if step.type.value == "automatic":
            cmd = step.command or "N/A"
            steps_table.add_row(
                step.id,
                "[green]auto[/green]",
                cmd,
            )
        else:
            inst = step.instructions or step.description or "N/A"
            steps_table.add_row(
                step.id,
                "[yellow]manual[/yellow]",
                inst,
            )
    console.print(steps_table)

    # --- File hashes --------------------------------------------------
    if manifest.file_hashes:
        hash_table = Table(title="File Hashes", show_header=False)
        hash_table.add_column("File")
        hash_table.add_column("SHA256", style="dim")
        for rel, h in manifest.file_hashes.items():
            hash_table.add_row(rel, h[:16] + "...")
        console.print(hash_table)

    # --- ZIP contents tree --------------------------------------------
    tree = Tree("📁 Package contents")
    for name in sorted(namelist):
        parts = name.rstrip("/").split("/")
        node = tree
        for part in parts:
            # Find existing child or add new
            found = None
            for child in node.children:
                if getattr(child, "label", None) == part:
                    found = child
                    break
            if found is None:
                found = node.add(part)
            node = found
    console.print(tree)
