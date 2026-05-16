"""Strict Dockerfile generation for reproducible environments."""

from __future__ import annotations

from pathlib import Path

from repropack.core.manifest import EnvironmentSpec

# Default base image with digest for strict reproducibility
DEFAULT_PYTHON_IMAGE = (
    "python:3.11-slim@sha256:"
    "fc39e3f1c15fb2fe18205cd4d04718f82baab4f57212a8cb4a04a4a\n"
    # Note: the hash above is a placeholder; in production it would be resolved
    # dynamically against the Docker Hub registry.
)


def resolve_base_image(spec: EnvironmentSpec | None = None) -> str:
    """Resolve the base image, preferably with SHA256 digest.

    Args:
        spec: Environment specification from the manifest.

    Returns:
        Docker image string with digest.
    """
    if spec and spec.base_image:
        return spec.base_image
    return DEFAULT_PYTHON_IMAGE


def generate_dockerfile(
    env: EnvironmentSpec,
    workdir: str = "/workspace",
    project_files: list[str] | None = None,
) -> str:
    """Generate a strict Dockerfile from a specification.

    The Dockerfile includes:
    - Base image with SHA256 digest.
    - System package installation.
    - Lockfile and requirement copying.
    - Installation with --require-hashes when possible.
    - Project code copying.
    - Generic entrypoint.

    Args:
        env: Environment specification.
        workdir: Working directory inside the container.
        project_files: List of relative paths to copy into the container.

    Returns:
        Dockerfile content as a string.
    """
    lines: list[str] = [
        f"FROM {env.base_image}",
        "",
        "# Avoid interactive prompts during installation",
        "ENV DEBIAN_FRONTEND=noninteractive",
        "",
        "# Create non-root user for security",
        "RUN groupadd -r repro && useradd -r -g repro repro",
        "",
        f"WORKDIR {workdir}",
        "",
    ]

    # System packages
    if env.system_packages:
        pkgs = " ".join(env.system_packages)
        lines.extend(
            [
                "# Install system packages",
                "RUN apt-get update && apt-get install -y --no-install-recommends \\",
                f"    {pkgs} && \\",
                "    rm -rf /var/lib/apt/lists/*",
                "",
            ]
        )

    # Python: requirements.lock with --require-hashes
    if env.python_requirements:
        req_path = Path(env.python_requirements).name
        lines.extend(
            [
                "# Install Python dependencies with mandatory hashes",
                f"COPY {env.python_requirements} {workdir}/{req_path}",
                "RUN pip install --no-cache-dir --require-hashes "
                f"-r {workdir}/{req_path}",
                "",
            ]
        )

    # Conda: environment.yml
    if env.conda_environment:
        conda_path = Path(env.conda_environment).name
        lines.extend(
            [
                "# Install Conda environment",
                f"COPY {env.conda_environment} {workdir}/{conda_path}",
                "RUN conda env update -n base -f "
                f"{workdir}/{conda_path} && conda clean -afy",
                "",
            ]
        )

    # Copy project files
    if project_files:
        lines.append("# Copy project files")
        for f in project_files:
            # Use normpath to avoid path issues
            safe = Path(f).as_posix()
            lines.append(f"COPY {safe} {workdir}/{safe}")
        lines.append("")

    # Permissions and user
    lines.extend(
        [
            f"RUN chown -R repro:repro {workdir}",
            "USER repro",
            "",
            f'ENV PYTHONPATH="{workdir}"',
            "",
            "# Entrypoint: manifest should define the CMD",
            '["echo", "Use repropack run to execute the defined steps"]',
        ]
    )

    return "\n".join(lines)


def write_dockerfile(content: str, path: Path) -> None:
    """Write the Dockerfile to disk."""
    path.write_text(content, encoding="utf-8")
