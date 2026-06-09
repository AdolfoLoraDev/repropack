"""Tests for package signing and attestation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from repropack.core import sign


def _rpk(tmp_path: Path, content: bytes = b"package-bytes") -> Path:
    rpk = tmp_path / "p.rpk"
    rpk.write_bytes(content)
    return rpk


class TestAttestation:
    def test_attest_and_verify_roundtrip(self, tmp_path: Path) -> None:
        rpk = _rpk(tmp_path)
        att = sign.attest_package(rpk)
        assert att.exists()
        assert sign.verify_attestation(rpk) is True

    def test_attest_custom_output(self, tmp_path: Path) -> None:
        rpk = _rpk(tmp_path)
        out = tmp_path / "custom.json"
        att = sign.attest_package(rpk, output=out)
        assert att == out
        assert sign.verify_attestation(rpk, attestation=out) is True

    def test_attest_missing_package(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            sign.attest_package(tmp_path / "ghost.rpk")

    def test_verify_missing_package(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            sign.verify_attestation(tmp_path / "ghost.rpk")

    def test_verify_missing_attestation(self, tmp_path: Path) -> None:
        rpk = _rpk(tmp_path)
        with pytest.raises(FileNotFoundError, match="Attestation not found"):
            sign.verify_attestation(rpk)

    def test_verify_detects_tampering(self, tmp_path: Path) -> None:
        rpk = _rpk(tmp_path)
        sign.attest_package(rpk)
        rpk.write_bytes(b"tampered")
        with pytest.raises(ValueError, match="Attestation mismatch"):
            sign.verify_attestation(rpk)


class TestCosign:
    def test_cosign_available(self) -> None:
        assert sign.cosign_available() in (True, False)

    def test_sign_without_cosign(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sign.shutil, "which", lambda x: None)
        with pytest.raises(RuntimeError, match="cosign is not installed"):
            sign.sign_with_cosign(_rpk(tmp_path))

    def test_sign_with_cosign(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sign.shutil, "which", lambda x: "/usr/bin/cosign")
        calls: list[list[str]] = []
        monkeypatch.setattr(sign.subprocess, "run", lambda cmd, **k: calls.append(cmd))
        sig = sign.sign_with_cosign(_rpk(tmp_path), key="cosign.key")
        assert sig.name == "p.rpk.bundle"
        assert calls[0][:2] == ["cosign", "sign-blob"]
        assert "--bundle" in calls[0]
        assert "--key" in calls[0]

    def test_sign_cosign_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess

        monkeypatch.setattr(sign.shutil, "which", lambda x: "/usr/bin/cosign")

        def _boom(*a: Any, **k: Any) -> None:
            raise subprocess.CalledProcessError(1, "cosign")

        monkeypatch.setattr(sign.subprocess, "run", _boom)
        with pytest.raises(RuntimeError, match="cosign signing failed"):
            sign.sign_with_cosign(_rpk(tmp_path))

    def test_verify_cosign_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sign.shutil, "which", lambda x: None)
        with pytest.raises(RuntimeError, match="cosign is not installed"):
            sign.verify_with_cosign(_rpk(tmp_path), tmp_path / "s.bundle", "pub.key")

    def test_verify_cosign_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sign.shutil, "which", lambda x: "/usr/bin/cosign")
        monkeypatch.setattr(sign.subprocess, "run", lambda cmd, **k: None)
        assert sign.verify_with_cosign(_rpk(tmp_path), tmp_path / "s.bundle", "pub.key")

    def test_verify_cosign_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess

        monkeypatch.setattr(sign.shutil, "which", lambda x: "/usr/bin/cosign")

        def _boom(*a: Any, **k: Any) -> None:
            raise subprocess.CalledProcessError(1, "cosign")

        monkeypatch.setattr(sign.subprocess, "run", _boom)
        with pytest.raises(RuntimeError, match="cosign verification failed"):
            sign.verify_with_cosign(_rpk(tmp_path), tmp_path / "s.bundle", "pub.key")
