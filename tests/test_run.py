"""Tests for the reproduction (run) module."""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

import pytest

from repropack.core.capture import capture_project
from repropack.core.manifest import ReproPackManifest, Step, StepType
from repropack.core.run import Reproducer, run_package

# =====================================================================
# Helpers
# =====================================================================


def _make_rpk(
    tmp_path: Path,
    with_manual: bool = False,
    corrupt_hash: bool = False,
    omit_manifest: bool = False,
) -> Path:
    """Build a minimal ``.rpk`` package for testing."""
    project = tmp_path / "sample_project"
    project.mkdir()
    (project / "main.py").write_text("print('hello from repro')\n")
    (project / "train.py").write_text("print('training')\n")
    (project / "requirements.txt").write_text("numpy\n")

    output = tmp_path / "sample.rpk"
    capture_project(project, output)

    if with_manual or corrupt_hash or omit_manifest:
        # Unpack, modify, repack
        mod_dir = tmp_path / "mod_rpk"
        mod_dir.mkdir()
        with zipfile.ZipFile(output, "r") as zf:
            zf.extractall(mod_dir)

        manifest_path = mod_dir / "repropack.yml"
        manifest = ReproPackManifest.from_file(manifest_path)

        if with_manual:
            manifest.steps.append(
                Step(
                    id="review",
                    type=StepType.MANUAL,
                    description="Review output",
                    instructions="Verify results are correct",
                )
            )

        if corrupt_hash:
            manifest.file_hashes["main.py"] = "0" * 64

        if not omit_manifest:
            manifest.to_file(manifest_path)
        else:
            manifest_path.unlink()

        # Repack
        output = tmp_path / "sample_modified.rpk"
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
            for fpath in mod_dir.rglob("*"):
                if fpath.is_file():
                    arcname = fpath.relative_to(mod_dir).as_posix()
                    zf.write(fpath, arcname)

    return output


class SpySubprocess:
    """Records subprocess.run calls."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        """Initialize the spy with fake return values."""
        self.calls: list[list[str]] = []
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture  # type: ignore[misc]
def fake_subprocess(monkeypatch: pytest.MonkeyPatch) -> SpySubprocess:
    """Monkeypatch subprocess.run with a spy."""
    spy = SpySubprocess()

    class FakeResult:
        returncode = 0
        stdout = "fake stdout"
        stderr = ""

    def _run(cmd: list[str] | str, **kwargs: Any) -> FakeResult:
        if isinstance(cmd, list):
            spy.calls.append(cmd)
        else:
            spy.calls.append([cmd])
        return FakeResult()

    monkeypatch.setattr("repropack.core.run.subprocess.run", _run)
    return spy


@pytest.fixture  # type: ignore[misc]
def fake_no_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend docker is not installed."""
    monkeypatch.setattr("repropack.core.run.shutil.which", lambda x: None)


@pytest.fixture  # type: ignore[misc]
def fake_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend docker is installed."""
    monkeypatch.setattr("repropack.core.run.shutil.which", lambda x: "/usr/bin/docker")


@pytest.fixture(autouse=True)  # type: ignore[misc]
def _patch_digest_for_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent network/docker calls during capture in run tests."""
    monkeypatch.setattr(
        "repropack.core.capture.get_base_image_digest",
        lambda img: f"{img}@sha256:fakedigest",
    )


# =====================================================================
# Validation
# =====================================================================


class TestValidatePackage:
    """Tests for _validate_package."""

    def test_valid_package(self, tmp_path: Path) -> None:
        """Must return manifest for a valid package."""
        rpk = _make_rpk(tmp_path)
        rep = Reproducer(rpk)
        with zipfile.ZipFile(rpk, "r") as zf:
            zf.extractall(tmp_path / "extracted")
        manifest = rep._validate_package(tmp_path / "extracted")
        assert manifest.metadata.name == "sample_project"

    def test_missing_manifest(self, tmp_path: Path) -> None:
        """Must raise ValueError when repropack.yml is missing."""
        rpk = _make_rpk(tmp_path, omit_manifest=True)
        rep = Reproducer(rpk)
        with zipfile.ZipFile(rpk, "r") as zf:
            zf.extractall(tmp_path / "extracted")
        with pytest.raises(ValueError, match="repropack.yml"):
            rep._validate_package(tmp_path / "extracted")

    def test_hash_mismatch(self, tmp_path: Path) -> None:
        """Must raise ValueError when a file hash does not match."""
        rpk = _make_rpk(tmp_path, corrupt_hash=True)
        rep = Reproducer(rpk)
        with zipfile.ZipFile(rpk, "r") as zf:
            zf.extractall(tmp_path / "extracted")
        with pytest.raises(ValueError, match="Hash mismatch"):
            rep._validate_package(tmp_path / "extracted")


# =====================================================================
# Docker build
# =====================================================================


class TestBuildDocker:
    """Tests for _build_docker."""

    def test_builds_with_cache(
        self,
        tmp_path: Path,
        fake_subprocess: SpySubprocess,
        fake_docker: None,
    ) -> None:
        """By default docker build must NOT include --no-cache."""
        rep = Reproducer(tmp_path / "dummy.rpk")
        df = tmp_path / "Dockerfile"
        df.write_text("FROM python\n")
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        rep._build_docker(df, ctx, "myimg:latest")
        assert len(fake_subprocess.calls) == 1
        assert "--no-cache" not in fake_subprocess.calls[0]

    def test_no_cache_flag(
        self,
        tmp_path: Path,
        fake_subprocess: SpySubprocess,
        fake_docker: None,
    ) -> None:
        """With no_cache=True, docker build must include --no-cache."""
        rep = Reproducer(tmp_path / "dummy.rpk", no_cache=True)
        df = tmp_path / "Dockerfile"
        df.write_text("FROM python\n")
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        rep._build_docker(df, ctx, "myimg:latest")
        assert "--no-cache" in fake_subprocess.calls[0]

    def test_missing_docker(self, tmp_path: Path, fake_no_docker: None) -> None:
        """Must raise RuntimeError if docker is not in PATH."""
        rep = Reproducer(tmp_path / "dummy.rpk")
        with pytest.raises(RuntimeError, match="Docker is not installed"):
            rep._build_docker(tmp_path / "Dockerfile", tmp_path, "img")


# =====================================================================
# Step execution
# =====================================================================


class TestRunStepInDocker:
    """Tests for _run_step_in_docker."""

    def test_success(self, tmp_path: Path, fake_subprocess: SpySubprocess) -> None:
        """Must return stdout on success."""
        rep = Reproducer(tmp_path / "dummy.rpk")
        step = Step(id="s1", type=StepType.AUTOMATIC, command="python main.py")
        out = rep._run_step_in_docker(step, "img", tmp_path)
        assert out == "fake stdout"
        assert fake_subprocess.calls[-1][0] == "docker"

    def test_missing_command(self, tmp_path: Path) -> None:
        """Must raise ValueError if step has no command."""
        rep = Reproducer(tmp_path / "dummy.rpk")
        step = Step.model_construct(id="s1", type=StepType.AUTOMATIC, command=None)
        with pytest.raises(ValueError, match="no command"):
            rep._run_step_in_docker(step, "img", tmp_path)

    def test_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Must raise RuntimeError on non-zero exit."""

        def _bad_run(cmd: list[str] | str, **kwargs: Any) -> Any:
            class BadResult:
                returncode = 1
                stdout = ""
                stderr = "error msg"

            return BadResult()

        monkeypatch.setattr("repropack.core.run.subprocess.run", _bad_run)
        rep = Reproducer(tmp_path / "dummy.rpk")
        step = Step(id="s1", type=StepType.AUTOMATIC, command="python main.py")
        with pytest.raises(RuntimeError, match="failed with code 1"):
            rep._run_step_in_docker(step, "img", tmp_path)


class TestRunStepLite:
    """Tests for _run_step_lite."""

    def test_success(self, tmp_path: Path, fake_subprocess: SpySubprocess) -> None:
        """Must return stdout on success."""
        rep = Reproducer(tmp_path / "dummy.rpk")
        step = Step(id="s1", type=StepType.AUTOMATIC, command="python main.py")
        out = rep._run_step_lite(step, tmp_path)
        assert out == "fake stdout"
        # Lite mode uses shell=True with a string command
        assert fake_subprocess.calls[-1] == ["python main.py"]

    def test_missing_command(self, tmp_path: Path) -> None:
        """Must raise ValueError if step has no command."""
        rep = Reproducer(tmp_path / "dummy.rpk")
        step = Step.model_construct(id="s1", type=StepType.AUTOMATIC, command=None)
        with pytest.raises(ValueError, match="no command"):
            rep._run_step_lite(step, tmp_path)

    def test_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Must raise RuntimeError on non-zero exit."""

        def _bad_run(cmd: list[str] | str, **kwargs: Any) -> Any:
            class BadResult:
                returncode = 2
                stdout = ""
                stderr = "lite error"

            return BadResult()

        monkeypatch.setattr("repropack.core.run.subprocess.run", _bad_run)
        rep = Reproducer(tmp_path / "dummy.rpk")
        step = Step(id="s1", type=StepType.AUTOMATIC, command="python main.py")
        with pytest.raises(RuntimeError, match="failed with code 2"):
            rep._run_step_lite(step, tmp_path)


# =====================================================================
# Full cycles
# =====================================================================


class TestRunFullCycle:
    """End-to-end reproduction tests."""

    def test_run_full_cycle(
        self,
        tmp_path: Path,
        fake_subprocess: SpySubprocess,
        fake_docker: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Reproducer.run must complete for a valid .rpk."""
        rpk = _make_rpk(tmp_path)
        rep = Reproducer(rpk)
        # Patch Confirm.ask to avoid interactive prompt (no manual steps here)
        monkeypatch.setattr("repropack.core.run.Confirm.ask", lambda x: True)
        rep.run()
        # Should have called docker build + docker run
        docker_calls = [c for c in fake_subprocess.calls if c[0] == "docker"]
        assert any(c[1] == "build" for c in docker_calls)
        assert any(c[1] == "run" for c in docker_calls)

    def test_run_package_wrapper(
        self,
        tmp_path: Path,
        fake_subprocess: SpySubprocess,
        fake_docker: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """run_package wrapper must delegate to Reproducer successfully."""
        rpk = _make_rpk(tmp_path)
        monkeypatch.setattr("repropack.core.run.Confirm.ask", lambda x: True)
        run_package(rpk)
        docker_calls = [c for c in fake_subprocess.calls if c[0] == "docker"]
        assert any(c[1] == "build" for c in docker_calls)

    def test_missing_rpk(self, tmp_path: Path) -> None:
        """Must raise FileNotFoundError when .rpk does not exist."""
        rep = Reproducer(tmp_path / "ghost.rpk")
        with pytest.raises(FileNotFoundError):
            rep.run()


class TestManualStepPrompt:
    """Tests for manual step handling."""

    def test_manual_prompt_yes(
        self,
        tmp_path: Path,
        fake_subprocess: SpySubprocess,
        fake_docker: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When user confirms, reproduction continues."""
        rpk = _make_rpk(tmp_path, with_manual=True)
        monkeypatch.setattr("repropack.core.run.Confirm.ask", lambda x: True)
        rep = Reproducer(rpk)
        rep.run()
        assert "completed manually" in rep._report_lines[-1]

    def test_manual_prompt_no(
        self,
        tmp_path: Path,
        fake_subprocess: SpySubprocess,
        fake_docker: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When user declines, reproduction stops early."""
        rpk = _make_rpk(tmp_path, with_manual=True)
        monkeypatch.setattr("repropack.core.run.Confirm.ask", lambda x: False)
        rep = Reproducer(rpk)
        rep.run()
        assert "aborted by user" in rep._report_lines[-1]

    def test_skip_manual(
        self,
        tmp_path: Path,
        fake_subprocess: SpySubprocess,
        fake_docker: None,
    ) -> None:
        """With skip_manual=True, no prompt is shown."""
        rpk = _make_rpk(tmp_path, with_manual=True)
        rep = Reproducer(rpk, skip_manual=True)
        rep.run()
        assert any("skipped (--skip-manual)" in line for line in rep._report_lines)


class TestLiteMode:
    """Tests for --lite execution without Docker."""

    def test_lite_executes_directly(
        self,
        tmp_path: Path,
        fake_subprocess: SpySubprocess,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Lite mode must skip docker build and run commands via shell."""
        rpk = _make_rpk(tmp_path)
        monkeypatch.setattr("repropack.core.run.Confirm.ask", lambda x: True)
        rep = Reproducer(rpk, lite=True)
        rep.run()
        # No docker calls should have been made
        docker_calls = [c for c in fake_subprocess.calls if c and c[0] == "docker"]
        assert not docker_calls
        # Should have a lite-mode subprocess call
        lite_calls = [c for c in fake_subprocess.calls if c == ["python train.py"]]
        assert len(lite_calls) == 1
        assert "lite (no container)" in rep._report_lines[0]


class TestFailureCases:
    """Error handling tests."""

    def test_docker_not_installed(self, tmp_path: Path, fake_no_docker: None) -> None:
        """Must raise RuntimeError when docker is missing."""
        rpk = _make_rpk(tmp_path)
        rep = Reproducer(rpk)
        with pytest.raises(RuntimeError, match="Docker is not installed"):
            rep.run()

    def test_corrupt_hash(
        self,
        tmp_path: Path,
        fake_docker: None,
    ) -> None:
        """Must raise ValueError when file hashes do not match."""
        rpk = _make_rpk(tmp_path, corrupt_hash=True)
        rep = Reproducer(rpk)
        with pytest.raises(ValueError, match="Hash mismatch"):
            rep.run()

    def test_missing_manifest(
        self,
        tmp_path: Path,
        fake_docker: None,
    ) -> None:
        """Must raise ValueError when repropack.yml is missing."""
        rpk = _make_rpk(tmp_path, omit_manifest=True)
        rep = Reproducer(rpk)
        with pytest.raises(ValueError, match="repropack.yml"):
            rep.run()


# =====================================================================
# Coverage: container backends, step execution, strict, profile
# =====================================================================


class _Result:
    def __init__(
        self, returncode: int = 0, stdout: str = "out", stderr: str = ""
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _make_apptainer_rpk(tmp_path: Path) -> Path:
    """Capture a package that also contains apptainer.def."""
    from repropack.core.capture import capture_project

    project = tmp_path / "apt_project"
    project.mkdir()
    (project / "main.py").write_text("print('x')\n")
    # An inferred automatic step so the apptainer exec path is exercised.
    (project / "prepare.py").write_text("print('prep')\n")
    (project / "requirements.txt").write_text("numpy\n")
    out = tmp_path / "apt.rpk"
    capture_project(project, out, container="both")
    return out


class TestDockerStepExecution:
    def test_run_step_in_docker_success(
        self, tmp_path: Path, fake_subprocess: SpySubprocess, fake_docker: None
    ) -> None:
        rep = Reproducer(tmp_path / "x.rpk")
        step = Step(id="s", type=StepType.AUTOMATIC, command="echo hi")
        out = rep._run_step_in_docker(step, "img:latest", tmp_path)
        assert out == "fake stdout"
        assert fake_subprocess.calls[0][0] == "docker"

    def test_run_step_in_docker_no_command(self, tmp_path: Path) -> None:
        rep = Reproducer(tmp_path / "x.rpk")
        step = Step(id="s", type=StepType.MANUAL, instructions="x")
        with pytest.raises(ValueError, match="no command"):
            rep._run_step_in_docker(step, "img", tmp_path)

    def test_run_step_in_docker_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "repropack.core.run.subprocess.run",
            lambda *a, **k: _Result(returncode=2, stderr="boom"),
        )
        rep = Reproducer(tmp_path / "x.rpk")
        step = Step(id="s", type=StepType.AUTOMATIC, command="false")
        with pytest.raises(RuntimeError, match="failed with code 2"):
            rep._run_step_in_docker(step, "img", tmp_path)

    def test_build_docker_failure(
        self, tmp_path: Path, fake_docker: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess as sp

        def _raise(*a: object, **k: object) -> None:
            raise sp.CalledProcessError(1, "docker")

        monkeypatch.setattr("repropack.core.run.subprocess.run", _raise)
        rep = Reproducer(tmp_path / "x.rpk")
        df = tmp_path / "Dockerfile"
        df.write_text("FROM python\n")
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        with pytest.raises(RuntimeError, match="Error building Docker image"):
            rep._build_docker(df, ctx, "img")


class TestApptainerExecution:
    def test_build_apptainer(
        self,
        tmp_path: Path,
        fake_subprocess: SpySubprocess,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "repropack.core.run.shutil.which",
            lambda x: "/usr/bin/apptainer" if x == "apptainer" else None,
        )
        rep = Reproducer(tmp_path / "x.rpk")
        ctx = tmp_path / "project"
        ctx.mkdir()
        defp = tmp_path / "apptainer.def"
        defp.write_text("Bootstrap: docker\n")
        sif = rep._build_apptainer(defp, ctx, "exp")
        assert sif.name == "exp.sif"
        assert fake_subprocess.calls[0][0] == "apptainer"

    def test_build_apptainer_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess as sp

        monkeypatch.setattr(
            "repropack.core.run.shutil.which", lambda x: "/bin/apptainer"
        )

        def _raise(*a: object, **k: object) -> None:
            raise sp.CalledProcessError(1, "apptainer")

        monkeypatch.setattr("repropack.core.run.subprocess.run", _raise)
        rep = Reproducer(tmp_path / "x.rpk")
        ctx = tmp_path / "project"
        ctx.mkdir()
        with pytest.raises(RuntimeError, match="Error building Apptainer"):
            rep._build_apptainer(tmp_path / "apptainer.def", ctx, "exp")

    def test_run_step_in_apptainer_success(
        self,
        tmp_path: Path,
        fake_subprocess: SpySubprocess,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "repropack.core.run.shutil.which", lambda x: "/bin/apptainer"
        )
        rep = Reproducer(tmp_path / "x.rpk")
        rep._sif_path = tmp_path / "exp.sif"
        step = Step(id="s", type=StepType.AUTOMATIC, command="echo hi")
        out = rep._run_step_in_apptainer(step, tmp_path)
        assert out == "fake stdout"
        assert fake_subprocess.calls[0][0] == "apptainer"

    def test_run_step_in_apptainer_no_command(self, tmp_path: Path) -> None:
        rep = Reproducer(tmp_path / "x.rpk")
        rep._sif_path = tmp_path / "e.sif"
        step = Step(id="s", type=StepType.MANUAL, instructions="x")
        with pytest.raises(ValueError, match="no command"):
            rep._run_step_in_apptainer(step, tmp_path)

    def test_run_step_in_apptainer_no_image(self, tmp_path: Path) -> None:
        rep = Reproducer(tmp_path / "x.rpk")
        step = Step(id="s", type=StepType.AUTOMATIC, command="echo hi")
        with pytest.raises(RuntimeError, match="image was not built"):
            rep._run_step_in_apptainer(step, tmp_path)

    def test_run_step_in_apptainer_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "repropack.core.run.shutil.which", lambda x: "/bin/apptainer"
        )
        monkeypatch.setattr(
            "repropack.core.run.subprocess.run",
            lambda *a, **k: _Result(returncode=3, stderr="bad"),
        )
        rep = Reproducer(tmp_path / "x.rpk")
        rep._sif_path = tmp_path / "e.sif"
        step = Step(id="s", type=StepType.AUTOMATIC, command="false")
        with pytest.raises(RuntimeError, match="failed with code 3"):
            rep._run_step_in_apptainer(step, tmp_path)

    def test_full_apptainer_run(
        self,
        tmp_path: Path,
        fake_subprocess: SpySubprocess,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        rpk = _make_apptainer_rpk(tmp_path)
        monkeypatch.setattr(
            "repropack.core.run.shutil.which",
            lambda x: "/bin/apptainer" if x in ("apptainer", "singularity") else None,
        )
        rep = Reproducer(rpk, container="apptainer")
        rep.run()
        binaries = {c[0] for c in fake_subprocess.calls}
        assert "apptainer" in binaries


class TestStrictAndProfileDocker:
    def test_verify_outputs_missing_file(self, tmp_path: Path) -> None:
        from repropack.core.manifest import EnvironmentSpec, Metadata

        manifest = ReproPackManifest(
            metadata=Metadata(name="m"),
            environment=EnvironmentSpec(base_image="python:3.11-slim@sha256:a"),
            steps=[
                Step(
                    id="s",
                    type=StepType.AUTOMATIC,
                    command="true",
                    outputs=["missing.txt"],
                )
            ],
            file_hashes={"missing.txt": "a" * 64},
        )
        rep = Reproducer(tmp_path / "x.rpk", strict=True)
        with pytest.raises(RuntimeError, match="missing after reproduction"):
            rep._verify_outputs(manifest, tmp_path)

    def test_verify_outputs_no_captured_hash(self, tmp_path: Path) -> None:
        from repropack.core.manifest import EnvironmentSpec, Metadata

        manifest = ReproPackManifest(
            metadata=Metadata(name="m"),
            environment=EnvironmentSpec(base_image="python:3.11-slim@sha256:a"),
            steps=[
                Step(
                    id="s",
                    type=StepType.AUTOMATIC,
                    command="true",
                    outputs=["unknown.txt"],
                )
            ],
            file_hashes={},
        )
        rep = Reproducer(tmp_path / "x.rpk", strict=True)
        rep._verify_outputs(manifest, tmp_path)  # no captured hash -> warn, no raise

    def test_profile_in_docker_mode(
        self,
        tmp_path: Path,
        fake_subprocess: SpySubprocess,
        fake_docker: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        rpk = _make_rpk(tmp_path)
        monkeypatch.setattr("repropack.core.run.Confirm.ask", lambda x: True)
        rep = Reproducer(rpk, profile=True)
        rep.run()
        assert (rpk.parent / "reproduction-profile.json").exists()
