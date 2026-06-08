"""Tests for multi-language support (R, Julia) and strict reproduction."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from repropack.core.capture import capture_project
from repropack.core.docker_generator import generate_dockerfile
from repropack.core.manifest import EnvironmentSpec, ReproPackManifest
from repropack.core.run import Reproducer
from repropack.utils.environment import (
    detect_julia_project,
    detect_r_renv,
    list_project_files,
)


@pytest.fixture(autouse=True)  # type: ignore[misc]
def _no_network_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid network/docker calls when resolving base-image digests."""
    monkeypatch.setattr(
        "repropack.core.capture.get_base_image_digest",
        lambda img: f"{img}@sha256:fakedigest",
    )


# =====================================================================
# Language detection
# =====================================================================


class TestLanguageDetection:
    """Tests for R and Julia ecosystem detection."""

    def test_detect_renv_toplevel(self, tmp_path: Path) -> None:
        """Detect a top-level renv.lock."""
        (tmp_path / "renv.lock").write_text("{}\n")
        assert detect_r_renv(tmp_path) == tmp_path / "renv.lock"

    def test_detect_renv_in_subdir(self, tmp_path: Path) -> None:
        """Detect renv.lock inside an renv/ directory."""
        (tmp_path / "renv").mkdir()
        (tmp_path / "renv" / "renv.lock").write_text("{}\n")
        assert detect_r_renv(tmp_path) == tmp_path / "renv" / "renv.lock"

    def test_detect_renv_absent(self, tmp_path: Path) -> None:
        """Return None when no renv.lock is present."""
        assert detect_r_renv(tmp_path) is None

    def test_detect_julia_project(self, tmp_path: Path) -> None:
        """Detect a Julia Project.toml."""
        (tmp_path / "Project.toml").write_text('name = "Demo"\n')
        assert detect_julia_project(tmp_path) == tmp_path / "Project.toml"

    def test_detect_julia_absent(self, tmp_path: Path) -> None:
        """Return None when no Project.toml is present."""
        assert detect_julia_project(tmp_path) is None


# =====================================================================
# Dockerfile generation for R and Julia
# =====================================================================


class TestMultiLangDockerfile:
    """Tests for R/Julia blocks in the generated Dockerfile."""

    def test_dockerfile_includes_renv(self) -> None:
        """Dockerfile must install R and restore renv when r_renv is set."""
        env = EnvironmentSpec(base_image="python:3.11-slim", r_renv="renv.lock")
        dockerfile = generate_dockerfile(env)
        assert "r-base" in dockerfile
        assert "renv::restore" in dockerfile
        assert "COPY renv.lock /workspace/renv.lock" in dockerfile

    def test_dockerfile_includes_julia(self) -> None:
        """Dockerfile must install Julia and instantiate when julia_project set."""
        env = EnvironmentSpec(
            base_image="python:3.11-slim", julia_project="Project.toml"
        )
        dockerfile = generate_dockerfile(env)
        assert "julia" in dockerfile
        assert "Pkg.instantiate()" in dockerfile
        assert "COPY Project.toml /workspace/Project.toml" in dockerfile
        assert "COPY Manifest.toml /workspace/Manifest.toml" in dockerfile


# =====================================================================
# Ignore patterns
# =====================================================================


class TestLanguageIgnorePatterns:
    """Language-specific noise must be excluded from the package."""

    def test_excludes_r_and_jupyter_noise(self, tmp_path: Path) -> None:
        """.Rhistory, .RData and .ipynb_checkpoints are excluded."""
        (tmp_path / "analysis.R").write_text("print(1)\n")
        (tmp_path / ".Rhistory").write_text("noise\n")
        (tmp_path / ".RData").write_text("noise\n")
        checkpoints = tmp_path / ".ipynb_checkpoints"
        checkpoints.mkdir()
        (checkpoints / "nb-checkpoint.ipynb").write_text("{}\n")

        files = list_project_files(tmp_path)
        assert "analysis.R" in files
        assert ".Rhistory" not in files
        assert ".RData" not in files
        assert not any("ipynb_checkpoints" in f for f in files)


# =====================================================================
# End-to-end capture with multiple languages
# =====================================================================


class TestMultiLangCapture:
    """Capture of projects mixing Python, R and Julia."""

    def test_capture_r_project(self, tmp_path: Path) -> None:
        """An R project produces an renv-aware manifest and Dockerfile."""
        project = tmp_path / "r_project"
        project.mkdir()
        (project / "analysis.R").write_text('print("hi")\n')
        (project / "renv.lock").write_text('{"R": {"Version": "4.3.0"}}\n')

        output = tmp_path / "r.rpk"
        capture_project(project, output)

        with zipfile.ZipFile(output, "r") as zf:
            names = zf.namelist()
            manifest = ReproPackManifest.from_yaml(
                zf.read("repropack.yml").decode("utf-8")
            )
            dockerfile = zf.read("Dockerfile").decode("utf-8")

        assert manifest.environment.r_renv == "renv.lock"
        assert any(s.command == "Rscript analysis.R" for s in manifest.steps)
        assert "project/renv.lock" in names
        assert "r-base" in dockerfile

    def test_capture_julia_project(self, tmp_path: Path) -> None:
        """A Julia project produces a Julia-aware manifest and stages files."""
        project = tmp_path / "jl_project"
        project.mkdir()
        (project / "run.jl").write_text('println("hi")\n')
        (project / "Project.toml").write_text('name = "Demo"\n')
        (project / "Manifest.toml").write_text("# manifest\n")

        output = tmp_path / "jl.rpk"
        capture_project(project, output)

        with zipfile.ZipFile(output, "r") as zf:
            names = zf.namelist()
            manifest = ReproPackManifest.from_yaml(
                zf.read("repropack.yml").decode("utf-8")
            )

        assert manifest.environment.julia_project == "Project.toml"
        assert any(s.command == "julia --project=. run.jl" for s in manifest.steps)
        assert "project/Project.toml" in names
        assert "project/Manifest.toml" in names


# =====================================================================
# Lockfile must not pollute the user's project directory
# =====================================================================


class TestLockfileSideEffect:
    """Regression: capture must not write lockfiles into the source project."""

    def test_capture_does_not_write_lockfile_to_project(self, tmp_path: Path) -> None:
        """requirements.lock must not appear in the user's project dir."""
        project = tmp_path / "clean_project"
        project.mkdir()
        (project / "main.py").write_text("print(1)\n")
        (project / "requirements.txt").write_text("numpy\n")

        before = {p.name for p in project.iterdir()}
        capture_project(project, tmp_path / "clean.rpk")
        after = {p.name for p in project.iterdir()}

        assert before == after
        assert "requirements.lock" not in after


# =====================================================================
# Strict reproduction
# =====================================================================


def _build_strict_rpk(
    tmp_path: Path,
    captured_content: str,
    step_command: str,
    name: str,
) -> Path:
    """Build an .rpk with a captured ``results/out.txt`` and one output step.

    Args:
        tmp_path: Pytest temp dir.
        captured_content: Content of ``results/out.txt`` recorded at capture.
        step_command: Shell command the step runs to regenerate the output.
        name: Unique name for the project/package.
    """
    from repropack.core.manifest import Step, StepType

    project = tmp_path / f"{name}_project"
    project.mkdir()
    (project / "requirements.txt").write_text("# none\n")
    results = project / "results"
    results.mkdir()
    (results / "out.txt").write_text(captured_content)

    output = tmp_path / f"{name}.rpk"
    capture_project(project, output)

    # Rewrite the manifest with a single automatic step that declares the
    # output, so strict mode has something to verify.
    work = tmp_path / f"{name}_unpack"
    work.mkdir()
    with zipfile.ZipFile(output, "r") as zf:
        zf.extractall(work)

    manifest = ReproPackManifest.from_file(work / "repropack.yml")
    manifest.steps = [
        Step(
            id="gen",
            type=StepType.AUTOMATIC,
            command=step_command,
            outputs=["results/out.txt"],
        )
    ]
    manifest.to_file(work / "repropack.yml")

    new_rpk = tmp_path / f"{name}_fixed.rpk"
    with zipfile.ZipFile(new_rpk, "w", zipfile.ZIP_DEFLATED) as zf:
        for fpath in work.rglob("*"):
            if fpath.is_file():
                zf.write(fpath, fpath.relative_to(work).as_posix())
    return new_rpk


class TestStrictReproduction:
    """Tests for --strict output verification."""

    def test_strict_passes_when_output_matches(self, tmp_path: Path) -> None:
        """Strict run succeeds when the regenerated output is identical."""
        rpk = _build_strict_rpk(
            tmp_path,
            captured_content="stable-result",
            step_command="printf 'stable-result' > results/out.txt",
            name="ok",
        )
        Reproducer(rpk, lite=True, strict=True).run()  # should not raise

    def test_strict_fails_when_output_differs(self, tmp_path: Path) -> None:
        """Strict run fails when the regenerated output differs."""
        rpk = _build_strict_rpk(
            tmp_path,
            captured_content="result-A",
            step_command="printf 'result-B' > results/out.txt",
            name="drift",
        )
        with pytest.raises(RuntimeError, match="Strict reproducibility check failed"):
            Reproducer(rpk, lite=True, strict=True).run()
