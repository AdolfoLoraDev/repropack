"""Tests for Apptainer/Singularity support."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from repropack.core.apptainer_generator import generate_apptainer_def
from repropack.core.capture import capture_project
from repropack.core.manifest import EnvironmentSpec
from repropack.core.run import Reproducer


@pytest.fixture(autouse=True)  # type: ignore[misc]
def _no_network_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid network/docker calls when resolving base-image digests."""
    monkeypatch.setattr(
        "repropack.core.capture.get_base_image_digest",
        lambda img: f"{img}@sha256:fakedigest",
    )


class TestApptainerDefGeneration:
    """Tests for the .def renderer."""

    def test_def_has_core_sections(self) -> None:
        env = EnvironmentSpec(
            base_image="python:3.11-slim@sha256:abc",
            python_requirements="requirements.lock",
            system_packages=["gcc"],
        )
        text = generate_apptainer_def(env, project_files=["train.py"])
        assert "Bootstrap: docker" in text
        assert "From: python:3.11-slim@sha256:abc" in text
        assert "%post" in text
        assert "%files" in text
        assert "%runscript" in text
        assert "pip install --no-cache-dir -r" in text
        assert "--require-hashes" not in text
        assert "train.py /workspace/train.py" in text

    def test_def_require_hashes(self) -> None:
        env = EnvironmentSpec(
            base_image="python:3.11-slim",
            python_requirements="requirements.lock",
        )
        text = generate_apptainer_def(env, pip_require_hashes=True)
        assert "--require-hashes" in text

    def test_def_includes_r_and_julia(self) -> None:
        env = EnvironmentSpec(
            base_image="python:3.11-slim",
            r_renv="renv.lock",
            julia_project="Project.toml",
        )
        text = generate_apptainer_def(env)
        assert "r-base" in text
        assert "renv::restore" in text
        assert "Pkg.instantiate()" in text


class TestApptainerCapture:
    """Capture must emit apptainer.def when requested."""

    def test_capture_emits_def(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        (project / "main.py").write_text("print(1)\n")
        (project / "requirements.txt").write_text("numpy\n")
        output = tmp_path / "p.rpk"
        capture_project(project, output, container="apptainer")

        with zipfile.ZipFile(output, "r") as zf:
            names = zf.namelist()
        assert "apptainer.def" in names
        assert "Dockerfile" in names  # docker is always emitted too

    def test_docker_default_has_no_def(self, tmp_path: Path) -> None:
        project = tmp_path / "proj2"
        project.mkdir()
        (project / "main.py").write_text("print(1)\n")
        output = tmp_path / "p2.rpk"
        capture_project(project, output)

        with zipfile.ZipFile(output, "r") as zf:
            assert "apptainer.def" not in zf.namelist()


class TestBackendSelection:
    """Tests for Reproducer._select_backend."""

    def test_auto_prefers_docker(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "repropack.core.run.shutil.which",
            lambda x: "/usr/bin/docker" if x == "docker" else None,
        )
        rep = Reproducer(tmp_path / "x.rpk")
        assert rep._select_backend(tmp_path) == "docker"

    def test_auto_falls_back_to_apptainer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "apptainer.def").write_text("Bootstrap: docker\n")
        monkeypatch.setattr(
            "repropack.core.run.shutil.which",
            lambda x: "/usr/bin/apptainer" if x == "apptainer" else None,
        )
        rep = Reproducer(tmp_path / "x.rpk")
        assert rep._select_backend(tmp_path) == "apptainer"

    def test_apptainer_requested_without_def_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "repropack.core.run.shutil.which",
            lambda x: "/usr/bin/apptainer",
        )
        rep = Reproducer(tmp_path / "x.rpk", container="apptainer")
        with pytest.raises(RuntimeError, match="no apptainer.def"):
            rep._select_backend(tmp_path)

    def test_no_backend_available_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("repropack.core.run.shutil.which", lambda x: None)
        rep = Reproducer(tmp_path / "x.rpk")
        with pytest.raises(RuntimeError, match="Docker is not installed"):
            rep._select_backend(tmp_path)
