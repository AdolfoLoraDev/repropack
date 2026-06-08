"""Diff two ``.rpk`` packages: steps, environment, packages and files."""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from repropack.core.manifest import ReproPackManifest

# A pinned requirement line, e.g. "numpy==1.26.0" (ignores hashes/markers).
_REQ_RE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*==\s*([^\s;#\\]+)")


@dataclass
class PackageDiff:
    """Structured difference between two ``.rpk`` packages."""

    base_image_changed: tuple[str, str] | None = None
    steps_added: list[str] = field(default_factory=list)
    steps_removed: list[str] = field(default_factory=list)
    steps_changed: list[str] = field(default_factory=list)
    files_added: list[str] = field(default_factory=list)
    files_removed: list[str] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)
    packages_added: list[str] = field(default_factory=list)
    packages_removed: list[str] = field(default_factory=list)
    packages_changed: list[str] = field(default_factory=list)

    @property
    def identical(self) -> bool:
        """Whether the two packages are equivalent across all tracked facets."""
        return not any(
            [
                self.base_image_changed,
                self.steps_added,
                self.steps_removed,
                self.steps_changed,
                self.files_added,
                self.files_removed,
                self.files_changed,
                self.packages_added,
                self.packages_removed,
                self.packages_changed,
            ]
        )


def _read_manifest(rpk_path: Path) -> ReproPackManifest:
    with zipfile.ZipFile(rpk_path, "r") as zf:
        return ReproPackManifest.from_yaml(zf.read("repropack.yml").decode("utf-8"))


def _read_lockfile_versions(
    rpk_path: Path, lockfile_name: str | None
) -> dict[str, str]:
    """Extract ``name -> version`` pins from a lockfile inside the package."""
    if not lockfile_name:
        return {}
    versions: dict[str, str] = {}
    with zipfile.ZipFile(rpk_path, "r") as zf:
        if lockfile_name not in zf.namelist():
            return {}
        text = zf.read(lockfile_name).decode("utf-8")
    for line in text.splitlines():
        match = _REQ_RE.match(line)
        if match:
            versions[match.group(1).lower()] = match.group(2)
    return versions


def diff_packages(rpk_a: Path, rpk_b: Path) -> PackageDiff:
    """Compute the difference between two ``.rpk`` packages.

    Args:
        rpk_a: The baseline package.
        rpk_b: The package to compare against the baseline.

    Returns:
        A :class:`PackageDiff` describing additions, removals and changes.
    """
    man_a = _read_manifest(rpk_a)
    man_b = _read_manifest(rpk_b)
    diff = PackageDiff()

    # Environment
    if man_a.environment.base_image != man_b.environment.base_image:
        diff.base_image_changed = (
            man_a.environment.base_image,
            man_b.environment.base_image,
        )

    # Steps (compare by id; "changed" when type or command differs)
    steps_a = {s.id: s for s in man_a.steps}
    steps_b = {s.id: s for s in man_b.steps}
    diff.steps_added = sorted(set(steps_b) - set(steps_a))
    diff.steps_removed = sorted(set(steps_a) - set(steps_b))
    for sid in sorted(set(steps_a) & set(steps_b)):
        sa, sb = steps_a[sid], steps_b[sid]
        if sa.command != sb.command or sa.type != sb.type:
            diff.steps_changed.append(sid)

    # Files (compare by file_hashes)
    fa, fb = man_a.file_hashes, man_b.file_hashes
    diff.files_added = sorted(set(fb) - set(fa))
    diff.files_removed = sorted(set(fa) - set(fb))
    diff.files_changed = sorted(p for p in set(fa) & set(fb) if fa[p] != fb[p])

    # Packages (compare lockfile pins)
    pa = _read_lockfile_versions(rpk_a, man_a.environment.python_requirements)
    pb = _read_lockfile_versions(rpk_b, man_b.environment.python_requirements)
    diff.packages_added = sorted(set(pb) - set(pa))
    diff.packages_removed = sorted(set(pa) - set(pb))
    diff.packages_changed = sorted(
        f"{name}: {pa[name]} → {pb[name]}"
        for name in set(pa) & set(pb)
        if pa[name] != pb[name]
    )

    return diff
