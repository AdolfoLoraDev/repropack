"""Tests for CITATION.cff generation and the publish command."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from repropack.core.manifest import (
    EnvironmentSpec,
    Metadata,
    ReproPackManifest,
)
from repropack.core.publish import (
    generate_citation_cff,
    parse_author,
    publish_package,
    write_citation,
)


def _manifest(authors: list[str]) -> ReproPackManifest:
    return ReproPackManifest(
        metadata=Metadata(
            name="demo",
            created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            authors=authors,
            description="A demo experiment",
        ),
        environment=EnvironmentSpec(base_image="python:3.11-slim@sha256:abc"),
    )


class TestParseAuthor:
    """Tests for author-string parsing."""

    def test_name_only(self) -> None:
        assert parse_author("Ana Garcia") == {"name": "Ana Garcia"}

    def test_name_email(self) -> None:
        out = parse_author("Ana Garcia <ana@x.org>")
        assert out == {"name": "Ana Garcia", "email": "ana@x.org"}

    def test_name_email_orcid(self) -> None:
        out = parse_author("Ana Garcia <ana@x.org> (0000-0002-1825-0097)")
        assert out["orcid"] == "https://orcid.org/0000-0002-1825-0097"


class TestCitationCff:
    """Tests for CFF generation."""

    def test_cff_structure(self) -> None:
        cff = generate_citation_cff(_manifest(["Ana Garcia <ana@x.org>"]))
        data = yaml.safe_load(cff)
        assert data["cff-version"] == "1.2.0"
        assert data["title"] == "demo"
        assert data["date-released"] == "2026-01-02"
        assert data["authors"][0]["name"] == "Ana Garcia"
        assert data["abstract"] == "A demo experiment"

    def test_cff_anonymous_when_no_authors(self) -> None:
        data = yaml.safe_load(generate_citation_cff(_manifest([])))
        assert data["authors"][0]["name"] == "Anonymous"


class TestPublish:
    """Tests for write_citation and publish_package."""

    def _make_rpk(self, tmp_path: Path) -> Path:
        from repropack.core.capture import capture_project

        project = tmp_path / "proj"
        project.mkdir()
        (project / "main.py").write_text("print(1)\n")
        output = tmp_path / "p.rpk"
        # Avoid network digest resolution
        import repropack.core.capture as cap

        orig = cap.get_base_image_digest
        cap.get_base_image_digest = lambda img: f"{img}@sha256:fake"  # type: ignore[assignment]
        try:
            capture_project(project, output)
        finally:
            cap.get_base_image_digest = orig  # type: ignore[assignment]
        return output

    def test_write_citation(self, tmp_path: Path) -> None:
        rpk = self._make_rpk(tmp_path)
        cff_path = write_citation(rpk)
        assert cff_path.exists()
        assert cff_path.name == "CITATION.cff"

    def test_publish_citation_target(self, tmp_path: Path) -> None:
        rpk = self._make_rpk(tmp_path)
        result = publish_package(rpk, to="citation")
        assert Path(result["citation"]).exists()
        assert "url" not in result

    def test_publish_zenodo_requires_token(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("REPROPACK_ZENODO_TOKEN", raising=False)
        rpk = self._make_rpk(tmp_path)
        with pytest.raises(RuntimeError, match="requires an API token"):
            publish_package(rpk, to="zenodo")

    def test_publish_unknown_target(self, tmp_path: Path) -> None:
        rpk = self._make_rpk(tmp_path)
        with pytest.raises(ValueError, match="Unknown publish target"):
            publish_package(rpk, to="bogus")
