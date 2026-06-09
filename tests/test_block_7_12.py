"""Tests for block 7-12 (ignore files, determinism, doctor, report, migrate)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from repropack.core import doctor
from repropack.core.capture import capture_project
from repropack.core.docker_generator import generate_dockerfile
from repropack.core.manifest import (
    EnvironmentSpec,
    Metadata,
    ReproPackManifest,
    Step,
    StepType,
)
from repropack.core.migrate import CURRENT_FORMAT_VERSION, migrate_package
from repropack.core.run import Reproducer
from repropack.utils.environment import _load_ignore_files, list_project_files


@pytest.fixture(autouse=True)  # type: ignore[misc]
def _no_network_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "repropack.core.capture.get_base_image_digest",
        lambda img: f"{img}@sha256:fakedigest",
    )


# =====================================================================
# 7. Ignore files
# =====================================================================


class TestIgnoreFiles:
    def test_load_ignore_files(self, tmp_path: Path) -> None:
        (tmp_path / ".repropackignore").write_text(
            "# comment\n\nbuild/\n*.log\n!keep.log\n"
        )
        ignore, negate = _load_ignore_files(tmp_path)
        assert "build" in ignore
        assert "*.log" in ignore
        assert "keep.log" in negate

    def test_repropackignore_excludes(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("x\n")
        (tmp_path / "scratch.tmp").write_text("junk\n")
        (tmp_path / ".repropackignore").write_text("*.tmp\n")
        files = list_project_files(tmp_path)
        assert "main.py" in files
        assert "scratch.tmp" not in files

    def test_gitignore_excludes(self, tmp_path: Path) -> None:
        (tmp_path / "keep.py").write_text("x\n")
        (tmp_path / "out.bin").write_text("y\n")
        (tmp_path / ".gitignore").write_text("out.bin\n")
        files = list_project_files(tmp_path)
        assert "out.bin" not in files

    def test_negation_reincludes(self, tmp_path: Path) -> None:
        (tmp_path / "a.log").write_text("x\n")
        (tmp_path / "keep.log").write_text("y\n")
        (tmp_path / ".repropackignore").write_text("*.log\n!keep.log\n")
        files = list_project_files(tmp_path)
        assert "a.log" not in files
        assert "keep.log" in files

    def test_no_ignore_files(self, tmp_path: Path) -> None:
        assert _load_ignore_files(tmp_path) == ([], [])

    def test_empty_pattern_after_strip_ignored(self, tmp_path: Path) -> None:
        # A bare "/" collapses to an empty pattern and must be skipped.
        (tmp_path / ".repropackignore").write_text("/\n")
        assert _load_ignore_files(tmp_path) == ([], [])


# =====================================================================
# 8. Deterministic environment + platform
# =====================================================================


class TestEnvDeterminism:
    def test_dockerfile_has_determinism_env(self) -> None:
        env = EnvironmentSpec(base_image="python:3.11-slim")
        df = generate_dockerfile(env)
        assert "PYTHONHASHSEED=0" in df
        assert "TZ=UTC" in df
        assert "LC_ALL=C.UTF-8" in df

    def test_capture_default_platform(self, tmp_path: Path) -> None:
        project = tmp_path / "p"
        project.mkdir()
        (project / "main.py").write_text("x\n")
        out = tmp_path / "p.rpk"
        capture_project(project, out)
        with zipfile.ZipFile(out) as zf:
            manifest = ReproPackManifest.from_yaml(zf.read("repropack.yml").decode())
        assert manifest.environment.platform == "linux/amd64"

    def test_capture_platform_override(self, tmp_path: Path) -> None:
        project = tmp_path / "p"
        project.mkdir()
        (project / "main.py").write_text("x\n")
        out = tmp_path / "p.rpk"
        capture_project(project, out, platform="linux/arm64")
        with zipfile.ZipFile(out) as zf:
            manifest = ReproPackManifest.from_yaml(zf.read("repropack.yml").decode())
        assert manifest.environment.platform == "linux/arm64"

    def test_build_docker_passes_platform(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr("repropack.core.run.shutil.which", lambda x: "/bin/docker")
        monkeypatch.setattr(
            "repropack.core.run.subprocess.run",
            lambda cmd, **k: calls.append(cmd),
        )
        rep = Reproducer(tmp_path / "x.rpk")
        rep._platform = "linux/arm64"
        df = tmp_path / "Dockerfile"
        df.write_text("FROM python\n")
        ctx = tmp_path / "ctx"
        ctx.mkdir()
        rep._build_docker(df, ctx, "img")
        assert any("--platform=linux/arm64" in c for c in calls[0])


# =====================================================================
# 9. doctor
# =====================================================================


class TestDoctor:
    def test_diagnose_returns_checks(self) -> None:
        checks = doctor.diagnose()
        names = {c.name for c in checks}
        assert "Docker" in names
        assert "lxml" in names

    def test_diagnose_respects_availability(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(doctor.shutil, "which", lambda cmd: None)
        monkeypatch.setattr(doctor, "_module_available", lambda mod: True)
        checks = {c.name: c for c in doctor.diagnose()}
        assert checks["Docker"].ok is False
        assert checks["lxml"].ok is True

    def test_module_available(self) -> None:
        assert doctor._module_available("json") is True
        assert doctor._module_available("nonexistent_xyz_module") is False


# =====================================================================
# 10. Structured run report
# =====================================================================


def _pack(tmp_path: Path, steps: list[Step], name: str = "p.rpk") -> Path:
    manifest = ReproPackManifest(
        metadata=Metadata(name="m"),
        environment=EnvironmentSpec(base_image="python:3.11-slim@sha256:a"),
        steps=steps,
    )
    rpk = tmp_path / name
    with zipfile.ZipFile(rpk, "w") as zf:
        zf.writestr("repropack.yml", manifest.to_yaml())
        zf.writestr("Dockerfile", "FROM python\n")
        zf.writestr("provenance.json", "{}")
        zf.writestr("project/main.py", "x\n")
    return rpk


class TestRunReport:
    def test_report_written(self, tmp_path: Path) -> None:
        rpk = _pack(
            tmp_path,
            [
                Step(
                    id="s",
                    type=StepType.AUTOMATIC,
                    command="true",
                    outputs=["missing.bin"],
                )
            ],
        )
        report = tmp_path / "run-report.json"
        Reproducer(rpk, lite=True, report=report).run()
        assert report.exists()
        data = json.loads(report.read_text())
        assert data["steps"][0]["id"] == "s"
        assert data["steps"][0]["missing_outputs"] == ["missing.bin"]
        assert any("not produced" in w for w in data["warnings"])

    def test_no_report_by_default(self, tmp_path: Path) -> None:
        rpk = _pack(tmp_path, [Step(id="s", type=StepType.AUTOMATIC, command="true")])
        rep = Reproducer(rpk, lite=True)
        rep.run()
        assert not (tmp_path / "run-report.json").exists()

    def test_write_run_report_noop_when_unset(self, tmp_path: Path) -> None:
        rep = Reproducer(tmp_path / "x.rpk")
        rep._write_run_report()  # report is None -> no-op, no error


# =====================================================================
# 11. Partial runs
# =====================================================================


class TestPartialRuns:
    def test_only_runs_subset(self, tmp_path: Path) -> None:
        rpk = _pack(
            tmp_path,
            [
                Step(id="a", type=StepType.AUTOMATIC, command="true"),
                Step(id="b", type=StepType.AUTOMATIC, command="true"),
            ],
        )
        rep = Reproducer(rpk, lite=True, only=["a"])
        rep.run()
        assert [r["id"] for r in rep._step_records] == ["a"]

    def test_from_runs_tail(self, tmp_path: Path) -> None:
        rpk = _pack(
            tmp_path,
            [
                Step(id="a", type=StepType.AUTOMATIC, command="true"),
                Step(id="b", type=StepType.AUTOMATIC, command="true"),
                Step(id="c", type=StepType.AUTOMATIC, command="true"),
            ],
        )
        rep = Reproducer(rpk, lite=True, from_step="b")
        rep.run()
        assert [r["id"] for r in rep._step_records] == ["b", "c"]

    def test_only_unknown_raises(self, tmp_path: Path) -> None:
        rpk = _pack(tmp_path, [Step(id="a", type=StepType.AUTOMATIC, command="true")])
        with pytest.raises(RuntimeError, match="--only references unknown"):
            Reproducer(rpk, lite=True, only=["ghost"]).run()

    def test_from_unknown_raises(self, tmp_path: Path) -> None:
        rpk = _pack(tmp_path, [Step(id="a", type=StepType.AUTOMATIC, command="true")])
        with pytest.raises(RuntimeError, match="--from references unknown"):
            Reproducer(rpk, lite=True, from_step="ghost").run()

    def test_only_and_from_conflict(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="only one of"):
            Reproducer(tmp_path / "x.rpk", only=["a"], from_step="b")


# =====================================================================
# 12. Format migration
# =====================================================================


class TestMigrate:
    def _old_package(self, tmp_path: Path, version_str: str) -> Path:
        manifest = ReproPackManifest(
            repropack_version=version_str,
            metadata=Metadata(name="m"),
            environment=EnvironmentSpec(base_image="python:3.11-slim@sha256:a"),
            steps=[Step(id="s", type=StepType.AUTOMATIC, command="true")],
        )
        rpk = tmp_path / "old.rpk"
        with zipfile.ZipFile(rpk, "w") as zf:
            zf.writestr("repropack.yml", manifest.to_yaml())
            zf.writestr("Dockerfile", "FROM python\n")
            zf.writestr("provenance.json", "{}")
            zf.writestr("project/main.py", "x\n")
        return rpk

    def test_migrate_upgrades(self, tmp_path: Path) -> None:
        rpk = self._old_package(tmp_path, "0.1.0")
        out = tmp_path / "new.rpk"
        result = migrate_package(rpk, out)
        assert result["from"] == "0.1.0"
        assert result["to"] == CURRENT_FORMAT_VERSION
        with zipfile.ZipFile(out) as zf:
            manifest = ReproPackManifest.from_yaml(zf.read("repropack.yml").decode())
        assert manifest.repropack_version == CURRENT_FORMAT_VERSION

    def test_migrate_already_current(self, tmp_path: Path) -> None:
        rpk = self._old_package(tmp_path, CURRENT_FORMAT_VERSION)
        result = migrate_package(rpk)  # in place
        assert result["from"] == result["to"] == CURRENT_FORMAT_VERSION

    def test_migrate_newer_rejected(self, tmp_path: Path) -> None:
        rpk = self._old_package(tmp_path, "99.0.0")
        with pytest.raises(ValueError, match="newer than supported"):
            migrate_package(rpk)


# =====================================================================
# CLI wiring for doctor / migrate / run flags
# =====================================================================


class TestCliWiring:
    def test_doctor_command(self) -> None:
        from typer.testing import CliRunner

        from repropack.cli import app

        result = CliRunner().invoke(app, ["doctor"])
        assert result.exit_code == 0
        assert "Docker" in result.output

    def test_migrate_command(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from repropack.cli import app

        manifest = ReproPackManifest(
            repropack_version="0.1.0",
            metadata=Metadata(name="m"),
            environment=EnvironmentSpec(base_image="python:3.11-slim@sha256:a"),
        )
        rpk = tmp_path / "old.rpk"
        with zipfile.ZipFile(rpk, "w") as zf:
            zf.writestr("repropack.yml", manifest.to_yaml())
        out = tmp_path / "new.rpk"
        result = CliRunner().invoke(app, ["migrate", str(rpk), "-o", str(out)])
        assert result.exit_code == 0
        assert "Migrated" in result.output

    def test_migrate_already_current_cli(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from repropack.cli import app

        manifest = ReproPackManifest(
            metadata=Metadata(name="m"),
            environment=EnvironmentSpec(base_image="python:3.11-slim@sha256:a"),
        )
        rpk = tmp_path / "cur.rpk"
        with zipfile.ZipFile(rpk, "w") as zf:
            zf.writestr("repropack.yml", manifest.to_yaml())
        result = CliRunner().invoke(app, ["migrate", str(rpk)])
        assert result.exit_code == 0
        assert "Already current" in result.output

    def test_migrate_error(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from repropack.cli import app

        bad = tmp_path / "bad.rpk"
        bad.write_text("not a zip")
        result = CliRunner().invoke(app, ["migrate", str(bad)])
        assert result.exit_code == 1

    def test_run_report_flag(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from repropack.cli import app

        rpk = _pack(tmp_path, [Step(id="s", type=StepType.AUTOMATIC, command="true")])
        report = tmp_path / "r.json"
        result = CliRunner().invoke(
            app, ["run", str(rpk), "--lite", "--report", str(report)]
        )
        assert result.exit_code == 0
        assert report.exists()
