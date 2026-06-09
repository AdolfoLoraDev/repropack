"""Capture Git version-control provenance for a project."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from repropack.core.manifest import GitInfo


def _git(project_path: Path, *args: str) -> str | None:
    """Run a git command in ``project_path``; return stripped stdout or None."""
    try:
        result = subprocess.run(
            ["git", "-C", str(project_path), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except (FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def get_git_info(project_path: Path) -> GitInfo | None:
    """Collect Git provenance (commit, branch, remote, dirty state).

    Args:
        project_path: Project root.

    Returns:
        A :class:`GitInfo` when ``project_path`` is inside a Git repository and
        the ``git`` CLI is available, otherwise ``None``.
    """
    if shutil.which("git") is None:
        return None

    commit = _git(project_path, "rev-parse", "HEAD")
    if commit is None:
        return None  # not a git repository (or no commits yet)

    branch = _git(project_path, "rev-parse", "--abbrev-ref", "HEAD")
    remote = _git(project_path, "config", "--get", "remote.origin.url")
    status = _git(project_path, "status", "--porcelain")
    return GitInfo(
        commit=commit,
        branch=branch or None,
        remote=remote or None,
        dirty=bool(status),
    )
