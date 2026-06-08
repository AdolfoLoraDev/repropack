"""Publishing helpers: CITATION.cff generation and Zenodo/OSF deposition.

Generates a Citation File Format (CFF 1.2.0) file from manifest metadata and
provides a thin client for depositing a ``.rpk`` to Zenodo or the OSF. Network
uploads require an API token (passed explicitly or via environment variable).
"""

from __future__ import annotations

import json
import os
import re
import zipfile
from pathlib import Path
from typing import Any

import yaml

from repropack.core.manifest import Metadata, ReproPackManifest

# "Name Surname <email> (0000-0000-0000-0000)" — email and ORCID optional.
_AUTHOR_RE = re.compile(
    r"^\s*(?P<name>[^<(]+?)\s*"
    r"(?:<(?P<email>[^>]+)>)?\s*"
    r"(?:\((?P<orcid>\d{4}-\d{4}-\d{4}-\d{3}[\dX])\))?\s*$"
)


def parse_author(author: str) -> dict[str, str]:
    """Parse an author string into CFF fields.

    Args:
        author: e.g. ``"Ana Garcia <ana@x.org> (0000-0002-1825-0097)"``.

    Returns:
        Dict with ``name`` and optionally ``email`` / ``orcid``.
    """
    match = _AUTHOR_RE.match(author)
    if not match:
        return {"name": author.strip()}
    out: dict[str, str] = {"name": match.group("name").strip()}
    if match.group("email"):
        out["email"] = match.group("email").strip()
    if match.group("orcid"):
        out["orcid"] = f"https://orcid.org/{match.group('orcid')}"
    return out


def generate_citation_cff(manifest: ReproPackManifest) -> str:
    """Build a CITATION.cff (CFF 1.2.0) document from manifest metadata.

    Args:
        manifest: The package manifest.

    Returns:
        YAML string in Citation File Format.
    """
    meta: Metadata = manifest.metadata
    authors: list[dict[str, str]] = [parse_author(a) for a in meta.authors]
    if not authors:
        authors = [{"name": "Anonymous"}]

    cff: dict[str, Any] = {
        "cff-version": "1.2.0",
        "message": "If you use this software, please cite it as below.",
        "title": meta.name,
        "version": manifest.repropack_version,
        "date-released": meta.created_at.date().isoformat(),
        "authors": authors,
    }
    if meta.description:
        cff["abstract"] = meta.description

    return str(yaml.safe_dump(cff, sort_keys=False, allow_unicode=True))


def _load_manifest_from_rpk(rpk_path: Path) -> ReproPackManifest:
    """Read and parse the manifest from inside a ``.rpk`` archive."""
    with zipfile.ZipFile(rpk_path, "r") as zf:
        return ReproPackManifest.from_yaml(zf.read("repropack.yml").decode("utf-8"))


def write_citation(rpk_path: Path, output: Path | None = None) -> Path:
    """Generate a ``CITATION.cff`` next to the package (or at ``output``).

    Args:
        rpk_path: Path to the ``.rpk`` package.
        output: Optional explicit output path.

    Returns:
        Path to the written CITATION.cff.
    """
    manifest = _load_manifest_from_rpk(rpk_path)
    cff = generate_citation_cff(manifest)
    target = output or rpk_path.parent / "CITATION.cff"
    target.write_text(cff, encoding="utf-8")
    return target


def _zenodo_deposit(rpk_path: Path, token: str, sandbox: bool = False) -> str:
    """Create a Zenodo deposition and upload the package.

    Args:
        rpk_path: Path to the ``.rpk`` file.
        token: Zenodo API token.
        sandbox: Use the sandbox instance when ``True``.

    Returns:
        URL of the created deposition.

    Raises:
        RuntimeError: On any API error.
    """
    import urllib.request

    host = "sandbox.zenodo.org" if sandbox else "zenodo.org"
    manifest = _load_manifest_from_rpk(rpk_path)
    base = f"https://{host}/api/deposit/depositions"

    metadata = {
        "metadata": {
            "title": manifest.metadata.name,
            "upload_type": "software",
            "description": manifest.metadata.description or manifest.metadata.name,
            "creators": [
                {"name": parse_author(a)["name"]} for a in manifest.metadata.authors
            ]
            or [{"name": "Anonymous"}],
        }
    }
    try:
        req = urllib.request.Request(
            f"{base}?access_token={token}",
            data=json.dumps(metadata).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            deposition = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Zenodo deposition failed: {exc}") from exc

    return str(deposition.get("links", {}).get("html", f"https://{host}"))


def publish_package(
    rpk_path: Path,
    to: str = "citation",
    token: str | None = None,
    sandbox: bool = False,
) -> dict[str, str]:
    """Publish a package: always write CITATION.cff, optionally deposit.

    Args:
        rpk_path: Path to the ``.rpk`` package.
        to: ``citation`` (default), ``zenodo`` or ``osf``.
        token: API token (falls back to ``REPROPACK_<TARGET>_TOKEN`` env var).
        sandbox: Use the provider sandbox where supported.

    Returns:
        Dict describing the result (``citation`` path and optional ``url``).

    Raises:
        RuntimeError: If a remote target lacks a token, or on API errors.
        ValueError: If ``to`` is not a recognised target.
    """
    if to not in ("citation", "zenodo", "osf"):
        raise ValueError(f"Unknown publish target: {to}")

    result: dict[str, str] = {"citation": str(write_citation(rpk_path))}

    if to == "citation":
        return result

    resolved_token = token or os.environ.get(f"REPROPACK_{to.upper()}_TOKEN")
    if not resolved_token:
        raise RuntimeError(
            f"Publishing to {to} requires an API token. Pass --token or set "
            f"REPROPACK_{to.upper()}_TOKEN."
        )

    if to == "zenodo":
        result["url"] = _zenodo_deposit(rpk_path, resolved_token, sandbox=sandbox)
    else:  # osf
        raise RuntimeError(
            "OSF publishing is not yet implemented; CITATION.cff was generated."
        )

    return result
