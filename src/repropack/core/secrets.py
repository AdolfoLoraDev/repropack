"""Detect likely secrets so they are not packaged into a shareable ``.rpk``.

A ``.rpk`` is meant to be published (Zenodo/OSF), so sweeping a project folder
risks leaking credentials. This module flags files that look like secrets by
name or content. Detection is intentionally conservative but covers the common
offenders.
"""

from __future__ import annotations

import re
from pathlib import Path

# Exact basenames that are almost always sensitive.
_SECRET_FILENAMES = {
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".git-credentials",
    "credentials",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
}

# Sensitive file extensions (private keys, keystores).
_SECRET_SUFFIXES = (".pem", ".key", ".pfx", ".p12", ".keystore")

# Content signatures scanned in small text files.
_CONTENT_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key id
    re.compile(r"aws_secret_access_key", re.IGNORECASE),
]

_MAX_CONTENT_SCAN_BYTES = 1_000_000


def is_secret_file(project_path: Path, rel: str) -> bool:
    """Whether ``rel`` (relative to ``project_path``) looks like a secret.

    Args:
        project_path: Project root.
        rel: Relative path of the file.

    Returns:
        ``True`` if the file matches a secret heuristic.
    """
    name = Path(rel).name
    if name in _SECRET_FILENAMES:
        return True
    if name == ".env" or name.startswith(".env."):
        return True
    if Path(rel).suffix in _SECRET_SUFFIXES:
        return True

    path = project_path / rel
    try:
        if path.stat().st_size > _MAX_CONTENT_SCAN_BYTES:
            return False
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return any(pattern.search(text) for pattern in _CONTENT_PATTERNS)


def scan_secrets(project_path: Path, files: list[str]) -> list[str]:
    """Return the subset of ``files`` that look like secrets.

    Args:
        project_path: Project root.
        files: Relative paths to scan.

    Returns:
        Sorted list of flagged relative paths.
    """
    return sorted(f for f in files if is_secret_file(project_path, f))
