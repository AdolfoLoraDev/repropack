"""Tests for block 1-6: determinism, DAG, user manifest, I/O, secrets, git."""

from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path
from typing import Any

import pytest

from repropack.core import capture as cap
from repropack.core import gitinfo, secrets
from repropack.core.capture import capture_project
from repropack.core.manifest import (
    EnvironmentSpec,
    GitInfo,
    Metadata,
    ReproPackManifest,
    Step,
    StepType,
    topological_order,
)
from repropack.core.run import Reproducer
from repropack.core.validate import validate_package


@pytest.fixture(autouse=True)  # type: ignore[misc]
def _no_network_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "repropack.core.capture.get_base_image_digest",
        lambda img: f"{img}@sha256:fakedigest",
    )


def _project(tmp_path: Path, name: str = "proj") -> Path:
    project = tmp_path / name
    project.mkdir()
    (project / "main.py").write_text("print(1)\n")
    (project / "requirements.txt").write_text("numpy\n")
    return project


# =====================================================================
# 1. Deterministic packaging
# =====================================================================


class TestDeterministicPackaging:
    def test_source_date_epoch_parsing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
        assert cap._source_date_epoch() is None
        monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")
        assert cap._source_date_epoch() == 1700000000
        monkeypatch.setenv("SOURCE_DATE_EPOCH", "not-a-number")
        assert cap._source_date_epoch() is None

    def test_zip_date_time(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
        assert cap._zip_date_time() == (1980, 1, 1, 0, 0, 0)
        monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")
        assert cap._zip_date_time()[0] >= 2023

    def test_resolve_created_at_epoch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")
        assert cap._resolve_created_at().year >= 2023

    def test_identical_bytes_with_epoch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")
        project = _project(tmp_path)
        a = tmp_path / "a.rpk"
        b = tmp_path / "b.rpk"
        capture_project(project, a)
        capture_project(project, b)
        assert a.read_bytes() == b.read_bytes()


# =====================================================================
# 2. Topological order + DAG validation
# =====================================================================


def _auto(sid: str, deps: list[str] | None = None) -> Step:
    return Step(
        id=sid,
        type=StepType.AUTOMATIC,
        command="true",
        depends_on=deps or [],
    )


class TestTopologicalOrder:
    def test_deps_come_first(self) -> None:
        steps = [_auto("c", ["b"]), _auto("b", ["a"]), _auto("a")]
        order = [s.id for s in topological_order(steps)]
        assert order.index("a") < order.index("b") < order.index("c")

    def test_no_deps_preserves_order(self) -> None:
        steps = [_auto("a"), _auto("b"), _auto("c")]
        assert [s.id for s in topological_order(steps)] == ["a", "b", "c"]

    def test_cycle_detected(self) -> None:
        steps = [_auto("a", ["b"]), _auto("b", ["a"])]
        with pytest.raises(ValueError, match="cycle"):
            topological_order(steps)

    def test_unknown_dependency(self) -> None:
        steps = [_auto("a", ["ghost"])]
        with pytest.raises(ValueError, match="unknown step 'ghost'"):
            topological_order(steps)


class TestDagValidation:
    def _pack(self, tmp_path: Path, steps: list[Step]) -> Path:
        manifest = ReproPackManifest(
            metadata=Metadata(name="m"),
            environment=EnvironmentSpec(base_image="python:3.11-slim@sha256:a"),
            steps=steps,
        )
        rpk = tmp_path / "p.rpk"
        with zipfile.ZipFile(rpk, "w") as zf:
            zf.writestr("repropack.yml", manifest.to_yaml())
            zf.writestr("Dockerfile", "FROM python\n")
            zf.writestr("provenance.json", "{}")
            zf.writestr("project/main.py", "x\n")
        return rpk

    def test_validate_flags_cycle(self, tmp_path: Path) -> None:
        rpk = self._pack(tmp_path, [_auto("a", ["b"]), _auto("b", ["a"])])
        result = validate_package(rpk)
        assert not result.valid
        assert any("dependency graph" in e for e in result.errors)

    def test_validate_flags_unknown_dep(self, tmp_path: Path) -> None:
        rpk = self._pack(tmp_path, [_auto("a", ["ghost"])])
        result = validate_package(rpk)
        assert any("unknown step" in e for e in result.errors)


# =====================================================================
# 3. User-authored repropack.yml
# =====================================================================


class TestUserManifest:
    def test_user_steps_and_metadata_respected(self, tmp_path: Path) -> None:
        project = _project(tmp_path)
        user = ReproPackManifest(
            metadata=Metadata(
                name="custom-name", authors=["Ada"], description="hand-written"
            ),
            environment=EnvironmentSpec(
                base_image="python:3.12-slim", system_packages=["libfoo"]
            ),
            steps=[
                Step(
                    id="custom",
                    type=StepType.AUTOMATIC,
                    command="echo custom",
                    outputs=["out.txt"],
                )
            ],
        )
        (project / "repropack.yml").write_text(user.to_yaml())

        output = tmp_path / "p.rpk"
        capture_project(project, output)
        with zipfile.ZipFile(output, "r") as zf:
            manifest = ReproPackManifest.from_yaml(
                zf.read("repropack.yml").decode("utf-8")
            )

        assert manifest.metadata.name == "custom-name"
        assert manifest.metadata.authors == ["Ada"]
        assert manifest.metadata.description == "hand-written"
        assert [s.id for s in manifest.steps] == ["custom"]
        assert "libfoo" in manifest.environment.system_packages
        assert manifest.environment.base_image.startswith("python:3.12-slim@sha256:")

    def test_invalid_user_manifest_falls_back(self, tmp_path: Path) -> None:
        project = _project(tmp_path)
        (project / "train.py").write_text("print('t')\n")
        (project / "repropack.yml").write_text("this: : is not : valid yaml: [")

        output = tmp_path / "p.rpk"
        capture_project(project, output)
        with zipfile.ZipFile(output, "r") as zf:
            manifest = ReproPackManifest.from_yaml(
                zf.read("repropack.yml").decode("utf-8")
            )
        # Falls back to inference -> the train step is auto-detected.
        assert any(s.id == "train" for s in manifest.steps)

    def test_base_image_override_beats_user(self, tmp_path: Path) -> None:
        project = _project(tmp_path)
        user = ReproPackManifest(
            metadata=Metadata(name="x"),
            environment=EnvironmentSpec(base_image="python:3.9-slim"),
            steps=[],
        )
        (project / "repropack.yml").write_text(user.to_yaml())
        output = tmp_path / "p.rpk"
        capture_project(project, output, base_image="python:3.11-slim")
        with zipfile.ZipFile(output, "r") as zf:
            manifest = ReproPackManifest.from_yaml(
                zf.read("repropack.yml").decode("utf-8")
            )
        assert manifest.environment.base_image.startswith("python:3.11-slim@")


# =====================================================================
# 4. Runtime input/output validation
# =====================================================================


def _pack_runnable(tmp_path: Path, steps: list[Step]) -> Path:
    manifest = ReproPackManifest(
        metadata=Metadata(name="m"),
        environment=EnvironmentSpec(base_image="python:3.11-slim@sha256:a"),
        steps=steps,
    )
    rpk = tmp_path / "p.rpk"
    with zipfile.ZipFile(rpk, "w") as zf:
        zf.writestr("repropack.yml", manifest.to_yaml())
        zf.writestr("Dockerfile", "FROM python\n")
        zf.writestr("provenance.json", "{}")
        zf.writestr("project/main.py", "x\n")
    return rpk


class TestIoValidation:
    def test_missing_input_warned(self, tmp_path: Path) -> None:
        rpk = _pack_runnable(
            tmp_path,
            [
                Step(
                    id="s", type=StepType.AUTOMATIC, command="true", inputs=["nope.csv"]
                )
            ],
        )
        rep = Reproducer(rpk, lite=True)
        rep.run()
        assert any("missing input nope.csv" in line for line in rep._report_lines)

    def test_missing_output_warned(self, tmp_path: Path) -> None:
        rpk = _pack_runnable(
            tmp_path,
            [
                Step(
                    id="s",
                    type=StepType.AUTOMATIC,
                    command="true",
                    outputs=["never.bin"],
                )
            ],
        )
        rep = Reproducer(rpk, lite=True)
        rep.run()
        assert any(
            "output not produced never.bin" in line for line in rep._report_lines
        )


# =====================================================================
# 5. Secret scanning
# =====================================================================


class TestSecrets:
    def test_is_secret_by_name(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text("API_KEY=x\n")
        (tmp_path / "id_rsa").write_text("key\n")
        (tmp_path / "cert.pem").write_text("cert\n")
        assert secrets.is_secret_file(tmp_path, ".env")
        assert secrets.is_secret_file(tmp_path, "id_rsa")
        assert secrets.is_secret_file(tmp_path, "cert.pem")

    def test_is_secret_by_content(self, tmp_path: Path) -> None:
        (tmp_path / "config.txt").write_text("-----BEGIN RSA PRIVATE KEY-----\nabc\n")
        assert secrets.is_secret_file(tmp_path, "config.txt")

    def test_not_secret(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("print(1)\n")
        assert not secrets.is_secret_file(tmp_path, "main.py")

    def test_unreadable_file_is_not_secret(self, tmp_path: Path) -> None:
        # A path that does not exist -> stat/read raises -> not a secret.
        assert not secrets.is_secret_file(tmp_path, "ghost.bin")

    def test_scan_secrets(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text("x\n")
        (tmp_path / "main.py").write_text("y\n")
        assert secrets.scan_secrets(tmp_path, [".env", "main.py"]) == [".env"]

    def test_capture_excludes_secrets(self, tmp_path: Path) -> None:
        # id_rsa is not in the ignore list, so this exercises secret scanning.
        project = _project(tmp_path)
        (project / "id_rsa").write_text("PRIVATE KEY\n")
        output = tmp_path / "p.rpk"
        capture_project(project, output)
        with zipfile.ZipFile(output, "r") as zf:
            names = zf.namelist()
        assert "project/id_rsa" not in names
        assert "project/main.py" in names

    def test_allow_secrets_keeps_them(self, tmp_path: Path) -> None:
        project = _project(tmp_path)
        (project / "id_rsa").write_text("PRIVATE KEY\n")
        output = tmp_path / "p.rpk"
        capture_project(project, output, allow_secrets=True)
        with zipfile.ZipFile(output, "r") as zf:
            names = zf.namelist()
        assert "project/id_rsa" in names


# =====================================================================
# 6. Git provenance
# =====================================================================


def _git_repo(tmp_path: Path) -> Path:
    import os

    project = _project(tmp_path, "gitproj")
    env = {
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "HOME": str(tmp_path),
        "PATH": os.environ["PATH"],
    }

    def _run(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(project), *args],
            check=True,
            capture_output=True,
            env=env,
        )

    _run("init")
    _run("config", "user.email", "t@t.co")
    _run("config", "user.name", "tester")
    _run("add", "-A")
    _run("-c", "commit.gpgsign=false", "commit", "-m", "init")
    return project


class TestGitProvenance:
    def test_get_git_info_no_git(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gitinfo.shutil, "which", lambda x: None)
        assert gitinfo.get_git_info(Path("/tmp")) is None

    def test_get_git_info_not_a_repo(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(gitinfo.shutil, "which", lambda x: "/usr/bin/git")
        monkeypatch.setattr(gitinfo, "_git", lambda *a, **k: None)
        assert gitinfo.get_git_info(tmp_path) is None

    def test_git_cmd_failure_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(*a: Any, **k: Any) -> None:
            raise FileNotFoundError

        monkeypatch.setattr(gitinfo.subprocess, "run", _boom)
        assert gitinfo._git(tmp_path, "status") is None

    def test_real_repo_info(self, tmp_path: Path) -> None:
        project = _git_repo(tmp_path)
        info = gitinfo.get_git_info(project)
        assert info is not None
        assert len(info.commit) == 40
        assert info.dirty is False

    def test_capture_records_git(self, tmp_path: Path) -> None:
        project = _git_repo(tmp_path)
        output = tmp_path / "p.rpk"
        capture_project(project, output)
        with zipfile.ZipFile(output, "r") as zf:
            manifest = ReproPackManifest.from_yaml(
                zf.read("repropack.yml").decode("utf-8")
            )
            prov = zf.read("provenance.json").decode("utf-8")
        assert manifest.metadata.git is not None
        assert "source_revision" in prov or "git_revision" in prov

    def test_inspect_shows_git(self, tmp_path: Path) -> None:
        from repropack.core.inspect import inspect_package

        manifest = ReproPackManifest(
            metadata=Metadata(
                name="m",
                git=GitInfo(
                    commit="a" * 40, branch="main", remote="git@x:repo", dirty=True
                ),
            ),
            environment=EnvironmentSpec(base_image="python:3.11-slim@sha256:a"),
        )
        rpk = tmp_path / "g.rpk"
        with zipfile.ZipFile(rpk, "w") as zf:
            zf.writestr("repropack.yml", manifest.to_yaml())
        inspect_package(rpk)  # exercises the git metadata rows

    def test_provenance_includes_git_entity(self) -> None:
        from repropack.core.provenance import ProvenanceGraph

        manifest = ReproPackManifest(
            metadata=Metadata(
                name="m",
                git=GitInfo(commit="a" * 40, branch="main", remote="git@x", dirty=True),
            ),
            environment=EnvironmentSpec(base_image="python:3.11-slim@sha256:a"),
        )
        graph = ProvenanceGraph()
        graph.build_from_manifest(manifest)
        assert "git_revision" in graph.to_json()
