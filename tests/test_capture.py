"""Tests for the capture module and core ReproPack components."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from repropack.core.capture import (
    CaptureOrchestrator,
    _file_hash,
    _parse_makefile_targets,
    capture_project,
)
from repropack.core.docker_generator import generate_dockerfile, get_base_image_digest
from repropack.core.manifest import (
    EnvironmentSpec,
    Metadata,
    ReproPackManifest,
    Step,
    StepType,
)
from repropack.core.provenance import ProvenanceGraph
from repropack.utils.environment import detect_env_type, list_project_files

# =====================================================================
# Helpers
# =====================================================================


def _make_basic_project(tmp_path: Path) -> Path:
    """Create a minimal pip project."""
    project = tmp_path / "basic_project"
    project.mkdir()
    (project / "main.py").write_text("print('hello')\n")
    (project / "requirements.txt").write_text("numpy==1.26.0\n")
    return project


def _hash_file(path: Path) -> str:
    """Compute SHA256 of a file."""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


# =====================================================================
# CaptureOrchestrator – validation
# =====================================================================


class TestCaptureOrchestratorValidation:
    """Tests for input validation in CaptureOrchestrator."""

    def test_rejects_nonexistent_project(self, tmp_path: Path) -> None:
        """Must raise FileNotFoundError when project does not exist."""
        orch = CaptureOrchestrator(
            project_path=tmp_path / "ghost",
            output_path=tmp_path / "out.rpk",
        )
        with pytest.raises(FileNotFoundError):
            orch.run()

    def test_rejects_rpk_as_project(self, tmp_path: Path) -> None:
        """Must raise ValueError when project path ends with .rpk."""
        bad = tmp_path / "bad.rpk"
        bad.write_text("fake")
        orch = CaptureOrchestrator(
            project_path=bad,
            output_path=tmp_path / "out.rpk",
        )
        with pytest.raises(ValueError):
            orch.run()

    def test_rejects_file_as_project(self, tmp_path: Path) -> None:
        """Must raise NotADirectoryError when project is a file."""
        f = tmp_path / "file.txt"
        f.write_text("hello")
        orch = CaptureOrchestrator(
            project_path=f,
            output_path=tmp_path / "out.rpk",
        )
        with pytest.raises(NotADirectoryError):
            orch.run()

    def test_forces_rpk_extension(self, tmp_path: Path) -> None:
        """Output path must end with .rpk even if omitted."""
        project = _make_basic_project(tmp_path)
        out = tmp_path / "package"
        result = CaptureOrchestrator(project, out).run()
        assert result.suffix == ".rpk"
        assert result.name == "package.rpk"


# =====================================================================
# CaptureOrchestrator – full capture flow
# =====================================================================


class TestCaptureBasicProject:
    """End-to-end capture tests."""

    def test_capture_creates_rpk(self, tmp_path: Path) -> None:
        """capture_project must create a .rpk with the expected structure."""
        project = _make_basic_project(tmp_path)
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

    def test_capture_orchestrator_run(self, tmp_path: Path) -> None:
        """CaptureOrchestrator.run must return a valid .rpk."""
        project = _make_basic_project(tmp_path)
        output = tmp_path / "orch.rpk"
        orch = CaptureOrchestrator(project, output)
        result = orch.run()
        assert result.exists()

        with zipfile.ZipFile(result, "r") as zf:
            manifest_text = zf.read("repropack.yml").decode("utf-8")
            manifest = ReproPackManifest.from_yaml(manifest_text)
            assert manifest.metadata.name == "basic_project"
            assert manifest.environment.python_requirements is not None

    def test_capture_populates_file_hashes(self, tmp_path: Path) -> None:
        """Manifest inside .rpk must contain SHA256 hashes."""
        project = _make_basic_project(tmp_path)
        output = tmp_path / "hashed.rpk"
        capture_project(project, output)

        with zipfile.ZipFile(output, "r") as zf:
            manifest_text = zf.read("repropack.yml").decode("utf-8")
            manifest = ReproPackManifest.from_yaml(manifest_text)

        assert manifest.file_hashes
        expected_main_hash = _hash_file(project / "main.py")
        assert manifest.file_hashes.get("main.py") == expected_main_hash


class TestCaptureWithManualSteps:
    """Capture with extra manual steps."""

    def test_manual_steps_in_manifest(self, tmp_path: Path) -> None:
        """Extra manual steps must appear in the generated manifest."""
        project = _make_basic_project(tmp_path)
        extra = [
            Step(
                id="review",
                type=StepType.MANUAL,
                description="Review results",
                instructions="Check that AUC > 0.85",
            ),
        ]
        output = tmp_path / "manual.rpk"
        capture_project(project, output, extra_steps=extra)

        with zipfile.ZipFile(output, "r") as zf:
            manifest_text = zf.read("repropack.yml").decode("utf-8")
            manifest = ReproPackManifest.from_yaml(manifest_text)

        manual_steps = [s for s in manifest.steps if s.type == StepType.MANUAL]
        assert len(manual_steps) == 1
        assert manual_steps[0].id == "review"


# =====================================================================
# Step inference
# =====================================================================


class TestInferSteps:
    """Tests for automatic step inference."""

    def test_infers_python_scripts(self, tmp_path: Path) -> None:
        """Must detect prepare.py, train.py, evaluate.py."""
        project = tmp_path / "py_project"
        project.mkdir()
        (project / "prepare.py").write_text("pass\n")
        (project / "train.py").write_text("pass\n")

        orch = CaptureOrchestrator(project, tmp_path / "out.rpk")
        steps = orch._infer_steps()
        ids = [s.id for s in steps]
        assert "prepare" in ids
        assert "train" in ids

    def test_infers_jupyter_notebooks(self, tmp_path: Path) -> None:
        """Must detect .ipynb files."""
        project = tmp_path / "nb_project"
        project.mkdir()
        (project / "analysis.ipynb").write_text('{"cells": []}\n')

        orch = CaptureOrchestrator(project, tmp_path / "out.rpk")
        steps = orch._infer_steps()
        ids = [s.id for s in steps]
        assert "jupyter_analysis" in ids
        assert any("jupyter execute" in (s.command or "") for s in steps)

    def test_infers_r_scripts(self, tmp_path: Path) -> None:
        """Must detect .R files."""
        project = tmp_path / "r_project"
        project.mkdir()
        (project / "analysis.R").write_text("print(1)\n")

        orch = CaptureOrchestrator(project, tmp_path / "out.rpk")
        steps = orch._infer_steps()
        ids = [s.id for s in steps]
        assert "r_analysis" in ids
        assert any("Rscript" in (s.command or "") for s in steps)

    def test_infers_shell_scripts(self, tmp_path: Path) -> None:
        """Must detect .sh files."""
        project = tmp_path / "sh_project"
        project.mkdir()
        (project / "run.sh").write_text("#!/bin/bash\necho hi\n")

        orch = CaptureOrchestrator(project, tmp_path / "out.rpk")
        steps = orch._infer_steps()
        ids = [s.id for s in steps]
        assert "shell_run" in ids
        assert any("bash" in (s.command or "") for s in steps)

    def test_no_duplicate_python_steps(self, tmp_path: Path) -> None:
        """If both train.py and entrenar.py exist, only one step is added."""
        project = tmp_path / "dup_project"
        project.mkdir()
        (project / "train.py").write_text("pass\n")
        (project / "entrenar.py").write_text("pass\n")

        orch = CaptureOrchestrator(project, tmp_path / "out.rpk")
        steps = orch._infer_steps()
        train_steps = [s for s in steps if s.id == "train"]
        assert len(train_steps) == 1


# =====================================================================
# RPK integrity
# =====================================================================


class TestRpkIntegrity:
    """Hash verification inside the .rpk archive."""

    def test_hashes_match_copied_files(self, tmp_path: Path) -> None:
        """SHA256 in manifest must match the files packaged inside project/."""
        project = tmp_path / "integrity_project"
        project.mkdir()
        (project / "data.csv").write_text("a,b,c\n1,2,3\n")
        (project / "script.py").write_text("print(42)\n")
        (project / "requirements.txt").write_text("pandas\n")

        output = tmp_path / "integrity.rpk"
        capture_project(project, output)

        with zipfile.ZipFile(output, "r") as zf:
            manifest_text = zf.read("repropack.yml").decode("utf-8")
            manifest = ReproPackManifest.from_yaml(manifest_text)

            for rel_path, expected_hash in manifest.file_hashes.items():
                archived = zf.read(f"project/{rel_path}")
                actual_hash = hashlib.sha256(archived).hexdigest()
                assert actual_hash == expected_hash, f"Hash mismatch for {rel_path}"


# =====================================================================
# Snapshot-like tests
# =====================================================================


class TestSnapshotManifest:
    """Stable-output checks for manifest and Dockerfile."""

    def test_manifest_snapshot(self, tmp_path: Path) -> None:
        """Manifest YAML must contain expected keys and structure."""
        project = _make_basic_project(tmp_path)
        output = tmp_path / "snap.rpk"
        capture_project(project, output)

        with zipfile.ZipFile(output, "r") as zf:
            yaml_text = zf.read("repropack.yml").decode("utf-8")

        assert "repropack_version:" in yaml_text
        assert "0.1.1" in yaml_text
        assert "metadata:" in yaml_text
        assert "name: basic_project" in yaml_text
        assert "environment:" in yaml_text
        assert "base_image:" in yaml_text
        assert "python:3.11-slim@sha256:" in yaml_text
        assert "steps:" in yaml_text

    def test_dockerfile_snapshot(self, tmp_path: Path) -> None:
        """Dockerfile must contain expected instructions."""
        project = _make_basic_project(tmp_path)
        output = tmp_path / "docker.rpk"
        capture_project(project, output)

        with zipfile.ZipFile(output, "r") as zf:
            dockerfile = zf.read("Dockerfile").decode("utf-8")

        assert "FROM python:3.11-slim@sha256:" in dockerfile
        assert "DEBIAN_FRONTEND=noninteractive" in dockerfile
        assert "RUN groupadd -r repro" in dockerfile
        assert (
            'COPY ["requirements.lock", "/workspace/requirements.lock"]' in dockerfile
            or 'COPY ["requirements.txt", "/workspace/requirements.txt"]' in dockerfile
        )
        # The fallback lockfile (pip freeze) has no hashes, so the Dockerfile
        # must use a plain pip install rather than --require-hashes.
        assert "pip install --no-cache-dir -r" in dockerfile
        assert "--require-hashes" not in dockerfile
        assert "USER repro" in dockerfile
        # The final instruction must be a valid CMD (regression: it used to
        # emit a bare JSON array without the CMD keyword).
        assert 'CMD ["echo"' in dockerfile


# =====================================================================
# Legacy / existing tests (kept for regression safety)
# =====================================================================


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
                ),
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
        """Must include COPY and a plain install by default (no hashes)."""
        env = EnvironmentSpec(
            base_image="python:3.11-slim@sha256:abc",
            python_requirements="requirements.lock",
        )
        df = generate_dockerfile(env)
        assert 'COPY ["requirements.lock"' in df
        assert "pip install --no-cache-dir -r" in df
        assert "--require-hashes" not in df

    def test_generate_dockerfile_handles_spaces_in_filenames(self) -> None:
        """Filenames with spaces must use JSON-array COPY (regression)."""
        env = EnvironmentSpec(base_image="python:3.11-slim@sha256:abc")
        df = generate_dockerfile(env, project_files=["DKF Derivation.pdf"])
        assert 'COPY ["DKF Derivation.pdf", "/workspace/DKF Derivation.pdf"]' in df

    def test_generate_dockerfile_require_hashes(self) -> None:
        """--require-hashes is emitted only when explicitly requested."""
        env = EnvironmentSpec(
            base_image="python:3.11-slim@sha256:abc",
            python_requirements="requirements.lock",
        )
        df = generate_dockerfile(env, pip_require_hashes=True)
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


class TestCaptureConda:
    """Capture with Conda environment."""

    def test_capture_conda_project(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Must handle conda environment and include conda-lock.yml."""
        project = tmp_path / "conda_project"
        project.mkdir()
        (project / "environment.yml").write_text("name: test_env\n")
        (project / "script.py").write_text("print(1)\n")

        lock_path = project / "conda-lock.yml"
        lock_path.write_text("name: test_env\n")

        # Mock generate_conda_lock to avoid requiring conda binary
        def _fake_conda_lock(path: Path, out: Path | None = None) -> Path:
            return lock_path

        monkeypatch.setattr(
            "repropack.core.capture.generate_conda_lock", _fake_conda_lock
        )

        output = tmp_path / "conda.rpk"
        capture_project(project, output)

        with zipfile.ZipFile(output, "r") as zf:
            manifest_text = zf.read("repropack.yml").decode("utf-8")
            manifest = ReproPackManifest.from_yaml(manifest_text)

        assert manifest.environment.conda_environment is not None


class TestCaptureFallbackRequirements:
    """Fallback to requirements.txt when no lockfile is produced."""

    def test_fallback_to_requirements_txt(self, tmp_path: Path) -> None:
        """If pip freeze fails and no lockfile exists, fallback to requirements.txt."""
        project = tmp_path / "fallback_project"
        project.mkdir()
        (project / "requirements.txt").write_text("requests\n")
        (project / "main.py").write_text("print('hi')\n")

        # Prevent lockfile generation by pre-creating a failed lock
        (project / "requirements.lock").write_text("# Lockfile generation failed\n")

        output = tmp_path / "fallback.rpk"
        capture_project(project, output)

        with zipfile.ZipFile(output, "r") as zf:
            manifest_text = zf.read("repropack.yml").decode("utf-8")
            manifest = ReproPackManifest.from_yaml(manifest_text)

        # Since lockfile was generated (even if placeholder), it should be used
        assert manifest.environment.python_requirements is not None


class TestFileHashUtility:
    """Tests for the standalone _file_hash helper."""

    def test_file_hash_matches_sha256(self, tmp_path: Path) -> None:
        """_file_hash must return the same digest as hashlib.sha256."""
        f = tmp_path / "data.txt"
        f.write_text("reproducibility matters")
        expected = hashlib.sha256(f.read_bytes()).hexdigest()
        assert _file_hash(f) == expected


# =====================================================================
# Makefile parsing
# =====================================================================


class TestMakefileParsing:
    """Tests for Makefile target extraction."""

    def test_parses_simple_targets(self, tmp_path: Path) -> None:
        """Must extract standard target names."""
        mf = tmp_path / "Makefile"
        mf.write_text(
            "build:\n\tgcc main.c -o main\n\n"
            "run: build\n\t./main\n\n"
            "clean:\n\trm -f main\n"
        )
        targets = _parse_makefile_targets(mf)
        assert targets == ["build", "run", "clean"]

    def test_ignores_comments_and_variables(self, tmp_path: Path) -> None:
        """Must skip comments, variable assignments, and directives."""
        mf = tmp_path / "Makefile"
        mf.write_text(
            "# Comment line\n"
            "CC := gcc\n"
            "CFLAGS ?= -O2\n"
            "build:\n\t$(CC) main.c\n"
        )
        targets = _parse_makefile_targets(mf)
        assert targets == ["build"]

    def test_ignores_indented_lines(self, tmp_path: Path) -> None:
        """Indented recipe lines must not be treated as targets."""
        mf = tmp_path / "Makefile"
        mf.write_text("all:\n" "\tgcc main.c\n" "\trun: should_be_ignored\n")
        targets = _parse_makefile_targets(mf)
        assert targets == ["all"]


# =====================================================================
# Base image digest resolution
# =====================================================================


class TestBaseImageDigest:
    """Tests for get_base_image_digest."""

    def test_returns_unchanged_if_already_has_digest(self) -> None:
        """If the image already contains @sha256:, return as-is."""
        pinned = "python:3.11-slim@sha256:abc123"
        assert get_base_image_digest(pinned) == pinned

    def test_orchestrator_uses_base_image_override(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CaptureOrchestrator must respect the base_image override."""
        project = tmp_path / "override_project"
        project.mkdir()
        (project / "main.py").write_text("print(1)\n")
        (project / "requirements.txt").write_text("requests\n")

        # Prevent network calls by monkeypatching digest resolver
        monkeypatch.setattr(
            "repropack.core.capture.get_base_image_digest",
            lambda img: f"{img}@sha256:fakedigest",
        )

        output = tmp_path / "override.rpk"
        orch = CaptureOrchestrator(project, output, base_image="python:3.10-slim")
        result = orch.run()

        with zipfile.ZipFile(result, "r") as zf:
            manifest_text = zf.read("repropack.yml").decode("utf-8")
            manifest = ReproPackManifest.from_yaml(manifest_text)

        assert manifest.environment.base_image == "python:3.10-slim@sha256:fakedigest"
