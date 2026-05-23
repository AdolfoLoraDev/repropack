"""End-to-end tests with real-world project fixtures."""

from __future__ import annotations

import zipfile
from pathlib import Path

from repropack.core.capture import capture_project
from repropack.core.manifest import ReproPackManifest, StepType
from repropack.utils.environment import detect_env_type

FIXTURES = Path(__file__).parent / "fixtures"


class TestJupyterFixture:
    """End-to-end with a Jupyter notebook project."""

    def test_capture_jupyter_project(self, tmp_path: Path) -> None:
        """Must detect .ipynb and infer jupyter execute step."""
        project = FIXTURES / "jupyter_ml"
        output = tmp_path / "jupyter.rpk"
        result = capture_project(project, output)
        assert result.exists()

        with zipfile.ZipFile(result, "r") as zf:
            manifest_text = zf.read("repropack.yml").decode("utf-8")
            manifest = ReproPackManifest.from_yaml(manifest_text)

        # Should infer a Jupyter step
        jupyter_steps = [
            s
            for s in manifest.steps
            if s.type == StepType.AUTOMATIC and "jupyter" in (s.command or "")
        ]
        assert len(jupyter_steps) == 1
        assert "analysis.ipynb" in (jupyter_steps[0].command or "")

    def test_detects_pip(self) -> None:
        """Fixture must be detected as pip."""
        assert detect_env_type(FIXTURES / "jupyter_ml") == "pip"


class TestCondaRFixture:
    """End-to-end with a Conda + R + shell project."""

    def test_capture_conda_r_project(self, tmp_path: Path) -> None:
        """Must detect conda, R scripts and shell scripts."""
        project = FIXTURES / "conda_r_bio"
        output = tmp_path / "conda_r.rpk"
        result = capture_project(project, output)
        assert result.exists()

        with zipfile.ZipFile(result, "r") as zf:
            manifest_text = zf.read("repropack.yml").decode("utf-8")
            manifest = ReproPackManifest.from_yaml(manifest_text)

        # Should detect conda
        assert manifest.environment.conda_environment is not None

        # Should infer R and shell steps
        r_steps = [
            s
            for s in manifest.steps
            if s.type == StepType.AUTOMATIC and "Rscript" in (s.command or "")
        ]
        sh_steps = [
            s
            for s in manifest.steps
            if s.type == StepType.AUTOMATIC and "bash" in (s.command or "")
        ]
        assert len(r_steps) == 1
        assert len(sh_steps) == 1

    def test_detects_conda(self) -> None:
        """Fixture must be detected as conda."""
        assert detect_env_type(FIXTURES / "conda_r_bio") == "conda"


class TestPhysicsFixture:
    """End-to-end with a Python + C++ extension project."""

    def test_capture_physics_project(self, tmp_path: Path) -> None:
        """Must detect Makefile targets and infer make steps."""
        project = FIXTURES / "physics_simulation"
        output = tmp_path / "physics.rpk"
        result = capture_project(project, output)
        assert result.exists()

        with zipfile.ZipFile(result, "r") as zf:
            manifest_text = zf.read("repropack.yml").decode("utf-8")
            manifest = ReproPackManifest.from_yaml(manifest_text)

        # Should infer Makefile targets
        make_steps = [
            s
            for s in manifest.steps
            if s.type == StepType.AUTOMATIC and "make" in (s.command or "")
        ]
        assert len(make_steps) >= 1
        step_ids = [s.id for s in make_steps]
        assert "make_build" in step_ids
        assert "make_run" in step_ids

    def test_detects_pip(self) -> None:
        """Fixture must be detected as pip."""
        assert detect_env_type(FIXTURES / "physics_simulation") == "pip"
