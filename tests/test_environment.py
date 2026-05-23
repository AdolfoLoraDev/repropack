"""Tests for environment detection and lockfile generation."""

from __future__ import annotations

from pathlib import Path

from repropack.utils.environment import (
    EnvType,
    detect_env_type,
    generate_conda_lock,
    generate_pip_lock,
    has_editable_installs,
    list_project_files,
)


class TestDetectEnvType:
    """Tests for environment detection."""

    def test_detect_pip(self, tmp_path: Path) -> None:
        """Must detect pip environment by requirements.txt."""
        (tmp_path / "requirements.txt").write_text("numpy\n")
        assert detect_env_type(tmp_path) == EnvType.PIP

    def test_detect_conda(self, tmp_path: Path) -> None:
        """Must detect conda environment by environment.yml."""
        (tmp_path / "environment.yml").write_text("name: test\n")
        assert detect_env_type(tmp_path) == EnvType.CONDA

    def test_detect_poetry(self, tmp_path: Path) -> None:
        """Must detect poetry environment by pyproject.toml."""
        (tmp_path / "pyproject.toml").write_text("[tool.poetry]\n")
        assert detect_env_type(tmp_path) == EnvType.POETRY

    def test_detect_unknown(self, tmp_path: Path) -> None:
        """Must return unknown when no recognized files exist."""
        assert detect_env_type(tmp_path) == EnvType.UNKNOWN


class TestGeneratePipLock:
    """Tests for pip lockfile generation."""

    def test_generates_lockfile(self, tmp_path: Path) -> None:
        """Must create requirements.lock in project path."""
        (tmp_path / "requirements.txt").write_text("numpy\n")
        result = generate_pip_lock(tmp_path)
        assert result.exists()
        assert result.name == "requirements.lock"

    def test_fallback_warning_in_content(self, tmp_path: Path) -> None:
        """Fallback lockfile must contain warning header."""
        result = generate_pip_lock(tmp_path)
        text = result.read_text(encoding="utf-8")
        assert "WARNING" in text or "Lockfile generation failed" in text


class TestGenerateCondaLock:
    """Tests for conda lockfile generation."""

    def test_generates_lockfile(self, tmp_path: Path) -> None:
        """Must create conda-lock.yml in project path."""
        (tmp_path / "environment.yml").write_text("name: test\n")
        result = generate_conda_lock(tmp_path)
        assert result.exists()
        assert result.name == "conda-lock.yml"


class TestHasEditableInstalls:
    """Tests for editable install detection."""

    def test_detects_editable(self, tmp_path: Path) -> None:
        """Must detect -e . in lockfile."""
        f = tmp_path / "req.lock"
        f.write_text("-e .\nnumpy\n")
        assert has_editable_installs(f) is True

    def test_no_false_positive(self, tmp_path: Path) -> None:
        """Must not flag normal packages."""
        f = tmp_path / "req.lock"
        f.write_text("numpy==1.26.0\n")
        assert has_editable_installs(f) is False

    def test_missing_file(self, tmp_path: Path) -> None:
        """Must return False for missing file."""
        assert has_editable_installs(tmp_path / "missing.txt") is False


class TestListProjectFiles:
    """Tests for project file listing."""

    def test_ignores_pycache(self, tmp_path: Path) -> None:
        """Must not include __pycache__ or .pyc files."""
        (tmp_path / "main.py").write_text("print(1)\n")
        pycache = tmp_path / "__pycache__"
        pycache.mkdir()
        (pycache / "main.cpython-311.pyc").write_text("fake\n")
        files = list_project_files(tmp_path)
        assert "main.py" in files
        assert not any("__pycache__" in f for f in files)

    def test_ignores_git(self, tmp_path: Path) -> None:
        """Must not include the .git directory."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text("[core]\n")
        (tmp_path / "script.py").write_text("pass\n")
        files = list_project_files(tmp_path)
        assert "script.py" in files
        assert not any(".git" in f for f in files)
