"""Validation logic for .rpk packages."""

from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from repropack.core.manifest import ReproPackManifest


@dataclass
class ValidationResult:
    """Result of validating a ``.rpk`` package."""

    valid: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_error(self, msg: str) -> None:
        """Append an error message."""
        self.errors.append(msg)

    def add_warning(self, msg: str) -> None:
        """Append a warning message."""
        self.warnings.append(msg)


def validate_package(rpk_path: Path) -> ValidationResult:
    """Validate the internal structure, schema and integrity of a ``.rpk``.

    Checks performed:
    1. The file is a valid ZIP archive.
    2. It contains ``repropack.yml``, ``Dockerfile``, ``provenance.json``
       and a ``project/`` directory.
    3. The manifest YAML is valid according to the Pydantic schema.
    4. All files listed in ``file_hashes`` exist inside ``project/`` and
       their SHA256 digests match.

    Args:
        rpk_path: Path to the ``.rpk`` file.

    Returns:
        A :class:`ValidationResult` with ``valid=True`` when no errors
        were found.
    """
    result = ValidationResult()
    rpk_path = rpk_path.resolve()

    if not rpk_path.exists():
        result.add_error(f"Package not found: {rpk_path}")
        return result

    # 1. ZIP structure
    try:
        with zipfile.ZipFile(rpk_path, "r") as zf:
            namelist = zf.namelist()

            required = {"repropack.yml", "Dockerfile", "provenance.json"}
            missing = required - set(namelist)
            if missing:
                result.add_error(f"Missing required files in archive: {missing}")

            if not any(n.startswith("project/") for n in namelist):
                result.add_error("Missing 'project/' directory in archive")

            # 2. Manifest schema
            try:
                manifest_text = zf.read("repropack.yml").decode("utf-8")
                manifest = ReproPackManifest.from_yaml(manifest_text)
            except Exception as exc:
                result.add_error(f"Invalid manifest (schema validation failed): {exc}")
                manifest = None

            # 3. File hash verification
            if manifest and manifest.file_hashes:
                for rel_path, expected_hash in manifest.file_hashes.items():
                    arcname = f"project/{rel_path}"
                    if arcname not in namelist:
                        result.add_error(f"Hash check: missing file {rel_path}")
                        continue
                    data = zf.read(arcname)
                    actual_hash = hashlib.sha256(data).hexdigest()
                    if actual_hash != expected_hash:
                        result.add_error(
                            f"Hash mismatch for {rel_path}: "
                            f"expected {expected_hash}, got {actual_hash}"
                        )
            elif manifest:
                result.add_warning(
                    "No file_hashes in manifest; integrity check skipped"
                )

            # 4. Verify the Dockerfile provides every required runtime
            if manifest and "Dockerfile" in namelist:
                dockerfile = zf.read("Dockerfile").decode("utf-8")
                _check_runtimes(manifest, dockerfile, result)

            # 5. Detect editable installs in requirements
            if manifest and manifest.environment.python_requirements:
                req_arc = manifest.environment.python_requirements
                if req_arc in namelist:
                    req_text = zf.read(req_arc).decode("utf-8")
                    if "-e " in req_text or "--editable" in req_text:
                        result.add_warning(
                            "Lockfile contains editable installs "
                            "(-e .); reproduction may be fragile"
                        )

    except zipfile.BadZipFile:
        result.add_error("File is not a valid ZIP archive")
    except Exception as exc:
        result.add_error(f"Unexpected validation error: {exc}")

    result.valid = len(result.errors) == 0
    return result


# Map a step language to a token expected in the Dockerfile and the
# human-readable runtime name used in the warning message.
_RUNTIME_TOKENS: dict[str, tuple[str, str]] = {
    "r": ("r-base", "R"),
    "julia": ("julia", "Julia"),
    "octave": ("octave", "Octave"),
    "cmake": ("cmake", "CMake"),
}


def _check_runtimes(
    manifest: ReproPackManifest,
    dockerfile: str,
    result: ValidationResult,
) -> None:
    """Warn when a step's language has no matching runtime in the Dockerfile.

    Args:
        manifest: Parsed manifest with language-tagged steps.
        dockerfile: Dockerfile contents.
        result: Validation result to append warnings to.
    """
    languages = {step.language for step in manifest.steps if step.language is not None}
    for lang in sorted(languages):
        token = _RUNTIME_TOKENS.get(lang)
        if token is None:
            continue
        needle, runtime_name = token
        if needle not in dockerfile:
            result.add_warning(
                f"Steps require {runtime_name} but the Dockerfile does not "
                f"install it (missing '{needle}'); reproduction may fail"
            )
