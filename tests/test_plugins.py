"""Tests for the exporter plugin API."""

from __future__ import annotations

from pathlib import Path

import pytest

from repropack.core.capture import capture_project
from repropack.core.plugins import (
    get_exporter,
    list_exporters,
    register_exporter,
)


@pytest.fixture(autouse=True)  # type: ignore[misc]
def _no_network_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "repropack.core.capture.get_base_image_digest",
        lambda img: f"{img}@sha256:fakedigest",
    )


def _make_rpk(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "main.py").write_text("print(1)\n")
    output = tmp_path / "p.rpk"
    capture_project(project, output)
    return output


class TestRegistry:
    def test_builtins_present(self) -> None:
        names = list_exporters()
        assert "citation" in names
        assert "provxml" in names
        assert "mermaid" in names

    def test_register_custom_exporter(self, tmp_path: Path) -> None:
        @register_exporter("dummy-test")
        def _dummy(rpk: Path, output: Path) -> Path:
            output.write_text("dummy", encoding="utf-8")
            return output

        assert "dummy-test" in list_exporters()
        out = get_exporter("dummy-test")(tmp_path / "x.rpk", tmp_path / "out.txt")
        assert out.read_text() == "dummy"

    def test_unknown_exporter_raises(self) -> None:
        with pytest.raises(KeyError, match="Unknown exporter"):
            get_exporter("does-not-exist")


class TestBuiltinExporters:
    def test_citation_exporter(self, tmp_path: Path) -> None:
        rpk = _make_rpk(tmp_path)
        out = get_exporter("citation")(rpk, tmp_path / "CITATION.cff")
        assert out.exists()
        assert "cff-version" in out.read_text()

    def test_mermaid_exporter(self, tmp_path: Path) -> None:
        rpk = _make_rpk(tmp_path)
        out = get_exporter("mermaid")(rpk, tmp_path / "graph.mmd")
        assert "graph TD" in out.read_text()

    def test_provxml_exporter(self, tmp_path: Path) -> None:
        rpk = _make_rpk(tmp_path)
        out = get_exporter("provxml")(rpk, tmp_path / "graph.xml")
        assert "prov:" in out.read_text()
