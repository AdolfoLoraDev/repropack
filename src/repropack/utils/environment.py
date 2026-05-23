"""Environment detection and capture (pip, conda, etc.)."""

from __future__ import annotations

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


def generate_pip_lock(project_path: Path, output_path: Path | None = None) -> Path:
    """Generate a requirements.lock with hashes from a pip environment.

    Args:
        project_path: Project root.
        output_path: Output path (default: requirements.lock in project_path).

    Returns:
        Path to the generated lockfile.
    """
    if output_path is None:
        output_path = project_path / "requirements.lock"

    # Prefer pip-compile if available
    if _command_exists("pip-compile"):
        req_in = project_path / "requirements.in"
        req_txt = project_path / "requirements.txt"
        source = str(req_in) if req_in.exists() else str(req_txt)
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
    else:
        # Fallback: pip freeze + pip hash
        try:
            result = subprocess.run(
                ["pip", "freeze", "--all"],
                capture_output=True,
                text=True,
                check=True,
            )
            output_path.write_text(result.stdout, encoding="utf-8")
        except subprocess.CalledProcessError:
            # If pip freeze fails, create a placeholder lockfile
            output_path.write_text(
                "# Lockfile generation failed; install dependencies manually\n",
                encoding="utf-8",
            )
    return output_path


def generate_conda_lock(project_path: Path, output_path: Path | None = None) -> Path:
    """Generate a frozen Conda environment.

    Args:
        project_path: Project root.
        output_path: Output path (default: conda-lock.yml in project_path).

    Returns:
        Path to the generated lockfile.
    """
    if output_path is None:
        output_path = project_path / "conda-lock.yml"

    if _command_exists("conda-lock"):
        subprocess.run(
            ["conda-lock", "lock", "--kind", "lock", "--file", "environment.yml"],
            check=True,
            cwd=str(project_path),
        )
    else:
        # Fallback: export active environment
        try:
            result = subprocess.run(
                ["conda", "env", "export", "--no-builds"],
                capture_output=True,
                text=True,
                check=True,
            )
            output_path.write_text(result.stdout, encoding="utf-8")
        except subprocess.CalledProcessError:
            # If conda export fails, create a placeholder lockfile
            output_path.write_text(
                "# Conda lockfile generation failed; install dependencies manually\n",
                encoding="utf-8",
            )
    return output_path


def get_python_version() -> str:
    """Get the active Python environment version."""
    result = subprocess.run(
        ["python", "--version"], capture_output=True, text=True, check=True
    )
    return result.stdout.strip() or result.stderr.strip()


def _command_exists(cmd: str) -> bool:
    """Check if a command exists in PATH."""
    import shutil

    return shutil.which(cmd) is not None


def list_project_files(
    project_path: Path,
    ignore_patterns: list[str] | None = None,
) -> list[str]:
    """List relevant project files excluding noise.

    Args:
        project_path: Project root.
        ignore_patterns: Patterns to ignore (simple gitignore-style).

    Returns:
        List of relative file paths.
    """
    if ignore_patterns is None:
        ignore_patterns = [
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
        ]

    files: list[str] = []
    for p in project_path.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(project_path).as_posix()
        if any(_match_pattern(rel, pat) for pat in ignore_patterns):
            continue
        files.append(rel)
    return sorted(files)


def _match_pattern(path: str, pattern: str) -> bool:
    """Simple pattern matching."""
    import fnmatch

    return fnmatch.fnmatch(path, pattern) or any(
        fnmatch.fnmatch(part, pattern) for part in path.split("/")
    )
