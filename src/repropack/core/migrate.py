"""Migrate older ``.rpk`` packages to the current manifest format version."""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

from packaging import version

from repropack.core.capture import write_deterministic_zip
from repropack.core.manifest import ReproPackManifest

# The manifest format version this build of ReproPack writes.
CURRENT_FORMAT_VERSION = "0.1.1"


def migrate_package(rpk_path: Path, output: Path | None = None) -> dict[str, str]:
    """Upgrade a ``.rpk`` to the current manifest format version.

    The package is unpacked, its manifest's ``repropack_version`` is bumped to
    :data:`CURRENT_FORMAT_VERSION` (filling in any new defaulted fields via the
    Pydantic model), and it is repacked as a byte-reproducible archive.

    Args:
        rpk_path: Path to the ``.rpk`` to migrate.
        output: Destination path (defaults to migrating in place).

    Returns:
        A dict with the ``from`` and ``to`` version strings.

    Raises:
        ValueError: If the package is newer than this build can handle.
    """
    rpk_path = rpk_path.resolve()
    target = (output or rpk_path).resolve()

    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp) / "staging"
        staging.mkdir()
        with zipfile.ZipFile(rpk_path, "r") as zf:
            zf.extractall(staging)

        manifest = ReproPackManifest.from_file(staging / "repropack.yml")
        old_version = manifest.repropack_version
        if version.parse(old_version) > version.parse(CURRENT_FORMAT_VERSION):
            raise ValueError(
                f"Package format {old_version} is newer than supported "
                f"{CURRENT_FORMAT_VERSION}; upgrade ReproPack."
            )

        manifest.repropack_version = CURRENT_FORMAT_VERSION
        manifest.to_file(staging / "repropack.yml")

        write_deterministic_zip(staging, target)

    return {"from": old_version, "to": CURRENT_FORMAT_VERSION}
