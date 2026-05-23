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
        default="0.1.0", description="ReproPack format version"
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
