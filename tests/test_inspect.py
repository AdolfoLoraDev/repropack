"""Tests for the inspect command."""

from __future__ import annotations

from pathlib import Path

import pytest

from repropack.core.capture import capture_project
from repropack.core.inspect import inspect_package


def _make_rpk(tmp_path: Path) -> Path:
    """Build a minimal .rpk for inspection tests."""
    project = tmp_path / "inspect_project"
    project.mkdir()
    (project / "main.py").write_text("print(1)\n")
    (project / "requirements.txt").write_text("numpy\n")
    output = tmp_path / "inspect.rpk"
    capture_project(project, output)
    return output


class TestInspectPackage:
    """Tests for inspect_package."""

    def test_inspect_runs(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """inspect_package must run without errors."""
        rpk = _make_rpk(tmp_path)
        inspect_package(rpk)
        captured = capsys.readouterr()
        assert "inspect_project" in captured.out or captured.out == ""

    def test_inspect_missing_file(self, tmp_path: Path) -> None:
        """Must raise FileNotFoundError for missing .rpk."""
        with pytest.raises(FileNotFoundError):
            inspect_package(tmp_path / "ghost.rpk")

    def test_inspect_bad_archive(self, tmp_path: Path) -> None:
        """Must raise ValueError for archive without manifest."""
        bad = tmp_path / "bad.rpk"
        bad.write_text("not a zip")
        with pytest.raises(ValueError):
            inspect_package(bad)
