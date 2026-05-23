"""Tests for the validate command."""

from __future__ import annotations

import zipfile
from pathlib import Path

from repropack.core.capture import capture_project
from repropack.core.validate import validate_package


def _make_rpk(tmp_path: Path) -> Path:
    """Build a minimal valid .rpk."""
    project = tmp_path / "val_project"
    project.mkdir()
    (project / "main.py").write_text("print(1)\n")
    (project / "requirements.txt").write_text("numpy\n")
    output = tmp_path / "val.rpk"
    capture_project(project, output)
    return output


class TestValidatePackage:
    """Tests for validate_package."""

    def test_valid_package(self, tmp_path: Path) -> None:
        """A freshly captured package must pass validation."""
        rpk = _make_rpk(tmp_path)
        result = validate_package(rpk)
        assert result.valid
        assert not result.errors

    def test_missing_file(self, tmp_path: Path) -> None:
        """Must report error when .rpk does not exist."""
        result = validate_package(tmp_path / "ghost.rpk")
        assert not result.valid
        assert any("not found" in e for e in result.errors)

    def test_bad_zip(self, tmp_path: Path) -> None:
        """Must report error for non-ZIP files."""
        bad = tmp_path / "bad.rpk"
        bad.write_text("not a zip")
        result = validate_package(bad)
        assert not result.valid
        assert any("valid ZIP" in e for e in result.errors)

    def test_missing_manifest(self, tmp_path: Path) -> None:
        """Must report error when repropack.yml is missing."""
        bad = tmp_path / "no_manifest.rpk"
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("Dockerfile", "FROM python\n")
        result = validate_package(bad)
        assert not result.valid
        assert any("repropack.yml" in e for e in result.errors)

    def test_corrupt_hash(self, tmp_path: Path) -> None:
        """Must report hash mismatch."""
        rpk = _make_rpk(tmp_path)
        # Tamper with a file inside the zip
        mod = tmp_path / "mod.rpk"
        with (
            zipfile.ZipFile(rpk, "r") as zin,
            zipfile.ZipFile(mod, "w") as zout,
        ):
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "project/main.py":
                    data = b"tampered"
                zout.writestr(item, data)
        result = validate_package(mod)
        assert not result.valid
        assert any("Hash mismatch" in e for e in result.errors)

    def test_editable_warning(self, tmp_path: Path) -> None:
        """Must warn when lockfile contains editable installs."""
        rpk = _make_rpk(tmp_path)
        # Repack with editable lockfile
        mod = tmp_path / "editable.rpk"
        with (
            zipfile.ZipFile(rpk, "r") as zin,
            zipfile.ZipFile(mod, "w") as zout,
        ):
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename.endswith("requirements.lock"):
                    data = b"-e .\nnumpy\n"
                zout.writestr(item, data)
        result = validate_package(mod)
        assert any("editable" in w for w in result.warnings)
