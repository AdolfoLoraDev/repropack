"""External / large-data handling for ReproPack packages.

Large datasets bloat ``.rpk`` archives and are often hosted externally (DOI,
Zenodo, S3, DVC). This module builds a ``data_manifest.json`` describing such
datasets with checksums and provenance so they can be fetched and verified
separately from the code package.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

# Recognised external-reference URI schemes / prefixes.
_KNOWN_SOURCES = ("http://", "https://", "doi:", "s3://", "dvc://", "zenodo:")


def classify_source(source: str) -> str:
    """Return a coarse source type for an external dataset reference.

    Args:
        source: The reference string (URL, DOI, S3 URI, ...).

    Returns:
        One of ``doi``, ``zenodo``, ``s3``, ``dvc``, ``url`` or ``unknown``.
    """
    lowered = source.lower()
    if lowered.startswith("doi:") or "doi.org" in lowered:
        return "doi"
    if lowered.startswith("zenodo:") or "zenodo.org" in lowered:
        return "zenodo"
    if lowered.startswith("s3://"):
        return "s3"
    if lowered.startswith("dvc://"):
        return "dvc"
    if lowered.startswith(("http://", "https://")):
        return "url"
    return "unknown"


def is_external_reference(source: str) -> bool:
    """Whether a string looks like a supported external reference."""
    lowered = source.lower()
    return lowered.startswith(_KNOWN_SOURCES) or "doi.org" in lowered


def _sha256(path: Path) -> str:
    """Compute the SHA256 of a file."""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def build_data_manifest(
    project_path: Path,
    excluded_files: list[str],
    data_refs: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a data manifest for excluded large files and external references.

    Args:
        project_path: Project root (to resolve relative paths and hash files).
        excluded_files: Relative paths excluded from the package (large files).
        data_refs: Optional mapping of relative path -> external source
            (DOI/Zenodo/S3/DVC/URL).

    Returns:
        A serialisable dict with a ``datasets`` list. Each entry records the
        path, size, SHA256 (when the file is available locally), the external
        ``source`` (if declared) and its classified ``source_type``.
    """
    data_refs = data_refs or {}
    datasets: list[dict[str, Any]] = []

    # Union of excluded files and any declared references.
    paths = sorted(set(excluded_files) | set(data_refs))
    for rel in paths:
        entry: dict[str, Any] = {"path": rel}
        local = project_path / rel
        if local.exists() and local.is_file():
            entry["size_bytes"] = local.stat().st_size
            entry["sha256"] = _sha256(local)
        source = data_refs.get(rel)
        if source:
            entry["source"] = source
            entry["source_type"] = classify_source(source)
        else:
            entry["source"] = None
            entry["source_type"] = "missing"
        datasets.append(entry)

    return {"version": "1.0", "datasets": datasets}


def parse_data_refs(raw: list[str] | None) -> dict[str, str]:
    """Parse ``path=source`` reference strings from the CLI.

    Args:
        raw: List of ``"<relative-path>=<source>"`` strings.

    Returns:
        Mapping of relative path to source string.

    Raises:
        ValueError: If an entry is not in ``path=source`` form.
    """
    refs: dict[str, str] = {}
    for item in raw or []:
        if "=" not in item:
            raise ValueError(
                f"Invalid --data-ref '{item}'; expected format path=source"
            )
        path, source = item.split("=", 1)
        path, source = path.strip(), source.strip()
        if not path or not source:
            raise ValueError(
                f"Invalid --data-ref '{item}'; both path and source are required"
            )
        refs[path] = source
    return refs


def save_data_manifest(manifest: dict[str, Any], path: Path) -> None:
    """Write the data manifest to disk as JSON."""
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
