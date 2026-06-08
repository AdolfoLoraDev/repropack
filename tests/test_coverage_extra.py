"""Final coverage-closing tests for residual branches."""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

import pytest

from repropack.core import plugins
from repropack.core.apptainer_generator import generate_apptainer_def
from repropack.core.manifest import (
    EnvironmentSpec,
    Metadata,
    ReproPackManifest,
    Step,
    StepType,
)
from repropack.core.run import Reproducer
from repropack.core.validate import validate_package


def _write_rpk(
    tmp_path: Path,
    manifest: ReproPackManifest,
    *,
    name: str = "p.rpk",
    extra_files: dict[str, str] | None = None,
    project_files: dict[str, str] | None = None,
) -> Path:
    rpk = tmp_path / name
    with zipfile.ZipFile(rpk, "w") as zf:
        zf.writestr("repropack.yml", manifest.to_yaml())
        zf.writestr("Dockerfile", "FROM python:3.11-slim\n")
        zf.writestr("provenance.json", "{}")
        for path, content in (extra_files or {}).items():
            zf.writestr(path, content)
        for path, content in (project_files or {}).items():
            zf.writestr(f"project/{path}", content)
    return rpk


def _manifest(**env: Any) -> ReproPackManifest:
    base: dict[str, Any] = {"base_image": "python:3.11-slim@sha256:a"}
    base.update(env)
    return ReproPackManifest(
        metadata=Metadata(name="m"),
        environment=EnvironmentSpec(**base),
        steps=[Step(id="s", type=StepType.AUTOMATIC, command="true")],
    )


# =====================================================================
# validate.py residual branches
# =====================================================================


class TestValidateResidual:
    def test_no_file_hashes_warning(self, tmp_path: Path) -> None:
        rpk = _write_rpk(tmp_path, _manifest(), project_files={"main.py": "x\n"})
        result = validate_package(rpk)
        assert any("No file_hashes" in w for w in result.warnings)

    def test_hash_check_missing_file_in_archive(self, tmp_path: Path) -> None:
        manifest = _manifest()
        manifest.file_hashes = {"ghost.py": "a" * 64}
        rpk = _write_rpk(tmp_path, manifest, project_files={"main.py": "x\n"})
        result = validate_package(rpk)
        assert any("missing file ghost.py" in e for e in result.errors)

    def test_unexpected_validation_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        manifest = _manifest()
        manifest.file_hashes = {"main.py": "a" * 64}
        rpk = _write_rpk(tmp_path, manifest, project_files={"main.py": "x\n"})

        import repropack.core.validate as vmod

        def _boom(_data: bytes) -> Any:
            raise RuntimeError("hash engine exploded")

        monkeypatch.setattr(vmod.hashlib, "sha256", _boom)
        result = validate_package(rpk)
        assert any("Unexpected validation error" in e for e in result.errors)


# =====================================================================
# run.py residual branches
# =====================================================================


class TestRunResidual:
    def test_validate_package_missing_hashed_file(self, tmp_path: Path) -> None:
        manifest = _manifest()
        manifest.file_hashes = {"ghost.py": "a" * 64}
        rpk = _write_rpk(tmp_path, manifest, project_files={"main.py": "x\n"})
        extract = tmp_path / "ex"
        with zipfile.ZipFile(rpk, "r") as zf:
            zf.extractall(extract)
        rep = Reproducer(rpk)
        with pytest.raises(ValueError, match="missing file ghost.py"):
            rep._validate_package(extract)

    def test_select_backend_docker_requested_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("repropack.core.run.shutil.which", lambda x: None)
        rep = Reproducer(tmp_path / "x.rpk", container="docker")
        with pytest.raises(RuntimeError, match="Docker is not installed"):
            rep._select_backend(tmp_path)

    def test_select_backend_docker_requested_ok(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("repropack.core.run.shutil.which", lambda x: "/bin/docker")
        rep = Reproducer(tmp_path / "x.rpk", container="docker")
        assert rep._select_backend(tmp_path) == "docker"

    def test_select_backend_apptainer_requested_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("repropack.core.run.shutil.which", lambda x: None)
        rep = Reproducer(tmp_path / "x.rpk", container="apptainer")
        with pytest.raises(
            RuntimeError, match="Apptainer/Singularity is not installed"
        ):
            rep._select_backend(tmp_path)

    def test_verify_outputs_no_declared(self, tmp_path: Path) -> None:
        manifest = _manifest()  # step has no outputs
        rep = Reproducer(tmp_path / "x.rpk", strict=True)
        rep._verify_outputs(manifest, tmp_path)  # prints "nothing to verify"

    def test_manual_step_with_outputs(self, tmp_path: Path) -> None:
        manifest = ReproPackManifest(
            metadata=Metadata(name="m"),
            environment=EnvironmentSpec(base_image="python:3.11-slim@sha256:a"),
            steps=[
                Step(
                    id="curate",
                    type=StepType.MANUAL,
                    instructions="curate",
                    outputs=["data/clean.csv"],
                )
            ],
        )
        rpk = _write_rpk(tmp_path, manifest)
        rep = Reproducer(rpk, lite=True, skip_manual=True)
        rep.run()
        assert any("skipped" in line for line in rep._report_lines)

    def test_lite_package_mismatch_warnings(self, tmp_path: Path) -> None:
        # 11 pins that cannot all match -> exercises the ">10 more" branch,
        # plus a real-but-mis-pinned package to hit the version-compare branch.
        lock = "\n".join(f"pkg{i}==0.0.{i}" for i in range(11)) + "\npytest==0.0.1\n"
        import sys

        py = f"python:{sys.version_info.major}.{sys.version_info.minor}-slim"
        manifest = ReproPackManifest(
            metadata=Metadata(name="m"),
            environment=EnvironmentSpec(
                base_image=f"{py}@sha256:a", python_requirements="requirements.lock"
            ),
            steps=[Step(id="s", type=StepType.AUTOMATIC, command="true")],
        )
        rpk = _write_rpk(
            tmp_path,
            manifest,
            extra_files={"requirements.lock": lock},
            project_files={"main.py": "x\n"},
        )
        rep = Reproducer(rpk, lite=True)
        rep.run()
        assert any("differ from lockfile" in line for line in rep._report_lines)


# =====================================================================
# plugins.py entry-point loading
# =====================================================================


class TestPluginEntryPoints:
    def test_entry_point_loading(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _EP:
            def __init__(self, name: str, fail: bool = False) -> None:
                self.name = name
                self._fail = fail

            def load(self) -> Any:
                if self._fail:
                    raise ImportError("broken plugin")
                return lambda rpk, out: out

        from importlib import metadata

        monkeypatch.setattr(
            metadata,
            "entry_points",
            lambda group: [_EP("ep-good"), _EP("ep-bad", fail=True)],
        )
        monkeypatch.setattr(plugins, "_ENTRY_POINTS_LOADED", False)
        names = plugins.list_exporters()
        assert "ep-good" in names
        assert "ep-bad" not in names


# =====================================================================
# provenance.to_png and capture conda+requirements fallback
# =====================================================================


class TestProvenancePng:
    def test_to_png(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from repropack.core.provenance import ProvenanceGraph

        graph = ProvenanceGraph()
        graph.build_from_manifest(_manifest())

        class _FakeSource:
            def __init__(self, dot: str) -> None:
                pass

            def render(self, *a: Any, **k: Any) -> str:
                return "x"

        import graphviz

        monkeypatch.setattr(graphviz, "Source", _FakeSource)
        graph.to_png(tmp_path / "g.png")


class TestCaptureCondaRequirementsFallback:
    def test_conda_project_with_requirements_txt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "repropack.core.capture.get_base_image_digest",
            lambda img: f"{img}@sha256:fake",
        )
        # Make conda lock generation a no-op that returns None so the
        # requirements.txt fallback path is exercised.
        monkeypatch.setattr(
            "repropack.core.capture.generate_conda_lock", lambda *a, **k: None
        )
        from repropack.core.capture import capture_project

        project = tmp_path / "conda_proj"
        project.mkdir()
        (project / "environment.yml").write_text("name: env\n")
        (project / "requirements.txt").write_text("numpy\n")
        out = tmp_path / "c.rpk"
        capture_project(project, out)
        with zipfile.ZipFile(out, "r") as zf:
            manifest = ReproPackManifest.from_yaml(
                zf.read("repropack.yml").decode("utf-8")
            )
        assert manifest.environment.python_requirements == "requirements.txt"


# =====================================================================
# Residual pure-function branches
# =====================================================================


class TestSmallBranches:
    def test_parse_data_refs_empty_source(self) -> None:
        from repropack.core.data import parse_data_refs

        with pytest.raises(ValueError, match="both path and source"):
            parse_data_refs(["path="])

    def test_read_lockfile_versions_none(self, tmp_path: Path) -> None:
        from repropack.core.diff import _read_lockfile_versions

        rpk = _write_rpk(tmp_path, _manifest())
        assert _read_lockfile_versions(rpk, None) == {}

    def test_read_lockfile_versions_absent(self, tmp_path: Path) -> None:
        from repropack.core.diff import _read_lockfile_versions

        rpk = _write_rpk(tmp_path, _manifest())
        assert _read_lockfile_versions(rpk, "nope.lock") == {}

    def test_read_lockfile_versions_parses_pins(self, tmp_path: Path) -> None:
        from repropack.core.diff import _read_lockfile_versions

        rpk = _write_rpk(
            tmp_path,
            _manifest(),
            extra_files={"requirements.lock": "numpy==1.26.0\n# comment\nbad-line\n"},
        )
        assert _read_lockfile_versions(rpk, "requirements.lock") == {"numpy": "1.26.0"}

    def test_registry_api_no_tag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from repropack.core import docker_generator as dg

        payload = '{"images": [{"digest": "sha256:zzz"}]}'

        class _Resp:
            def __enter__(self) -> _Resp:
                return self

            def __exit__(self, *a: Any) -> None:
                return None

            def read(self) -> bytes:
                return payload.encode()

        import urllib.request

        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
        # No colon -> defaults to ":latest" (covers that branch).
        assert dg._inspect_via_registry_api("python") == "python:latest@sha256:zzz"

    def test_apptainer_skips_dep_artifacts(self) -> None:
        env = EnvironmentSpec(
            base_image="python:3.11-slim", python_requirements="requirements.lock"
        )
        text = generate_apptainer_def(
            env, project_files=["requirements.lock", "main.py"]
        )
        # requirements.lock copied once (in the deps block), not again below.
        assert text.count("requirements.lock /workspace/requirements.lock") == 1

    def test_conda_lock_tool_raises_then_export(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess

        from repropack.utils import environment as env_mod

        monkeypatch.setattr(env_mod, "_command_exists", lambda cmd: cmd == "conda-lock")

        calls = {"n": 0}

        def _run(cmd: Any, **k: Any) -> Any:
            calls["n"] += 1
            if calls["n"] == 1:  # conda-lock invocation fails
                raise subprocess.CalledProcessError(1, "conda-lock")

            class _R:
                stdout = "name: base\n"
                stderr = ""
                returncode = 0

            return _R()

        monkeypatch.setattr(env_mod.subprocess, "run", _run)
        out = env_mod.generate_conda_lock(tmp_path, tmp_path / "c.yml")
        assert "name: base" in out.read_text()

    def test_mermaid_has_association_edge(self) -> None:
        from repropack.core.provenance import ProvenanceGraph

        graph = ProvenanceGraph()
        graph.build_from_manifest(_manifest())
        mermaid = graph.to_mermaid()
        # step_s --> repropack_system (wasAssociatedWith)
        assert "step_s --> repropack_system" in mermaid

    def test_provxml_missing_lxml(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import sys

        from repropack.core.provenance import ProvenanceGraph

        graph = ProvenanceGraph()
        graph.build_from_manifest(_manifest())
        # Make the provxml serializer import fail.
        monkeypatch.setitem(sys.modules, "prov.serializers.provxml", None)
        with pytest.raises(RuntimeError, match="requires 'lxml'"):
            graph.to_provxml()

    def test_run_lite_lockfile_referenced_but_absent(self, tmp_path: Path) -> None:
        # python_requirements set but the lockfile is not in the package:
        # _check_lite_environment must return early without raising.
        manifest = ReproPackManifest(
            metadata=Metadata(name="m"),
            environment=EnvironmentSpec(
                base_image="python:3.11-slim@sha256:a",
                python_requirements="requirements.lock",
            ),
            steps=[Step(id="s", type=StepType.AUTOMATIC, command="true")],
        )
        rpk = _write_rpk(tmp_path, manifest, project_files={"main.py": "x\n"})
        Reproducer(rpk, lite=True).run()


class TestValidateCliWarning:
    def test_validate_warns_via_cli(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from repropack.cli import app

        rpk = _write_rpk(tmp_path, _manifest(), project_files={"main.py": "x\n"})
        result = CliRunner().invoke(app, ["validate", str(rpk)])
        assert result.exit_code == 0
        assert "WARN" in result.output
