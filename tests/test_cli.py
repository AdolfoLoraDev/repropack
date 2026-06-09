"""Integration tests for the Typer CLI commands."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from repropack.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)  # type: ignore[misc]
def _no_network_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "repropack.core.capture.get_base_image_digest",
        lambda img: f"{img}@sha256:fakedigest",
    )


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "main.py").write_text("print(1)\n")
    (project / "requirements.txt").write_text("numpy\n")
    return project


def _capture(tmp_path: Path) -> Path:
    out = tmp_path / "p.rpk"
    result = runner.invoke(
        app, ["capture", "-p", str(_project(tmp_path)), "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    return out


class TestVersion:
    def test_version(self) -> None:
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert "ReproPack" in result.output


class TestCaptureInspectValidate:
    def test_capture_and_inspect(self, tmp_path: Path) -> None:
        rpk = _capture(tmp_path)
        assert rpk.exists()
        result = runner.invoke(app, ["inspect", str(rpk)])
        assert result.exit_code == 0
        assert "proj" in result.output

    def test_validate(self, tmp_path: Path) -> None:
        rpk = _capture(tmp_path)
        result = runner.invoke(app, ["validate", str(rpk)])
        assert result.exit_code == 0
        assert "valid" in result.output.lower()

    def test_capture_invalid_container(self, tmp_path: Path) -> None:
        out = tmp_path / "x.rpk"
        result = runner.invoke(
            app,
            ["capture", "-p", str(_project(tmp_path)), "-o", str(out), "-c", "bogus"],
        )
        assert result.exit_code == 1


class TestGraph:
    @pytest.mark.parametrize("fmt", ["mermaid", "dot", "html", "provxml"])
    def test_graph_formats(self, tmp_path: Path, fmt: str) -> None:
        rpk = _capture(tmp_path)
        out = tmp_path / f"graph.{fmt}"
        result = runner.invoke(app, ["graph", str(rpk), "-f", fmt, "-o", str(out)])
        assert result.exit_code == 0, result.output
        assert out.exists()
        assert out.stat().st_size > 0


class TestExport:
    def test_export_lists(self, tmp_path: Path) -> None:
        rpk = _capture(tmp_path)
        result = runner.invoke(app, ["export", str(rpk)])
        assert result.exit_code == 0
        assert "citation" in result.output

    def test_export_citation(self, tmp_path: Path) -> None:
        rpk = _capture(tmp_path)
        out = tmp_path / "CITATION.cff"
        result = runner.invoke(
            app, ["export", str(rpk), "-e", "citation", "-o", str(out)]
        )
        assert result.exit_code == 0
        assert out.exists()


class TestDiff:
    def test_diff_identical(self, tmp_path: Path) -> None:
        rpk = _capture(tmp_path)
        copy = tmp_path / "copy.rpk"
        copy.write_bytes(rpk.read_bytes())
        result = runner.invoke(app, ["diff", str(rpk), str(copy)])
        assert result.exit_code == 0
        assert "equivalent" in result.output.lower()


class TestPublish:
    def test_publish_citation(self, tmp_path: Path) -> None:
        rpk = _capture(tmp_path)
        result = runner.invoke(app, ["publish", str(rpk), "--to", "citation"])
        assert result.exit_code == 0
        assert "CITATION.cff" in result.output


# =====================================================================
# Coverage: error paths and branches
# =====================================================================


def _corrupt_rpk(tmp_path: Path) -> Path:
    """A package whose stored file hashes do not match its files."""
    import zipfile

    from repropack.core.manifest import (
        EnvironmentSpec,
        Metadata,
        ReproPackManifest,
        Step,
        StepType,
    )

    manifest = ReproPackManifest(
        metadata=Metadata(name="m"),
        environment=EnvironmentSpec(base_image="python:3.11-slim@sha256:a"),
        steps=[Step(id="s", type=StepType.AUTOMATIC, command="true")],
        file_hashes={"main.py": "0" * 64},
    )
    rpk = tmp_path / "corrupt.rpk"
    with zipfile.ZipFile(rpk, "w") as zf:
        zf.writestr("repropack.yml", manifest.to_yaml())
        zf.writestr("Dockerfile", "FROM python\n")
        zf.writestr("provenance.json", "{}")
        zf.writestr("project/main.py", "print('changed')\n")
    return rpk


class TestCaptureManualStep:
    def test_capture_with_manual_step(self, tmp_path: Path) -> None:
        out = tmp_path / "m.rpk"
        result = runner.invoke(
            app,
            [
                "capture",
                "-p",
                str(_project(tmp_path)),
                "-o",
                str(out),
                "-m",
                "Review the results",
            ],
        )
        assert result.exit_code == 0, result.output


class TestRunCommand:
    def test_run_lite(self, tmp_path: Path) -> None:
        rpk = _capture(tmp_path)
        result = runner.invoke(app, ["run", str(rpk), "--lite"])
        assert result.exit_code == 0, result.output

    def test_run_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        rpk = _capture(tmp_path)

        def _boom(*a: object, **k: object) -> None:
            raise RuntimeError("kaboom")

        monkeypatch.setattr("repropack.cli.run_package", _boom)
        result = runner.invoke(app, ["run", str(rpk), "--lite"])
        assert result.exit_code == 1
        assert "kaboom" in result.output


class TestGraphBranches:
    def test_graph_unsupported_format(self, tmp_path: Path) -> None:
        rpk = _capture(tmp_path)
        result = runner.invoke(
            app, ["graph", str(rpk), "-f", "bogus", "-o", str(tmp_path / "g")]
        )
        assert result.exit_code == 1
        assert "Unsupported" in result.output

    def test_graph_png(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        rpk = _capture(tmp_path)

        class _FakeSource:
            def __init__(self, dot: str) -> None:
                self.dot = dot

            def render(self, *a: object, **k: object) -> str:
                return "rendered"

        import graphviz

        monkeypatch.setattr(graphviz, "Source", _FakeSource)
        result = runner.invoke(
            app, ["graph", str(rpk), "-f", "png", "-o", str(tmp_path / "g.png")]
        )
        assert result.exit_code == 0, result.output


class TestInspectError:
    def test_inspect_bad_file(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.rpk"
        bad.write_text("not a zip")
        result = runner.invoke(app, ["inspect", str(bad)])
        assert result.exit_code == 1
        assert "Error" in result.output


class TestValidateBranches:
    def test_validate_invalid(self, tmp_path: Path) -> None:
        rpk = _corrupt_rpk(tmp_path)
        result = runner.invoke(app, ["validate", str(rpk)])
        assert result.exit_code == 1
        assert "failed" in result.output.lower()

    def test_validate_exception(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rpk = _capture(tmp_path)

        def _boom(*a: object, **k: object) -> None:
            raise RuntimeError("explode")

        monkeypatch.setattr("repropack.cli.validate_package", _boom)
        result = runner.invoke(app, ["validate", str(rpk)])
        assert result.exit_code == 1


class TestPublishBranches:
    def test_publish_with_url(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rpk = _capture(tmp_path)
        monkeypatch.setattr(
            "repropack.core.publish.publish_package",
            lambda *a, **k: {"citation": "CITATION.cff", "url": "https://z/1"},
        )
        result = runner.invoke(app, ["publish", str(rpk), "--to", "zenodo"])
        assert result.exit_code == 0
        assert "Deposited" in result.output

    def test_publish_error(self, tmp_path: Path) -> None:
        rpk = _capture(tmp_path)
        result = runner.invoke(
            app, ["publish", str(rpk), "--to", "osf", "--token", "x"]
        )
        assert result.exit_code == 1


class TestDiffBranches:
    def test_diff_with_differences(self, tmp_path: Path) -> None:
        project = _project(tmp_path)
        rpk_a = tmp_path / "a.rpk"
        runner.invoke(
            app,
            ["capture", "-p", str(project), "-o", str(rpk_a), "-b", "python:3.10-slim"],
        )
        rpk_b = tmp_path / "b.rpk"
        runner.invoke(
            app,
            ["capture", "-p", str(project), "-o", str(rpk_b), "-b", "python:3.12-slim"],
        )
        result = runner.invoke(app, ["diff", str(rpk_a), str(rpk_b)])
        assert result.exit_code == 0
        assert "Base image" in result.output

    def test_diff_error(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.rpk"
        bad.write_text("not a zip")
        other = _capture(tmp_path)
        result = runner.invoke(app, ["diff", str(bad), str(other)])
        assert result.exit_code == 1


class TestExportBranches:
    def test_export_requires_output(self, tmp_path: Path) -> None:
        rpk = _capture(tmp_path)
        result = runner.invoke(app, ["export", str(rpk), "-e", "citation"])
        assert result.exit_code == 1
        assert "output is required" in result.output

    def test_export_error(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.rpk"
        bad.write_text("not a zip")
        result = runner.invoke(
            app, ["export", str(bad), "-e", "citation", "-o", str(tmp_path / "o")]
        )
        assert result.exit_code == 1


class TestMain:
    def test_main_invokes_app(self, monkeypatch: pytest.MonkeyPatch) -> None:
        called: dict[str, bool] = {}
        monkeypatch.setattr("repropack.cli.app", lambda: called.setdefault("ok", True))
        from repropack.cli import main

        main()
        assert called["ok"]


# =====================================================================
# sign / verify / run --fetch-data
# =====================================================================


class TestSignVerifyCli:
    def test_sign_attestation(self, tmp_path: Path) -> None:
        rpk = _capture(tmp_path)
        result = runner.invoke(app, ["sign", str(rpk)])
        assert result.exit_code == 0
        assert "Attestation" in result.output
        # verify it
        result = runner.invoke(app, ["verify", str(rpk)])
        assert result.exit_code == 0
        assert "succeeded" in result.output

    def test_verify_failure(self, tmp_path: Path) -> None:
        rpk = _capture(tmp_path)
        runner.invoke(app, ["sign", str(rpk)])
        rpk.write_bytes(b"tampered")
        result = runner.invoke(app, ["verify", str(rpk)])
        assert result.exit_code == 1
        assert "failed" in result.output.lower()

    def test_sign_cosign(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        rpk = _capture(tmp_path)
        from repropack.core import sign as sign_mod

        monkeypatch.setattr(sign_mod.shutil, "which", lambda x: "/usr/bin/cosign")
        monkeypatch.setattr(sign_mod.subprocess, "run", lambda cmd, **k: None)
        result = runner.invoke(app, ["sign", str(rpk), "--cosign"])
        assert result.exit_code == 0
        assert "Signature" in result.output

    def test_sign_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        rpk = _capture(tmp_path)
        from repropack.core import sign as sign_mod

        monkeypatch.setattr(sign_mod.shutil, "which", lambda x: None)
        result = runner.invoke(app, ["sign", str(rpk), "--cosign"])
        assert result.exit_code == 1

    def test_verify_cosign_requires_args(self, tmp_path: Path) -> None:
        rpk = _capture(tmp_path)
        result = runner.invoke(app, ["verify", str(rpk), "--cosign"])
        assert result.exit_code == 1
        assert "requires" in result.output

    def test_verify_cosign_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rpk = _capture(tmp_path)
        bundle = tmp_path / "s.bundle"
        bundle.write_text("bundle")
        key = tmp_path / "pub.key"
        key.write_text("key")
        from repropack.core import sign as sign_mod

        monkeypatch.setattr(sign_mod, "verify_with_cosign", lambda *a, **k: True)
        result = runner.invoke(
            app,
            [
                "verify",
                str(rpk),
                "--cosign",
                "--bundle",
                str(bundle),
                "--key",
                str(key),
            ],
        )
        assert result.exit_code == 0
        assert "succeeded" in result.output


class TestRunFetchData:
    def test_run_fetch_data_no_manifest(self, tmp_path: Path) -> None:
        rpk = _capture(tmp_path)
        result = runner.invoke(app, ["run", str(rpk), "--lite", "--fetch-data"])
        assert result.exit_code == 0
        assert "nothing to fetch" in result.output
