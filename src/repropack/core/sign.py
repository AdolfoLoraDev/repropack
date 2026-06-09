"""Package signing and attestation for ``.rpk`` integrity.

Two mechanisms are provided:

1. **SHA256 attestation** (always available): a small JSON document recording
   the package digest, size and timestamp. Verifiable offline with no extra
   tooling.
2. **cosign signatures** (optional): if `cosign <https://github.com/sigstore/cosign>`_
   is installed, the package can be signed/verified as a blob with sigstore.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ATTESTATION_SUFFIX = ".attestation.json"


def _sha256_file(path: Path) -> str:
    """Compute the SHA256 of a file."""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def attest_package(rpk_path: Path, output: Path | None = None) -> Path:
    """Write a SHA256 attestation document for a ``.rpk`` package.

    Args:
        rpk_path: Path to the ``.rpk`` file.
        output: Optional explicit output path (defaults to
            ``<rpk><ATTESTATION_SUFFIX>``).

    Returns:
        Path to the written attestation JSON.

    Raises:
        FileNotFoundError: If the package does not exist.
    """
    if not rpk_path.exists():
        raise FileNotFoundError(f"Package not found: {rpk_path}")
    attestation: dict[str, Any] = {
        "package": rpk_path.name,
        "algorithm": "sha256",
        "sha256": _sha256_file(rpk_path),
        "size_bytes": rpk_path.stat().st_size,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    target = output or rpk_path.with_name(rpk_path.name + ATTESTATION_SUFFIX)
    target.write_text(json.dumps(attestation, indent=2), encoding="utf-8")
    return target


def verify_attestation(rpk_path: Path, attestation: Path | None = None) -> bool:
    """Verify a ``.rpk`` package against its SHA256 attestation.

    Args:
        rpk_path: Path to the ``.rpk`` file.
        attestation: Optional explicit attestation path.

    Returns:
        ``True`` if the digest matches.

    Raises:
        FileNotFoundError: If the package or attestation is missing.
        ValueError: If the digest does not match the attestation.
    """
    if not rpk_path.exists():
        raise FileNotFoundError(f"Package not found: {rpk_path}")
    att_path = attestation or rpk_path.with_name(rpk_path.name + ATTESTATION_SUFFIX)
    if not att_path.exists():
        raise FileNotFoundError(f"Attestation not found: {att_path}")

    data = json.loads(att_path.read_text(encoding="utf-8"))
    expected = data.get("sha256")
    actual = _sha256_file(rpk_path)
    if actual != expected:
        raise ValueError(
            f"Attestation mismatch for {rpk_path.name}: "
            f"expected {expected}, got {actual}"
        )
    return True


def cosign_available() -> bool:
    """Whether the ``cosign`` CLI is on PATH."""
    return shutil.which("cosign") is not None


def sign_with_cosign(rpk_path: Path, key: str | None = None) -> Path:
    """Sign a ``.rpk`` blob with cosign, producing a ``.bundle`` file.

    Uses cosign's ``--bundle`` output (the format required by cosign v3+, and
    supported by v2), which packages the signature and verification material
    together.

    Args:
        rpk_path: Path to the ``.rpk`` file.
        key: Optional cosign private key path (omit for keyless/OIDC signing).

    Returns:
        Path to the signature bundle file.

    Raises:
        RuntimeError: If cosign is unavailable or signing fails.
    """
    if not cosign_available():
        raise RuntimeError(
            "cosign is not installed or not in PATH; use SHA256 attestation "
            "instead (omit --cosign)."
        )
    bundle_path = rpk_path.with_name(rpk_path.name + ".bundle")
    cmd = ["cosign", "sign-blob", "--yes", "--bundle", str(bundle_path)]
    if key:
        cmd += ["--key", key]
    cmd.append(str(rpk_path))
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"cosign signing failed: {exc}") from exc
    return bundle_path


def verify_with_cosign(
    rpk_path: Path,
    bundle: Path,
    key: str,
) -> bool:
    """Verify a cosign blob signature bundle.

    Args:
        rpk_path: Path to the ``.rpk`` file.
        bundle: Path to the ``.bundle`` produced by :func:`sign_with_cosign`.
        key: cosign public key path.

    Returns:
        ``True`` if verification succeeds.

    Raises:
        RuntimeError: If cosign is unavailable or verification fails.
    """
    if not cosign_available():
        raise RuntimeError("cosign is not installed or not in PATH.")
    cmd = [
        "cosign",
        "verify-blob",
        "--key",
        key,
        "--bundle",
        str(bundle),
        str(rpk_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"cosign verification failed: {exc}") from exc
    return True
