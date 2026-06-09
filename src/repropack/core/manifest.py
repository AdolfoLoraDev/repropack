"""Pydantic models for the repropack.yml manifest."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator


class StepType(str, Enum):
    """Step type in the manifest."""

    AUTOMATIC = "automatic"
    MANUAL = "manual"


class GitInfo(BaseModel):
    """Version-control provenance of the captured project."""

    commit: str = Field(..., description="Git commit SHA at capture time")
    branch: str | None = Field(default=None, description="Current branch name")
    remote: str | None = Field(default=None, description="origin remote URL")
    dirty: bool = Field(
        default=False, description="Whether the working tree had uncommitted changes"
    )


class Metadata(BaseModel):
    """Experiment metadata."""

    name: str = Field(..., description="Experiment name")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Creation timestamp in UTC",
    )
    authors: list[str] = Field(default_factory=list, description="Project authors")
    description: str | None = Field(
        default=None, description="Short experiment description"
    )
    git: GitInfo | None = Field(
        default=None, description="Git provenance of the captured project"
    )


class EnvironmentSpec(BaseModel):
    """Execution environment specification."""

    base_image: str = Field(
        ...,
        description="Docker base image with digest, e.g. python:3.11-slim@sha256:...",
    )
    python_requirements: str | None = Field(
        default=None, description="Path to requirements.lock"
    )
    conda_environment: str | None = Field(
        default=None, description="Path to frozen environment.yml"
    )
    r_renv: str | None = Field(
        default=None, description="Path to renv.lock for the R ecosystem"
    )
    julia_project: str | None = Field(
        default=None,
        description="Path to Julia Project.toml (Manifest.toml staged alongside)",
    )
    system_packages: list[str] = Field(
        default_factory=list, description="System packages to install"
    )
    custom_dockerfile: str | None = Field(
        default=None, description="Custom Dockerfile if available"
    )


class Step(BaseModel):
    """Individual reproduction step."""

    id: str = Field(..., description="Unique step identifier")
    type: StepType = Field(..., description="Step type: automatic or manual")
    command: str | None = Field(
        default=None, description="Command to run (automatic only)"
    )
    description: str | None = Field(default=None, description="Step description")
    instructions: str | None = Field(
        default=None, description="Detailed instructions (manual only)"
    )
    language: str | None = Field(
        default=None,
        description="Inferred language/runtime (python, r, julia, octave, etc.)",
    )
    inputs: list[str] = Field(
        default_factory=list, description="Input files or directories"
    )
    outputs: list[str] = Field(
        default_factory=list, description="Output files or directories"
    )
    depends_on: list[str] = Field(
        default_factory=list, description="IDs of previous steps this depends on"
    )

    @model_validator(mode="after")
    def _validate_step(self) -> Step:
        """Cross-field validation based on step type."""
        if self.type == StepType.AUTOMATIC and not self.command:
            raise ValueError("Automatic steps require a 'command'")
        if (
            self.type == StepType.MANUAL
            and not self.instructions
            and not self.description
        ):
            raise ValueError("Manual steps require 'instructions' or 'description'")
        return self


class ReproPackManifest(BaseModel):
    """Full manifest for a reproducible package."""

    repropack_version: str = Field(
        default="0.1.1", description="ReproPack format version"
    )
    metadata: Metadata = Field(..., description="Experiment metadata")
    environment: EnvironmentSpec = Field(..., description="Environment specification")
    steps: list[Step] = Field(
        default_factory=list, description="Ordered reproduction steps"
    )
    file_hashes: dict[str, str] = Field(
        default_factory=dict, description="SHA256 hashes of project files"
    )
    extra: dict[str, Any] = Field(
        default_factory=dict, description="Free-form extra fields"
    )

    def to_yaml(self) -> str:
        """Serialize manifest to YAML."""
        return str(
            yaml.safe_dump(
                self.model_dump(mode="json"),
                sort_keys=False,
                allow_unicode=True,
            )
        )

    @classmethod
    def from_yaml(cls, text: str) -> ReproPackManifest:
        """Deserialize manifest from YAML."""
        data = yaml.safe_load(text)
        return cls.model_validate(data)

    @classmethod
    def from_file(cls, path: Path) -> ReproPackManifest:
        """Load manifest from file."""
        return cls.from_yaml(path.read_text(encoding="utf-8"))

    def to_file(self, path: Path) -> None:
        """Save manifest to file."""
        path.write_text(self.to_yaml(), encoding="utf-8")


def topological_order(steps: list[Step]) -> list[Step]:
    """Return steps ordered so every step follows its ``depends_on`` deps.

    Performs a depth-first topological sort, preserving the original order as a
    tie-breaker for independent steps.

    Args:
        steps: The manifest steps.

    Returns:
        The steps in dependency-respecting execution order.

    Raises:
        ValueError: If a dependency references an unknown step id, or the
            dependency graph contains a cycle.
    """
    by_id: dict[str, Step] = {s.id: s for s in steps}
    state: dict[str, int] = {}  # 0 = visiting, 1 = done
    order: list[Step] = []

    def _visit(step_id: str, path: list[str]) -> None:
        current = state.get(step_id)
        if current == 1:
            return
        if current == 0:
            cycle = " -> ".join([*path, step_id])
            raise ValueError(f"Dependency cycle detected: {cycle}")
        state[step_id] = 0
        for dep in by_id[step_id].depends_on:
            if dep not in by_id:
                raise ValueError(f"Step '{step_id}' depends on unknown step '{dep}'")
            _visit(dep, [*path, step_id])
        state[step_id] = 1
        order.append(by_id[step_id])

    for step in steps:
        _visit(step.id, [])
    return order
