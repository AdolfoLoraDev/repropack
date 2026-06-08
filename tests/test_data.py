"""Tests for large-data handling and external data references."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from repropack.core.capture import capture_project
from repropack.core.data import (
    build_data_manifest,
    classify_source,
    is_external_reference,
    parse_data_refs,
)


@pytest.fixture(autouse=True)  # type: ignore[misc]
def _no_network_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid network/docker calls when resolving base-image digests."""
    monkeypatch.setattr(
        "repropack.core.capture.get_base_image_digest",
        lambda img: f"{img}@sha256:fakedigest",
    )


class TestSourceClassification:
    """Tests for external-reference classification."""

    @pytest.mark.parametrize(
        "source,expected",
        [
            ("https://doi.org/10.5281/zenodo.123", "doi"),
            ("doi:10.1000/xyz", "doi"),
            ("https://zenodo.org/record/123", "zenodo"),
            ("s3://bucket/key", "s3"),
            ("dvc://remote/path", "dvc"),
            ("https://example.com/data.csv", "url"),
            ("ftp://weird", "unknown"),
        ],
    )
    def test_classify(self, source: str, expected: str) -> None:
        assert classify_source(source) == expected

    def test_is_external_reference(self) -> None:
        assert is_external_reference("s3://b/k")
        assert is_external_reference("https://doi.org/x")
        assert not is_external_reference("relative/path.csv")


class TestParseDataRefs:
    """Tests for parsing path=source CLI strings."""

    def test_parse_ok(self) -> None:
        refs = parse_data_refs(["data/raw.csv=s3://bucket/raw.csv"])
        assert refs == {"data/raw.csv": "s3://bucket/raw.csv"}

    def test_parse_invalid(self) -> None:
        with pytest.raises(ValueError, match="path=source"):
            parse_data_refs(["no-equals-sign"])


class TestBuildDataManifest:
    """Tests for the data manifest builder."""

    def test_excluded_file_gets_hash_and_size(self, tmp_path: Path) -> None:
        (tmp_path / "big.bin").write_bytes(b"x" * 100)
        manifest = build_data_manifest(tmp_path, ["big.bin"], {})
        entry = manifest["datasets"][0]
        assert entry["path"] == "big.bin"
        assert entry["size_bytes"] == 100
        assert len(entry["sha256"]) == 64
        assert entry["source"] is None

    def test_reference_gets_source_type(self, tmp_path: Path) -> None:
        manifest = build_data_manifest(
            tmp_path, [], {"data/x.csv": "https://doi.org/10.5281/zenodo.9"}
        )
        entry = manifest["datasets"][0]
        assert entry["source_type"] == "doi"


class TestCaptureExcludesData:
    """End-to-end: large files are excluded from the .rpk."""

    def test_large_file_excluded(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        (project / "main.py").write_text("print(1)\n")
        (project / "big.dat").write_bytes(b"0" * (2 * 1024 * 1024))  # 2 MB

        output = tmp_path / "p.rpk"
        capture_project(project, output, exclude_data=True, data_threshold_mb=1.0)

        with zipfile.ZipFile(output, "r") as zf:
            names = zf.namelist()
            data_manifest = json.loads(zf.read("data_manifest.json"))

        assert "project/big.dat" not in names
        assert "project/main.py" in names
        paths = [d["path"] for d in data_manifest["datasets"]]
        assert "big.dat" in paths

    def test_data_ref_excluded_and_recorded(self, tmp_path: Path) -> None:
        project = tmp_path / "proj2"
        project.mkdir()
        (project / "main.py").write_text("print(1)\n")
        (project / "raw.csv").write_text("a,b\n1,2\n")

        output = tmp_path / "p2.rpk"
        capture_project(
            project,
            output,
            data_refs={"raw.csv": "s3://bucket/raw.csv"},
        )

        with zipfile.ZipFile(output, "r") as zf:
            names = zf.namelist()
            data_manifest = json.loads(zf.read("data_manifest.json"))

        assert "project/raw.csv" not in names
        entry = next(d for d in data_manifest["datasets"] if d["path"] == "raw.csv")
        assert entry["source"] == "s3://bucket/raw.csv"
        assert entry["source_type"] == "s3"
