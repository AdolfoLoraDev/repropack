"""Tests for data fetching, OSF publishing and ecosystem exporters."""

from __future__ import annotations

import sys
import types
import zipfile
from pathlib import Path
from typing import Any

import pytest

from repropack.core import data as data_mod
from repropack.core.data import DataFetchError, fetch_datasets
from repropack.core.manifest import (
    EnvironmentSpec,
    Metadata,
    ReproPackManifest,
    Step,
    StepType,
)

# =====================================================================
# Data fetching
# =====================================================================


class TestResolveUrl:
    def test_http_passthrough(self) -> None:
        assert data_mod._resolve_url("https://x/y") == "https://x/y"

    def test_doi_prefix(self) -> None:
        assert data_mod._resolve_url("doi:10.5/x") == "https://doi.org/10.5/x"

    def test_doi_url_passthrough(self) -> None:
        assert data_mod._resolve_url("https://doi.org/10.5/x") == (
            "https://doi.org/10.5/x"
        )

    def test_doi_org_without_scheme(self) -> None:
        assert data_mod._resolve_url("doi.org/10.5/x") == "doi.org/10.5/x"

    def test_unresolvable(self) -> None:
        with pytest.raises(DataFetchError, match="Cannot resolve"):
            data_mod._resolve_url("ftp://nope")


class TestFetchDatasets:
    def _manifest(self, sha: str | None, source: str, stype: str) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "path": "data/x.csv",
            "source": source,
            "source_type": stype,
        }
        if sha is not None:
            entry["sha256"] = sha
        return {"datasets": [entry]}

    def test_http_fetch_with_verify(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import hashlib

        content = b"a,b\n1,2\n"
        digest = hashlib.sha256(content).hexdigest()

        def _fake_dl(url: str, target: Path, timeout: int = 60) -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)

        monkeypatch.setattr(data_mod, "_download_http", _fake_dl)
        manifest = self._manifest(digest, "https://x/data.csv", "url")
        fetched = fetch_datasets(manifest, tmp_path)
        assert fetched == ["data/x.csv"]
        assert (tmp_path / "data/x.csv").read_bytes() == content

    def test_checksum_mismatch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _dl(url: str, target: Path, timeout: int = 60) -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"wrong")

        monkeypatch.setattr(data_mod, "_download_http", _dl)
        manifest = self._manifest("0" * 64, "https://x/d", "url")
        with pytest.raises(DataFetchError, match="Checksum mismatch"):
            fetch_datasets(manifest, tmp_path)

    def test_skip_entry_without_source(self, tmp_path: Path) -> None:
        manifest = {"datasets": [{"path": "x", "source": None}]}
        assert fetch_datasets(manifest, tmp_path) == []

    def test_dvc_unsupported(self, tmp_path: Path) -> None:
        manifest = self._manifest(None, "dvc://remote/x", "dvc")
        with pytest.raises(DataFetchError, match="DVC datasets"):
            fetch_datasets(manifest, tmp_path)

    def test_unknown_source_type(self, tmp_path: Path) -> None:
        manifest = self._manifest(None, "weird://x", "unknown")
        with pytest.raises(DataFetchError, match="Unsupported data source"):
            fetch_datasets(manifest, tmp_path)

    def test_verify_disabled_skips_hash(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _dl(url: str, target: Path, timeout: int = 60) -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"any")

        monkeypatch.setattr(data_mod, "_download_http", _dl)
        manifest = self._manifest("0" * 64, "https://x/d", "url")
        assert fetch_datasets(manifest, tmp_path, verify=False) == ["data/x.csv"]

    def test_s3_via_fetch_datasets(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _dl(source: str, target: Path) -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"s3")

        monkeypatch.setattr(data_mod, "_download_s3", _dl)
        manifest = self._manifest(None, "s3://bucket/key", "s3")
        assert fetch_datasets(manifest, tmp_path, verify=False) == ["data/x.csv"]

    def test_download_http_real(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Resp:
            def __enter__(self) -> _Resp:
                return self

            def __exit__(self, *a: Any) -> None:
                return None

            def read(self) -> bytes:
                return b"downloaded"

        import urllib.request

        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
        target = tmp_path / "sub/file.bin"
        data_mod._download_http("https://x/file", target)
        assert target.read_bytes() == b"downloaded"


class TestS3Fetch:
    def test_s3_requires_boto3(self, tmp_path: Path) -> None:
        # boto3 is not installed in the test environment.
        with pytest.raises(DataFetchError, match="requires 'boto3'"):
            data_mod._download_s3("s3://bucket/key", tmp_path / "x")

    def test_s3_success_and_malformed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        downloaded: dict[str, Any] = {}

        class _Client:
            def download_file(self, bucket: str, key: str, dest: str) -> None:
                downloaded["args"] = (bucket, key, dest)
                Path(dest).write_bytes(b"s3-bytes")

        fake_boto3 = types.ModuleType("boto3")
        fake_boto3.client = lambda service: _Client()  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

        data_mod._download_s3("s3://bucket/path/key.csv", tmp_path / "out.csv")
        assert downloaded["args"][0] == "bucket"
        assert (tmp_path / "out.csv").read_bytes() == b"s3-bytes"

        with pytest.raises(DataFetchError, match="Malformed S3 URI"):
            data_mod._download_s3("s3://bucketonly", tmp_path / "x")


# =====================================================================
# OSF publishing
# =====================================================================


def _rpk_with_manifest(tmp_path: Path) -> Path:
    manifest = ReproPackManifest(
        metadata=Metadata(name="demo", description="d"),
        environment=EnvironmentSpec(base_image="python:3.11-slim@sha256:a"),
    )
    rpk = tmp_path / "p.rpk"
    with zipfile.ZipFile(rpk, "w") as zf:
        zf.writestr("repropack.yml", manifest.to_yaml())
    return rpk


class _OsfResp:
    def __enter__(self) -> _OsfResp:
        return self

    def __exit__(self, *a: Any) -> None:
        return None

    def read(self) -> bytes:
        return b'{"data": {"links": {"html": "https://osf.io/abcde/"}}}'


class TestOsf:
    def test_osf_create_node(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import urllib.request

        from repropack.core import publish

        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _OsfResp())
        url = publish._osf_create_node(
            _rpk_with_manifest(tmp_path), "tok", sandbox=True
        )
        assert url == "https://osf.io/abcde/"

    def test_publish_osf(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from repropack.core import publish

        monkeypatch.setattr(
            publish, "_osf_create_node", lambda *a, **k: "https://osf/x"
        )
        result = publish.publish_package(
            _rpk_with_manifest(tmp_path), to="osf", token="tok"
        )
        assert result["url"] == "https://osf/x"

    def test_osf_network_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import urllib.request

        from repropack.core import publish

        def _boom(*a: Any, **k: Any) -> None:
            raise OSError("offline")

        monkeypatch.setattr(urllib.request, "urlopen", _boom)
        with pytest.raises(RuntimeError, match="OSF deposition failed"):
            publish._osf_create_node(_rpk_with_manifest(tmp_path), "tok")


# =====================================================================
# Ecosystem exporters
# =====================================================================


def _full_rpk(tmp_path: Path) -> Path:
    manifest = ReproPackManifest(
        metadata=Metadata(name="exp"),
        environment=EnvironmentSpec(base_image="python:3.11-slim@sha256:a"),
        steps=[
            Step(
                id="train",
                type=StepType.AUTOMATIC,
                command="python train.py",
                inputs=["data/"],
                outputs=["model.pkl"],
            )
        ],
    )
    rpk = tmp_path / "exp.rpk"
    with zipfile.ZipFile(rpk, "w") as zf:
        zf.writestr("repropack.yml", manifest.to_yaml())
        zf.writestr("Dockerfile", "FROM python:3.11-slim\n")
        zf.writestr("provenance.json", "{}")
        zf.writestr("project/train.py", "print('train')\n")
        zf.writestr("project/data/raw.csv", "a,b\n")
    return rpk


class TestEcosystemExporters:
    def test_repo2docker(self, tmp_path: Path) -> None:
        from repropack.core.plugins import get_exporter

        rpk = _full_rpk(tmp_path)
        out = tmp_path / "context"
        get_exporter("repo2docker")(rpk, out)
        assert (out / "train.py").exists()
        assert (out / "data/raw.csv").exists()
        assert (out / "Dockerfile").exists()

    def test_reprozip(self, tmp_path: Path) -> None:
        import yaml

        from repropack.core.plugins import get_exporter

        rpk = _full_rpk(tmp_path)
        out = tmp_path / "reprozip.yml"
        get_exporter("reprozip")(rpk, out)
        config = yaml.safe_load(out.read_text())
        assert config["experiment"] == "exp"
        assert config["runs"][0]["id"] == "train"
        assert config["runs"][0]["outputs"] == ["model.pkl"]

    def test_exporters_listed(self) -> None:
        from repropack.core.plugins import list_exporters

        names = list_exporters()
        assert "repo2docker" in names
        assert "reprozip" in names


# =====================================================================
# Reproducer fetch-data integration
# =====================================================================


class TestReproducerFetch:
    def test_fetch_external_data_with_manifest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import json

        from repropack.core import data as dm
        from repropack.core.run import Reproducer

        manifest = ReproPackManifest(
            metadata=Metadata(name="m"),
            environment=EnvironmentSpec(base_image="python:3.11-slim@sha256:a"),
            steps=[Step(id="s", type=StepType.AUTOMATIC, command="true")],
        )
        rpk = tmp_path / "p.rpk"
        with zipfile.ZipFile(rpk, "w") as zf:
            zf.writestr("repropack.yml", manifest.to_yaml())
            zf.writestr("Dockerfile", "FROM python\n")
            zf.writestr("provenance.json", "{}")
            zf.writestr("project/main.py", "x\n")
            zf.writestr(
                "data_manifest.json",
                json.dumps(
                    {
                        "datasets": [
                            {
                                "path": "data/x.csv",
                                "source": "https://x/d",
                                "source_type": "url",
                            }
                        ]
                    }
                ),
            )

        captured: dict[str, Any] = {}

        def _fake_fetch(man: dict[str, Any], dest: Path, **k: Any) -> list[str]:
            captured["dest"] = dest
            return ["data/x.csv"]

        monkeypatch.setattr(dm, "fetch_datasets", _fake_fetch)
        rep = Reproducer(rpk, lite=True, fetch_data=True)
        rep.run()
        assert captured["dest"].name == "project"
        assert any("Fetched 1" in line for line in rep._report_lines)
