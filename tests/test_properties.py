"""Property-based tests (Hypothesis) for round-trips and invariants."""

from __future__ import annotations

import zipfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from repropack.core.data import classify_source, parse_data_refs
from repropack.core.diff import diff_packages
from repropack.core.manifest import (
    EnvironmentSpec,
    Metadata,
    ReproPackManifest,
    Step,
    StepType,
)

# Identifiers / paths kept simple but non-trivial.
_ids = st.text(
    alphabet=st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters="_-"
    ),
    min_size=1,
    max_size=12,
)
# Printable text only: YAML does not faithfully round-trip C0/C1 control
# characters, which is a YAML limitation rather than a manifest concern.
_text = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd", "Po", "Pd")),
    min_size=1,
    max_size=40,
)


@st.composite
def _steps(draw: st.DrawFn) -> list[Step]:
    n = draw(st.integers(min_value=0, max_value=4))
    steps: list[Step] = []
    seen: set[str] = set()
    for i in range(n):
        sid = f"{draw(_ids)}_{i}"
        if sid in seen:
            continue
        seen.add(sid)
        steps.append(
            Step(
                id=sid,
                type=StepType.AUTOMATIC,
                command=draw(_text),
                inputs=draw(st.lists(_ids, max_size=3)),
                outputs=draw(st.lists(_ids, max_size=3)),
            )
        )
    return steps


@st.composite
def _manifests(draw: st.DrawFn) -> ReproPackManifest:
    return ReproPackManifest(
        metadata=Metadata(
            name=draw(_ids),
            authors=draw(st.lists(_text, max_size=3)),
            description=draw(st.one_of(st.none(), _text)),
        ),
        environment=EnvironmentSpec(
            base_image="python:3.11-slim@sha256:" + "a" * 64,
            system_packages=draw(st.lists(_ids, max_size=3)),
        ),
        steps=draw(_steps()),
    )


class TestManifestRoundTrip:
    @settings(max_examples=60)
    @given(_manifests())
    def test_yaml_roundtrip(self, manifest: ReproPackManifest) -> None:
        """A manifest survives a YAML serialise/parse round-trip unchanged."""
        restored = ReproPackManifest.from_yaml(manifest.to_yaml())
        assert restored == manifest


class TestDiffIdentity:
    @settings(max_examples=30, deadline=None)
    @given(_manifests())
    def test_diff_of_self_is_identical(self, manifest: ReproPackManifest) -> None:
        """diff(a, a) is always identical regardless of manifest content."""
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            rpk = Path(d) / "a.rpk"
            with zipfile.ZipFile(rpk, "w") as zf:
                zf.writestr("repropack.yml", manifest.to_yaml())
            assert diff_packages(rpk, rpk).identical


class TestDataRefRoundTrip:
    @settings(max_examples=50)
    @given(
        st.dictionaries(
            keys=st.text(
                alphabet="abcdefABCDEF0123456789/_.-", min_size=1, max_size=20
            ),
            values=st.text(
                alphabet="abcdefABCDEF0123456789/_.:-", min_size=1, max_size=30
            ),
            max_size=5,
        )
    )
    def test_parse_data_refs_roundtrip(self, refs: dict[str, str]) -> None:
        """Serialising refs to 'path=source' and parsing back is stable."""
        items = [f"{path}={source}" for path, source in refs.items()]
        parsed = parse_data_refs(items)
        assert parsed == refs


class TestClassifySourceTotal:
    @settings(max_examples=80)
    @given(st.text(max_size=60))
    def test_classify_always_returns_known_label(self, source: str) -> None:
        """classify_source is total: it never raises and returns a known tag."""
        assert classify_source(source) in {
            "doi",
            "zenodo",
            "s3",
            "dvc",
            "url",
            "unknown",
        }
