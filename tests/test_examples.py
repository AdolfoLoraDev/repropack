"""Smoke tests that the shipped examples capture cleanly."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from repropack.core.capture import capture_project
from repropack.core.manifest import ReproPackManifest
from repropack.core.validate import validate_package

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture(autouse=True)  # type: ignore[misc]
def _no_network_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "repropack.core.capture.get_base_image_digest",
        lambda img: f"{img}@sha256:fakedigest",
    )


@pytest.mark.parametrize("name", ["python-ml", "r-analysis", "julia-sim"])
def test_example_captures_and_validates(name: str, tmp_path: Path) -> None:
    example = EXAMPLES_DIR / name
    assert example.is_dir(), f"missing example: {example}"

    output = tmp_path / f"{name}.rpk"
    capture_project(example, output)

    with zipfile.ZipFile(output, "r") as zf:
        manifest = ReproPackManifest.from_yaml(zf.read("repropack.yml").decode("utf-8"))
    assert manifest.steps, "example produced no steps"

    result = validate_package(output)
    assert result.valid, result.errors


def test_python_ml_infers_prepare_and_train(tmp_path: Path) -> None:
    output = tmp_path / "ml.rpk"
    capture_project(EXAMPLES_DIR / "python-ml", output)
    with zipfile.ZipFile(output, "r") as zf:
        manifest = ReproPackManifest.from_yaml(zf.read("repropack.yml").decode("utf-8"))
    ids = {s.id for s in manifest.steps}
    assert {"prepare", "train"} <= ids
