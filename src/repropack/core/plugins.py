"""Plugin API for custom ``.rpk`` exporters.

Third parties can register exporters that turn a ``.rpk`` package into another
artifact (a citation file, an alternative provenance serialisation, a
Nextflow/Galaxy descriptor, ...). Exporters are discovered two ways:

1. **In-process registration** via :func:`register_exporter` (decorator).
2. **Entry points** in the ``repropack.exporters`` group, so installed
   third-party packages plug in automatically.

An exporter is any callable ``(rpk_path: Path, output: Path) -> Path``.
"""

from __future__ import annotations

import zipfile
from collections.abc import Callable
from pathlib import Path

Exporter = Callable[[Path, Path], Path]

_REGISTRY: dict[str, Exporter] = {}
_ENTRY_POINTS_LOADED = False


def register_exporter(name: str) -> Callable[[Exporter], Exporter]:
    """Register an exporter under ``name`` (decorator).

    Args:
        name: Unique exporter name used on the CLI.

    Returns:
        The decorator that registers and returns the exporter unchanged.
    """

    def decorator(func: Exporter) -> Exporter:
        _REGISTRY[name] = func
        return func

    return decorator


def _load_entry_point_exporters() -> None:
    """Discover exporters registered by installed packages via entry points."""
    global _ENTRY_POINTS_LOADED
    if _ENTRY_POINTS_LOADED:
        return
    _ENTRY_POINTS_LOADED = True
    from importlib import metadata

    # entry_points(group=...) is available on Python >= 3.10 (our minimum).
    for ep in metadata.entry_points(group="repropack.exporters"):
        try:
            _REGISTRY.setdefault(ep.name, ep.load())
        except Exception:  # noqa: BLE001 - a broken plugin must not crash us
            continue


def list_exporters() -> list[str]:
    """Return the sorted names of all available exporters."""
    _load_entry_point_exporters()
    return sorted(_REGISTRY)


def get_exporter(name: str) -> Exporter:
    """Look up an exporter by name.

    Args:
        name: Registered exporter name.

    Returns:
        The exporter callable.

    Raises:
        KeyError: If no exporter is registered under ``name``.
    """
    _load_entry_point_exporters()
    if name not in _REGISTRY:
        raise KeyError(
            f"Unknown exporter '{name}'. Available: {', '.join(list_exporters())}"
        )
    return _REGISTRY[name]


# ---------------------------------------------------------------------------
# Built-in exporters
# ---------------------------------------------------------------------------


@register_exporter("citation")
def _export_citation(rpk_path: Path, output: Path) -> Path:
    """Export a CITATION.cff file."""
    from repropack.core.publish import write_citation

    return write_citation(rpk_path, output)


@register_exporter("provxml")
def _export_provxml(rpk_path: Path, output: Path) -> Path:
    """Export the provenance graph as W3C PROV-XML."""
    import json

    from repropack.core.provenance import ProvenanceGraph

    with zipfile.ZipFile(rpk_path, "r") as zf:
        prov_data = json.loads(zf.read("provenance.json"))
    graph = ProvenanceGraph.from_prov_json(prov_data)
    output.write_text(graph.to_provxml(), encoding="utf-8")
    return output


@register_exporter("mermaid")
def _export_mermaid(rpk_path: Path, output: Path) -> Path:
    """Export the provenance graph as a Mermaid diagram."""
    import json

    from repropack.core.provenance import ProvenanceGraph

    with zipfile.ZipFile(rpk_path, "r") as zf:
        prov_data = json.loads(zf.read("provenance.json"))
    graph = ProvenanceGraph.from_prov_json(prov_data)
    output.write_text(graph.to_mermaid(), encoding="utf-8")
    return output


@register_exporter("repo2docker")
def _export_repo2docker(rpk_path: Path, output: Path) -> Path:
    """Export a jupyter-repo2docker / Binder-buildable context directory.

    Writes the package's ``project/`` files plus the frozen ``Dockerfile`` into
    ``output`` so it can be built with ``jupyter-repo2docker <output>``.

    Args:
        rpk_path: Path to the ``.rpk`` package.
        output: Target directory (created if missing).

    Returns:
        The populated context directory.
    """
    output.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(rpk_path, "r") as zf:
        names = zf.namelist()
        for name in names:
            if name.startswith("project/") and not name.endswith("/"):
                rel = name[len("project/") :]
                dest = output / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(zf.read(name))
        if "Dockerfile" in names:
            (output / "Dockerfile").write_bytes(zf.read("Dockerfile"))
    return output


@register_exporter("reprozip")
def _export_reprozip(rpk_path: Path, output: Path) -> Path:
    """Export a reprozip/reprounzip-style configuration descriptor (YAML).

    Captures the manifest's automatic steps as ``runs`` with their commands,
    inputs and outputs so the experiment can be reconstructed by reprounzip
    tooling.

    Args:
        rpk_path: Path to the ``.rpk`` package.
        output: Target YAML file.

    Returns:
        The written YAML file.
    """
    import yaml

    from repropack.core.manifest import ReproPackManifest

    with zipfile.ZipFile(rpk_path, "r") as zf:
        manifest = ReproPackManifest.from_yaml(zf.read("repropack.yml").decode("utf-8"))
    runs = [
        {
            "id": step.id,
            "command": step.command,
            "inputs": step.inputs,
            "outputs": step.outputs,
        }
        for step in manifest.steps
        if step.command
    ]
    config = {
        "version": "reprozip-repropack-1",
        "experiment": manifest.metadata.name,
        "base_image": manifest.environment.base_image,
        "runs": runs,
    }
    output.write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return output
