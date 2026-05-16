"""Tests for the capture module and core ReproPack components."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from repropack.core.capture import _build_manifest, _infer_steps, capture_project
from repropack.core.docker_generator import generate_dockerfile
from repropack.core.manifest import (
    EnvironmentSpec,
    Metadata,
    ReproPackManifest,
    Step,
    StepType,
)
from repropack.core.provenance import ProvenanceGraph
from repropack.utils.environment import detect_env_type, list_project_files


class TestDetectEnvType:
    """Tests for environment detection."""

    def test_detect_pip(self, tmp_path: Path) -> None:
        """Must detect pip environment by requirements.txt."""
        (tmp_path / "requirements.txt").write_text("numpy\n")
        assert detect_env_type(tmp_path) == "pip"

    def test_detect_conda(self, tmp_path: Path) -> None:
        """Must detect conda environment by environment.yml."""
        (tmp_path / "environment.yml").write_text("name: test\n")
        assert detect_env_type(tmp_path) == "conda"

    def test_detect_poetry(self, tmp_path: Path) -> None:
        """Must detect poetry environment by pyproject.toml."""
        (tmp_path / "pyproject.toml").write_text("[tool.poetry]\n")
        assert detect_env_type(tmp_path) == "poetry"

    def test_detect_unknown(self, tmp_path: Path) -> None:
        """Must return unknown when no recognized files exist."""
        assert detect_env_type(tmp_path) == "unknown"


class TestManifest:
    """Tests for the repropack.yml manifest."""

    def test_manifest_roundtrip(self, tmp_path: Path) -> None:
        """Serialize and deserialize a manifest must preserve data."""
        manifest = ReproPackManifest(
            metadata=Metadata(name="test", authors=["Ana"]),
            environment=EnvironmentSpec(base_image="python:3.11-slim@sha256:abc"),
            steps=[
                Step(
                    id="step1",
                    type=StepType.AUTOMATIC,
                    command="python main.py",
                    inputs=["data/"],
                    outputs=["results/"],
                ),
                Step(
                    id="step2",
                    type=StepType.MANUAL,
                    description="Human review",
                    instructions="Verify that AUC > 0.85",
                ),
            ],
        )
        path = tmp_path / "repropack.yml"
        manifest.to_file(path)
        loaded = ReproPackManifest.from_file(path)

        assert loaded.metadata.name == "test"
        assert len(loaded.steps) == 2
        assert loaded.steps[0].type == StepType.AUTOMATIC
        assert loaded.steps[1].type == StepType.MANUAL

    def test_automatic_requires_command(self) -> None:
        """An automatic step without a command must raise ValidationError."""
        with pytest.raises(ValueError):
            Step(id="bad", type=StepType.AUTOMATIC)

    def test_manual_requires_instructions_or_description(self) -> None:
        """A manual step without instructions or description must raise an error."""
        with pytest.raises(ValueError):
            Step(id="bad", type=StepType.MANUAL)


class TestProvenanceGraph:
    """Tests for the W3C PROV provenance graph."""

    def test_build_from_manifest(self) -> None:
        """Must build a PROV document without errors."""
        manifest = ReproPackManifest(
            metadata=Metadata(name="prov_test", authors=["Bob"]),
            environment=EnvironmentSpec(base_image="python:3.10"),
            steps=[
                Step(
                    id="train",
                    type=StepType.AUTOMATIC,
                    command="python train.py",
                    inputs=["data.csv"],
                    outputs=["model.pkl"],
                )
            ],
        )
        prov = ProvenanceGraph()
        prov.build_from_manifest(manifest)

        json_str = prov.to_json()
        data = json.loads(json_str)
        assert "agent" in str(data).lower() or "activity" in str(data).lower()

    def test_to_mermaid_not_empty(self) -> None:
        """Mermaid output must contain nodes and edges."""
        manifest = ReproPackManifest(
            metadata=Metadata(name="mermaid_test"),
            environment=EnvironmentSpec(base_image="python:3.10"),
            steps=[],
        )
        prov = ProvenanceGraph()
        prov.build_from_manifest(manifest)
        mmd = prov.to_mermaid()
        assert "graph TD" in mmd

    def test_to_html_contains_mermaid(self) -> None:
        """HTML output must include the Mermaid script."""
        manifest = ReproPackManifest(
            metadata=Metadata(name="html_test"),
            environment=EnvironmentSpec(base_image="python:3.10"),
            steps=[],
        )
        prov = ProvenanceGraph()
        prov.build_from_manifest(manifest)
        html = prov.to_html(title="Test")
        assert "mermaid" in html
        assert "Test" in html


class TestDockerGenerator:
    """Tests for Dockerfile generation."""

    def test_generate_dockerfile_has_from(self) -> None:
        """Dockerfile must include the base image."""
        env = EnvironmentSpec(base_image="python:3.11-slim@sha256:abc")
        df = generate_dockerfile(env)
        assert "FROM python:3.11-slim@sha256:abc" in df

    def test_generate_dockerfile_with_requirements(self) -> None:
        """Must include COPY and install for requirements."""
        env = EnvironmentSpec(
            base_image="python:3.11-slim@sha256:abc",
            python_requirements="requirements.lock",
        )
        df = generate_dockerfile(env)
        assert "COPY requirements.lock" in df
        assert "--require-hashes" in df

    def test_generate_dockerfile_with_system_packages(self) -> None:
        """Must install system packages."""
        env = EnvironmentSpec(
            base_image="python:3.11-slim@sha256:abc",
            system_packages=["build-essential", "git"],
        )
        df = generate_dockerfile(env)
        assert "apt-get install" in df
        assert "build-essential" in df
        assert "git" in df


class TestListProjectFiles:
    """Tests for project file listing."""

    def test_ignores_pycache(self, tmp_path: Path) -> None:
        """Must not include __pycache__ or .pyc files."""
        (tmp_path / "main.py").write_text("print(1)\n")
        pycache = tmp_path / "__pycache__"
        pycache.mkdir()
        (pycache / "main.cpython-311.pyc").write_text("fake\n")
        files = list_project_files(tmp_path)
        assert "main.py" in files
        assert not any("__pycache__" in f for f in files)

    def test_ignores_git(self, tmp_path: Path) -> None:
        """Must not include the .git directory."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text("[core]\n")
        (tmp_path / "script.py").write_text("pass\n")
        files = list_project_files(tmp_path)
        assert "script.py" in files
        assert not any(".git" in f for f in files)


class TestCaptureIntegration:
    """Integration tests for full capture workflow."""

    def test_capture_creates_rpk(self, tmp_path: Path) -> None:
        """capture_project must create a .rpk with the expected structure."""
        project = tmp_path / "my_project"
        project.mkdir()
        (project / "main.py").write_text("print('hello')\n")
        (project / "requirements.txt").write_text("numpy==1.26.0\n")

        output = tmp_path / "package.rpk"
        result = capture_project(project, output)

        assert result.exists()
        assert result.suffix == ".rpk"

        with zipfile.ZipFile(result, "r") as zf:
            names = zf.namelist()
            assert "repropack.yml" in names
            assert "Dockerfile" in names
            assert "provenance.json" in names
            assert any(n.startswith("project/") for n in names)

    def test_build_manifest_infer_steps(self, tmp_path: Path) -> None:
        """_build_manifest must infer steps from known scripts."""
        (tmp_path / "train.py").write_text("pass\n")
        manifest = _build_manifest(tmp_path, "pip", None, None)
        assert any(s.id == "train" for s in manifest.steps)

    def test_infer_steps_detects_prepare(self, tmp_path: Path) -> None:
        """_infer_steps must detect prepare.py."""
        (tmp_path / "prepare.py").write_text("pass\n")
        steps = _infer_steps(tmp_path)
        assert any(s.id == "prepare" for s in steps)
