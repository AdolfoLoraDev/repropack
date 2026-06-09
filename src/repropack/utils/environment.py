"""Environment detection and capture (pip, conda, etc.)."""

from __future__ import annotations

import shutil
import subprocess
from enum import Enum
from pathlib import Path


class EnvType(str, Enum):
    """Detected environment type."""

    PIP = "pip"
    CONDA = "conda"
    POETRY = "poetry"
    UNKNOWN = "unknown"


def detect_env_type(project_path: Path) -> EnvType:
    """Detect the dependency manager used in the project.

    Args:
        project_path: Project root.

    Returns:
        Detected environment type.
    """
    if (project_path / "poetry.lock").exists() or (
        project_path / "pyproject.toml"
    ).exists():
        return EnvType.POETRY
    if (project_path / "environment.yml").exists() or (
        project_path / "conda-lock.yml"
    ).exists():
        return EnvType.CONDA
    if (project_path / "requirements.txt").exists() or (
        project_path / "setup.py"
    ).exists():
        return EnvType.PIP
    return EnvType.UNKNOWN


def generate_pip_lock(
    project_path: Path,
    output_path: Path | None = None,
) -> Path:
    """Generate a requirements.lock with hashes from a pip environment.

    Priority:
    1. ``pip-compile --generate-hashes`` (if available).
    2. ``pip freeze --all`` (fallback with warning placeholder).

    Args:
        project_path: Project root.
        output_path: Output path (default: requirements.lock in project_path).

    Returns:
        Path to the generated lockfile.
    """
    if output_path is None:
        output_path = project_path / "requirements.lock"

    # Prefer pip-compile with hashes
    if _command_exists("pip-compile"):
        req_in = project_path / "requirements.in"
        req_txt = project_path / "requirements.txt"
        source = str(req_in) if req_in.exists() else str(req_txt)
        try:
            subprocess.run(
                [
                    "pip-compile",
                    "--generate-hashes",
                    "--output-file",
                    str(output_path),
                    source,
                ],
                check=True,
                cwd=str(project_path),
            )
            return output_path
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

    # Fallback: pip freeze
    try:
        result = subprocess.run(
            ["pip", "freeze", "--all"],
            capture_output=True,
            text=True,
            check=True,
        )
        content = (
            "# WARNING: This lockfile was generated with 'pip freeze'\n"
            "#          It does NOT contain cryptographic hashes.\n"
            "#          Install 'pip-tools' and use 'pip-compile "
            "--generate-hashes' for strict reproducibility.\n\n"
            f"{result.stdout}"
        )
        output_path.write_text(content, encoding="utf-8")
    except subprocess.CalledProcessError:
        output_path.write_text(
            "# Lockfile generation failed; " "install dependencies manually\n",
            encoding="utf-8",
        )
    return output_path


def generate_conda_lock(
    project_path: Path,
    output_path: Path | None = None,
) -> Path:
    """Generate a frozen Conda environment.

    Priority:
    1. ``conda-lock lock`` (if available).
    2. ``conda env export --no-builds`` (fallback).

    Args:
        project_path: Project root.
        output_path: Output path (default: conda-lock.yml in project_path).

    Returns:
        Path to the generated lockfile.
    """
    if output_path is None:
        output_path = project_path / "conda-lock.yml"

    if _command_exists("conda-lock"):
        try:
            subprocess.run(
                [
                    "conda-lock",
                    "lock",
                    "--kind",
                    "lock",
                    "--file",
                    "environment.yml",
                ],
                check=True,
                cwd=str(project_path),
            )
            return output_path
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

    # Fallback: export active environment
    try:
        result = subprocess.run(
            ["conda", "env", "export", "--no-builds"],
            capture_output=True,
            text=True,
            check=True,
        )
        output_path.write_text(result.stdout, encoding="utf-8")
    except (subprocess.CalledProcessError, FileNotFoundError):
        output_path.write_text(
            "# Conda lockfile generation failed; " "install dependencies manually\n",
            encoding="utf-8",
        )
    return output_path


def detect_r_renv(project_path: Path) -> Path | None:
    """Locate an R ``renv.lock`` lockfile in the project.

    Detects either a top-level ``renv.lock`` or one inside an ``renv/``
    directory (the layout produced by ``renv::init()``).

    Args:
        project_path: Project root.

    Returns:
        Path to ``renv.lock`` if found, otherwise ``None``.
    """
    candidates = [project_path / "renv.lock", project_path / "renv" / "renv.lock"]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def detect_julia_project(project_path: Path) -> Path | None:
    """Locate a Julia ``Project.toml`` in the project.

    Args:
        project_path: Project root.

    Returns:
        Path to ``Project.toml`` if found, otherwise ``None``.
    """
    project_toml = project_path / "Project.toml"
    if project_toml.exists():
        return project_toml
    return None


def get_python_version() -> str:
    """Get the active Python environment version."""
    result = subprocess.run(
        ["python", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip() or result.stderr.strip()


def has_editable_installs(lockfile_path: Path) -> bool:
    """Check whether a lockfile contains editable installs.

    Detects ``-e .`` or ``--editable`` lines.

    Args:
        lockfile_path: Path to the lockfile.

    Returns:
        ``True`` if editable installs were found.
    """
    if not lockfile_path.exists():
        return False
    text = lockfile_path.read_text(encoding="utf-8")
    return "-e " in text or "--editable" in text


def lockfile_has_hashes(lockfile_path: Path) -> bool:
    """Check whether a pip lockfile carries cryptographic hashes.

    ``pip install --require-hashes`` only works when every requirement has a
    ``--hash=`` entry (as produced by ``pip-compile --generate-hashes``). A
    plain ``requirements.txt`` or a ``pip freeze`` fallback has none.

    Args:
        lockfile_path: Path to the lockfile.

    Returns:
        ``True`` if at least one ``--hash=`` entry is present.
    """
    if not lockfile_path.exists():
        return False
    return "--hash=" in lockfile_path.read_text(encoding="utf-8")


def _command_exists(cmd: str) -> bool:
    """Check if a command exists in PATH."""
    return shutil.which(cmd) is not None


DEFAULT_IGNORE_PATTERNS = [
    ".git",
    "__pycache__",
    "*.pyc",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "*.egg-info",
    ".venv",
    "venv",
    ".env",
    "node_modules",
    ".idea",
    ".vscode",
    # Language-specific noise
    ".Rhistory",
    ".RData",
    ".ipynb_checkpoints",
]

# Ignore files honoured at the project root (first found wins for ordering).
_IGNORE_FILENAMES = (".repropackignore", ".gitignore")


def _load_ignore_files(project_path: Path) -> tuple[list[str], list[str]]:
    """Parse ``.repropackignore`` / ``.gitignore`` into (ignore, negate) lists.

    Lines are gitignore-style: ``#`` comments and blanks are skipped, a leading
    ``!`` marks a re-include (negation), and a trailing ``/`` (directory) is
    stripped to a plain pattern.

    Args:
        project_path: Project root.

    Returns:
        A tuple ``(ignore_patterns, negate_patterns)``.
    """
    ignore: list[str] = []
    negate: list[str] = []
    for fname in _IGNORE_FILENAMES:
        path = project_path / fname
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            negated = line.startswith("!")
            if negated:
                line = line[1:].strip()
            line = line.rstrip("/")
            if not line:
                continue
            (negate if negated else ignore).append(line)
    return ignore, negate


def list_project_files(
    project_path: Path,
    ignore_patterns: list[str] | None = None,
) -> list[str]:
    """List relevant project files excluding noise.

    On top of the supplied (or default) ignore patterns, any
    ``.repropackignore`` or ``.gitignore`` at the project root is honoured,
    including ``!`` negations that re-include otherwise-ignored files.

    Args:
        project_path: Project root.
        ignore_patterns: Patterns to ignore (defaults to
            :data:`DEFAULT_IGNORE_PATTERNS`).

    Returns:
        Sorted list of relative file paths.
    """
    if ignore_patterns is None:
        ignore_patterns = DEFAULT_IGNORE_PATTERNS

    extra_ignore, negate = _load_ignore_files(project_path)
    effective_ignore = [*ignore_patterns, *extra_ignore]

    files: list[str] = []
    for p in project_path.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(project_path).as_posix()
        ignored = any(_match_pattern(rel, pat) for pat in effective_ignore)
        if ignored and not any(_match_pattern(rel, pat) for pat in negate):
            continue
        files.append(rel)
    return sorted(files)


def _match_pattern(path: str, pattern: str) -> bool:
    """Simple pattern matching."""
    import fnmatch

    return fnmatch.fnmatch(path, pattern) or any(
        fnmatch.fnmatch(part, pattern) for part in path.split("/")
    )
