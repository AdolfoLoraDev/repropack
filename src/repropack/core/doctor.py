"""Environment diagnostics: report which optional tooling is available."""

from __future__ import annotations

import importlib.util
import shutil
from dataclasses import dataclass


@dataclass
class Check:
    """Result of a single diagnostic check."""

    name: str
    ok: bool
    detail: str


# (display name, command, what it unlocks)
_CLI_CHECKS = [
    ("Docker", "docker", "container builds"),
    ("Apptainer", "apptainer", "HPC container builds"),
    ("Singularity", "singularity", "HPC container builds (legacy)"),
    ("Graphviz (dot)", "dot", "PNG provenance graphs"),
    ("Git", "git", "source provenance capture"),
    ("pip-compile", "pip-compile", "hashed pip lockfiles"),
    ("conda-lock", "conda-lock", "conda lockfiles"),
    ("cosign", "cosign", "sigstore package signing"),
]

# (display name, importable module, what it unlocks)
_MODULE_CHECKS = [
    ("lxml", "lxml", "PROV-XML export"),
    ("boto3", "boto3", "S3 dataset fetching"),
]


def _module_available(module: str) -> bool:
    """Whether ``module`` can be imported without importing it."""
    return importlib.util.find_spec(module) is not None


def diagnose() -> list[Check]:
    """Run all diagnostic checks.

    Returns:
        A list of :class:`Check` results (CLIs first, then Python modules).
    """
    checks: list[Check] = []
    for name, cmd, why in _CLI_CHECKS:
        path = shutil.which(cmd)
        checks.append(
            Check(name, path is not None, path or f"not found — needed for {why}")
        )
    for name, module, why in _MODULE_CHECKS:
        ok = _module_available(module)
        checks.append(
            Check(name, ok, "available" if ok else f"not installed — needed for {why}")
        )
    return checks
