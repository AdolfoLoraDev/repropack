"""Targeted coverage tests for internal helpers and edge paths."""

from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path
from typing import Any

import pytest

from repropack.core import docker_generator as dg
from repropack.core.apptainer_generator import generate_apptainer_def
from repropack.core.manifest import (
    EnvironmentSpec,
    Metadata,
    ReproPackManifest,
    Step,
    StepType,
)
from repropack.utils import environment as env_mod


class _FakeResult:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


# =====================================================================
# docker_generator
# =====================================================================


class TestDockerGeneratorHelpers:
    def test_command_exists(self) -> None:
        assert dg._command_exists("ls") in (True, False)

    def test_resolve_base_image_default(self) -> None:
        assert dg.resolve_base_image(None) == dg.DEFAULT_PYTHON_IMAGE

    def test_resolve_base_image_from_spec(self) -> None:
        spec = EnvironmentSpec(base_image="python:3.12-slim@sha256:abc")
        assert dg.resolve_base_image(spec) == "python:3.12-slim@sha256:abc"

    def test_write_dockerfile(self, tmp_path: Path) -> None:
        out = tmp_path / "Dockerfile"
        dg.write_dockerfile("FROM scratch\n", out)
        assert out.read_text() == "FROM scratch\n"

    def test_inspect_with_docker_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            dg.subprocess,
            "run",
            lambda *a, **k: _FakeResult(stdout="python@sha256:deadbeef"),
        )
        assert dg._inspect_with_docker("python:3.11") == "python@sha256:deadbeef"

    def test_inspect_with_docker_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(*a: Any, **k: Any) -> None:
            raise FileNotFoundError

        monkeypatch.setattr(dg.subprocess, "run", _raise)
        assert dg._inspect_with_docker("python:3.11") is None

    def test_get_digest_already_pinned(self) -> None:
        pinned = "python:3.11@sha256:abc"
        assert dg.get_base_image_digest(pinned) == pinned

    def test_get_digest_via_docker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dg, "_inspect_with_docker", lambda img: "python@sha256:xyz")
        assert dg.get_base_image_digest("python:3.11") == "python@sha256:xyz"

    def test_get_digest_falls_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dg, "_inspect_with_docker", lambda img: None)
        monkeypatch.setattr(dg, "_inspect_via_registry_api", lambda img: None)
        assert dg.get_base_image_digest("python:3.11") == "python:3.11"

    def test_registry_api_already_pinned(self) -> None:
        assert dg._inspect_via_registry_api("x@sha256:a") == "x@sha256:a"

    def test_registry_api_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = '{"images": [{"digest": "sha256:abc123"}]}'

        class _Resp:
            def __enter__(self) -> _Resp:
                return self

            def __exit__(self, *a: Any) -> None:
                return None

            def read(self) -> bytes:
                return payload.encode()

        import urllib.request

        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
        result = dg._inspect_via_registry_api("python:3.11")
        assert result == "python:3.11@sha256:abc123"

    def test_registry_api_network_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import urllib.request

        def _boom(*a: Any, **k: Any) -> None:
            raise OSError("no network")

        monkeypatch.setattr(urllib.request, "urlopen", _boom)
        assert dg._inspect_via_registry_api("custom/img:tag") is None


# =====================================================================
# apptainer_generator (conda branch)
# =====================================================================


class TestApptainerConda:
    def test_conda_branch(self) -> None:
        env = EnvironmentSpec(
            base_image="continuumio/miniconda3",
            conda_environment="conda-lock.yml",
        )
        text = generate_apptainer_def(env, project_files=["run.py"])
        assert "conda env update" in text
        assert "run.py /workspace/run.py" in text


# =====================================================================
# environment helpers
# =====================================================================


class TestLockfileHasHashes:
    def test_with_hashes(self, tmp_path: Path) -> None:
        lock = tmp_path / "requirements.lock"
        lock.write_text("numpy==1.26.0 --hash=sha256:abc\n")
        assert env_mod.lockfile_has_hashes(lock) is True

    def test_without_hashes(self, tmp_path: Path) -> None:
        lock = tmp_path / "requirements.txt"
        lock.write_text("numpy==1.26.0\n")
        assert env_mod.lockfile_has_hashes(lock) is False

    def test_missing_file(self, tmp_path: Path) -> None:
        assert env_mod.lockfile_has_hashes(tmp_path / "nope.lock") is False


class TestEnvironmentHelpers:
    def test_get_python_version(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            env_mod.subprocess,
            "run",
            lambda *a, **k: _FakeResult(stdout="Python 3.11.0"),
        )
        assert env_mod.get_python_version() == "Python 3.11.0"

    def test_conda_lock_with_tool(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(env_mod, "_command_exists", lambda cmd: cmd == "conda-lock")
        monkeypatch.setattr(env_mod.subprocess, "run", lambda *a, **k: _FakeResult())
        out = env_mod.generate_conda_lock(tmp_path, tmp_path / "conda-lock.yml")
        assert out == tmp_path / "conda-lock.yml"

    def test_conda_lock_export_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(env_mod, "_command_exists", lambda cmd: False)
        monkeypatch.setattr(
            env_mod.subprocess,
            "run",
            lambda *a, **k: _FakeResult(stdout="name: base\n"),
        )
        out = env_mod.generate_conda_lock(tmp_path, tmp_path / "c.yml")
        assert "name: base" in out.read_text()

    def test_conda_lock_total_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(env_mod, "_command_exists", lambda cmd: False)

        def _boom(*a: Any, **k: Any) -> None:
            raise FileNotFoundError

        monkeypatch.setattr(env_mod.subprocess, "run", _boom)
        out = env_mod.generate_conda_lock(tmp_path, tmp_path / "c.yml")
        assert "failed" in out.read_text()

    def test_pip_lock_with_pip_compile(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "requirements.txt").write_text("numpy\n")
        monkeypatch.setattr(
            env_mod, "_command_exists", lambda cmd: cmd == "pip-compile"
        )
        monkeypatch.setattr(env_mod.subprocess, "run", lambda *a, **k: _FakeResult())
        out = env_mod.generate_pip_lock(tmp_path, tmp_path / "requirements.lock")
        assert out == tmp_path / "requirements.lock"

    def test_pip_lock_freeze_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(env_mod, "_command_exists", lambda cmd: False)

        def _boom(*a: Any, **k: Any) -> None:
            raise subprocess.CalledProcessError(1, "pip")

        monkeypatch.setattr(env_mod.subprocess, "run", _boom)
        out = env_mod.generate_pip_lock(tmp_path, tmp_path / "r.lock")
        assert "failed" in out.read_text()


# =====================================================================
# inspect (rich branches) and publish (zenodo/osf)
# =====================================================================


def _full_manifest() -> ReproPackManifest:
    return ReproPackManifest(
        metadata=Metadata(name="full", authors=["Ana"], description="desc"),
        environment=EnvironmentSpec(
            base_image="python:3.11-slim@sha256:abc",
            conda_environment="conda-lock.yml",
            r_renv="renv.lock",
            julia_project="Project.toml",
            system_packages=["gcc", "octave"],
        ),
        steps=[
            Step(id="auto1", type=StepType.AUTOMATIC, command="python x.py"),
            Step(
                id="manual1",
                type=StepType.MANUAL,
                instructions="do it",
                outputs=["out.txt"],
            ),
        ],
        file_hashes={"x.py": "a" * 64},
    )


class TestInspectBranches:
    def test_inspect_full_manifest(self, tmp_path: Path) -> None:
        from repropack.core.inspect import inspect_package

        rpk = tmp_path / "full.rpk"
        with zipfile.ZipFile(rpk, "w") as zf:
            zf.writestr("repropack.yml", _full_manifest().to_yaml())
            zf.writestr("project/x.py", "print(1)\n")
        inspect_package(rpk)  # exercises conda/r/julia/system + manual rows

    def test_inspect_missing_manifest(self, tmp_path: Path) -> None:
        from repropack.core.inspect import inspect_package

        rpk = tmp_path / "bad.rpk"
        with zipfile.ZipFile(rpk, "w") as zf:
            zf.writestr("note.txt", "x")
        with pytest.raises(ValueError, match="repropack.yml"):
            inspect_package(rpk)

    def test_inspect_bad_zip(self, tmp_path: Path) -> None:
        from repropack.core.inspect import inspect_package

        rpk = tmp_path / "notzip.rpk"
        rpk.write_text("not a zip")
        with pytest.raises(ValueError, match="not a valid ZIP"):
            inspect_package(rpk)

    def test_inspect_missing_file(self, tmp_path: Path) -> None:
        from repropack.core.inspect import inspect_package

        with pytest.raises(FileNotFoundError):
            inspect_package(tmp_path / "ghost.rpk")


class TestPublishZenodoOsf:
    def _rpk(self, tmp_path: Path) -> Path:
        rpk = tmp_path / "p.rpk"
        with zipfile.ZipFile(rpk, "w") as zf:
            zf.writestr("repropack.yml", _full_manifest().to_yaml())
        return rpk

    def test_parse_author_empty(self) -> None:
        from repropack.core.publish import parse_author

        assert parse_author("") == {"name": ""}

    def test_zenodo_deposit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from repropack.core import publish as pub

        class _Resp:
            def __enter__(self) -> _Resp:
                return self

            def __exit__(self, *a: Any) -> None:
                return None

            def read(self) -> bytes:
                return b'{"links": {"html": "https://zenodo.org/deposit/1"}}'

        import urllib.request

        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
        url = pub._zenodo_deposit(self._rpk(tmp_path), "tok", sandbox=True)
        assert url == "https://zenodo.org/deposit/1"

    def test_publish_zenodo_via_token(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from repropack.core import publish as pub

        monkeypatch.setattr(pub, "_zenodo_deposit", lambda *a, **k: "https://z/1")
        result = pub.publish_package(self._rpk(tmp_path), to="zenodo", token="tok")
        assert result["url"] == "https://z/1"

    def test_publish_osf_via_env_token(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from repropack.core import publish as pub

        monkeypatch.setenv("REPROPACK_OSF_TOKEN", "tok")
        monkeypatch.setattr(pub, "_osf_create_node", lambda *a, **k: "https://osf/y")
        result = pub.publish_package(self._rpk(tmp_path), to="osf")
        assert result["url"] == "https://osf/y"

    def test_zenodo_deposit_network_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import urllib.request

        from repropack.core import publish as pub

        def _boom(*a: Any, **k: Any) -> None:
            raise OSError("offline")

        monkeypatch.setattr(urllib.request, "urlopen", _boom)
        with pytest.raises(RuntimeError, match="Zenodo deposition failed"):
            pub._zenodo_deposit(self._rpk(tmp_path), "tok")
