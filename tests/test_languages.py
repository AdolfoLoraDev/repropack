"""Tests for compiled-language, Octave, CMake support and runtime validation."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from repropack.core.capture import CaptureOrchestrator, capture_project
from repropack.core.manifest import ReproPackManifest
from repropack.core.validate import validate_package


@pytest.fixture(autouse=True)  # type: ignore[misc]
def _no_network_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid network/docker calls when resolving base-image digests."""
    monkeypatch.setattr(
        "repropack.core.capture.get_base_image_digest",
        lambda img: f"{img}@sha256:fakedigest",
    )


def _capture(project: Path, tmp_path: Path, name: str) -> ReproPackManifest:
    output = tmp_path / f"{name}.rpk"
    capture_project(project, output)
    with zipfile.ZipFile(output, "r") as zf:
        return ReproPackManifest.from_yaml(zf.read("repropack.yml").decode("utf-8"))


class TestStepLanguageTagging:
    """Inferred steps must carry a language tag."""

    def test_python_and_r_languages(self, tmp_path: Path) -> None:
        project = tmp_path / "mix"
        project.mkdir()
        (project / "train.py").write_text("print(1)\n")
        (project / "analysis.R").write_text('print("x")\n')
        manifest = _capture(project, tmp_path, "mix")

        langs = {s.id: s.language for s in manifest.steps}
        assert langs["train"] == "python"
        assert langs["r_analysis"] == "r"


class TestCMakeSupport:
    """CMake projects must produce configure + build steps and packages."""

    def test_cmake_steps_and_packages(self, tmp_path: Path) -> None:
        project = tmp_path / "cmake_proj"
        project.mkdir()
        (project / "CMakeLists.txt").write_text("project(demo)\n")
        (project / "main.cpp").write_text("int main(){return 0;}\n")
        manifest = _capture(project, tmp_path, "cmake")

        ids = [s.id for s in manifest.steps]
        assert "cmake_configure" in ids
        assert "cmake_build" in ids
        build = next(s for s in manifest.steps if s.id == "cmake_build")
        assert build.depends_on == ["cmake_configure"]
        assert "cmake" in manifest.environment.system_packages
        assert "build-essential" in manifest.environment.system_packages


class TestOctaveSupport:
    """MATLAB/Octave .m scripts must be detected."""

    def test_octave_step_and_package(self, tmp_path: Path) -> None:
        project = tmp_path / "oct"
        project.mkdir()
        (project / "sim.m").write_text("disp('hi')\n")
        manifest = _capture(project, tmp_path, "oct")

        step = next(s for s in manifest.steps if s.id == "octave_sim")
        assert step.language == "octave"
        assert step.command == "octave --no-gui sim.m"
        assert "octave" in manifest.environment.system_packages


class TestFortranSupport:
    """Fortran sources must pull in gfortran."""

    def test_gfortran_package(self, tmp_path: Path) -> None:
        project = tmp_path / "fort"
        project.mkdir()
        (project / "solver.f90").write_text("program p\nend program p\n")
        orch = CaptureOrchestrator(project, tmp_path / "fort.rpk")
        orch.project_path = project.resolve()
        assert "gfortran" in orch._infer_system_packages()


class TestRuntimeValidation:
    """validate must warn when a required runtime is missing from Dockerfile."""

    def test_warns_when_julia_runtime_missing(self, tmp_path: Path) -> None:
        # Build a normal package, then strip the Julia install from the
        # Dockerfile and add a Julia step, then re-pack and validate.
        project = tmp_path / "jl"
        project.mkdir()
        (project / "main.py").write_text("print(1)\n")
        output = tmp_path / "jl.rpk"
        capture_project(project, output)

        work = tmp_path / "unpack"
        work.mkdir()
        with zipfile.ZipFile(output, "r") as zf:
            zf.extractall(work)

        from repropack.core.manifest import Step, StepType

        manifest = ReproPackManifest.from_file(work / "repropack.yml")
        manifest.steps.append(
            Step(
                id="julia_run",
                type=StepType.AUTOMATIC,
                command="julia run.jl",
                language="julia",
            )
        )
        manifest.to_file(work / "repropack.yml")
        # Dockerfile has no Julia runtime (plain python image).

        repacked = tmp_path / "jl_fixed.rpk"
        with zipfile.ZipFile(repacked, "w", zipfile.ZIP_DEFLATED) as zf:
            for fpath in work.rglob("*"):
                if fpath.is_file():
                    zf.write(fpath, fpath.relative_to(work).as_posix())

        result = validate_package(repacked)
        assert any("Julia" in w for w in result.warnings)
