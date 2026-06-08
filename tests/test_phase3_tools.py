"""Tests for diff, profiling and lite-environment checks."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from repropack.core.capture import capture_project
from repropack.core.diff import diff_packages
from repropack.core.manifest import ReproPackManifest, Step, StepType
from repropack.core.run import Reproducer


@pytest.fixture(autouse=True)  # type: ignore[misc]
def _no_network_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "repropack.core.capture.get_base_image_digest",
        lambda img: f"{img}@sha256:fakedigest",
    )


def _repack(rpk: Path, work: Path, manifest: ReproPackManifest, out: Path) -> Path:
    with zipfile.ZipFile(rpk, "r") as zf:
        zf.extractall(work)
    manifest.to_file(work / "repropack.yml")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for fpath in work.rglob("*"):
            if fpath.is_file():
                zf.write(fpath, fpath.relative_to(work).as_posix())
    return out


class TestDiff:
    """Tests for diff_packages."""

    def test_identical_packages(self, tmp_path: Path) -> None:
        project = tmp_path / "p"
        project.mkdir()
        (project / "main.py").write_text("print(1)\n")
        a = tmp_path / "a.rpk"
        capture_project(project, a)
        # Copy bytes to a second package.
        b = tmp_path / "b.rpk"
        b.write_bytes(a.read_bytes())

        result = diff_packages(a, b)
        assert result.identical

    def test_step_and_file_changes(self, tmp_path: Path) -> None:
        project = tmp_path / "p2"
        project.mkdir()
        (project / "main.py").write_text("print(1)\n")
        (project / "train.py").write_text("print('a')\n")
        a = tmp_path / "a2.rpk"
        capture_project(project, a)

        # Build b with a changed step command and a removed file hash.
        man = ReproPackManifest.from_yaml(
            zipfile.ZipFile(a).read("repropack.yml").decode()
        )
        man.steps = [
            Step(id="train", type=StepType.AUTOMATIC, command="python train.py --new")
        ]
        man.file_hashes = dict(list(man.file_hashes.items())[:1])
        b = _repack(a, tmp_path / "wb", man, tmp_path / "b2.rpk")

        result = diff_packages(a, b)
        assert not result.identical
        assert "train" in result.steps_changed or "train" in result.steps_added


class TestProfile:
    """Tests for --profile."""

    def test_profile_written(self, tmp_path: Path) -> None:
        project = tmp_path / "p"
        project.mkdir()
        (project / "requirements.txt").write_text("# none\n")
        a = tmp_path / "a.rpk"
        capture_project(project, a)

        # Replace steps with a trivial shell step.
        work = tmp_path / "w"
        man = ReproPackManifest.from_yaml(
            zipfile.ZipFile(a).read("repropack.yml").decode()
        )
        man.steps = [Step(id="noop", type=StepType.AUTOMATIC, command="true")]
        rpk = _repack(a, work, man, tmp_path / "prof.rpk")

        Reproducer(rpk, lite=True, profile=True).run()
        profile_file = rpk.parent / "reproduction-profile.json"
        assert profile_file.exists()
        data = json.loads(profile_file.read_text())
        assert data["steps"][0]["step"] == "noop"
        assert "total_seconds" in data


class TestLiteEnvironmentCheck:
    """Tests for the lite-mode mismatch warnings."""

    def test_warns_on_python_mismatch(self, tmp_path: Path) -> None:
        project = tmp_path / "p"
        project.mkdir()
        (project / "requirements.txt").write_text("# none\n")
        a = tmp_path / "a.rpk"
        # Force an impossible Python version into the base image.
        capture_project(project, a, base_image="python:2.7-slim")

        work = tmp_path / "w"
        man = ReproPackManifest.from_yaml(
            zipfile.ZipFile(a).read("repropack.yml").decode()
        )
        man.steps = [Step(id="noop", type=StepType.AUTOMATIC, command="true")]
        rpk = _repack(a, work, man, tmp_path / "py.rpk")

        rep = Reproducer(rpk, lite=True)
        rep.run()
        assert any("Python" in line for line in rep._report_lines)
