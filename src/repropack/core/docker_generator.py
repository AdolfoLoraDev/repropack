"""Strict Dockerfile generation for reproducible environments."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from repropack.core.manifest import EnvironmentSpec

# Default base image. The digest is resolved at capture time via
# ``get_base_image_digest`` (docker inspect or registry API); we avoid
# hard-coding a fake/stale digest here.
DEFAULT_PYTHON_IMAGE = "python:3.11-slim"


def _command_exists(cmd: str) -> bool:
    """Check whether a command is available on PATH."""
    import shutil

    return shutil.which(cmd) is not None


def _inspect_with_docker(image: str) -> str | None:
    """Try to resolve a digest via ``docker inspect``."""
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format={{index .RepoDigests 0}}", image],
            capture_output=True,
            text=True,
            check=True,
        )
        digest = result.stdout.strip()
        if digest and "@sha256:" in digest:
            return digest
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return None


def _inspect_via_registry_api(image: str) -> str | None:
    """Query Docker Hub API for the digest of a public image.

    Supports ``library/<image>:<tag>`` and ``<user>/<image>:<tag>``.
    Returns ``None`` for private repos or network errors.
    """
    # Parse image string:  name:tag or name@sha256:...
    if "@sha256:" in image:
        return image  # Already pinned

    if ":" in image:
        name, tag = image.rsplit(":", 1)
    else:
        name, tag = image, "latest"

    # Normalize namespace
    repo = f"library/{name}" if "/" not in name else name

    try:
        import urllib.request

        url = f"https://hub.docker.com/v2/repositories/{repo}/tags/{tag}/"
        with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))

        images = data.get("images", [])
        if images:
            digest = images[0].get("digest", "")
            if digest.startswith("sha256:"):
                return f"{name}:{tag}@{digest}"
    except Exception:  # noqa: BLE001,S110
        pass

    return None


def get_base_image_digest(image: str) -> str:
    """Resolve the SHA256 digest for a Docker base image.

    Tries, in order:
    1. ``docker inspect`` if the Docker CLI is available.
    2. Docker Hub public API for official/community images.
    3. Return the original string unchanged (may already contain a digest).

    Args:
        image: Docker image reference, e.g. ``python:3.11-slim``.

    Returns:
        Image string with digest when available.
    """
    if "@sha256:" in image:
        return image

    # 1. Local docker daemon
    digest = _inspect_with_docker(image)
    if digest:
        return digest

    # 2. Registry API fallback
    digest = _inspect_via_registry_api(image)
    if digest:
        return digest

    return image


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

    # R ecosystem: install R + renv and restore the locked library
    if env.r_renv:
        renv_path = Path(env.r_renv).name
        lines.extend(
            [
                "# Install R and restore the renv library",
                "RUN apt-get update && apt-get install -y --no-install-recommends \\",
                "    r-base && \\",
                "    rm -rf /var/lib/apt/lists/*",
                "RUN R -e \"install.packages('renv', repos='https://cloud.r-project.org')\"",  # noqa: E501
                f"COPY {env.r_renv} {workdir}/{renv_path}",
                f"RUN R -e \"renv::restore(lockfile='{workdir}/{renv_path}')\"",
                "",
            ]
        )

    # Julia ecosystem: install Julia and instantiate the project
    if env.julia_project:
        proj_path = Path(env.julia_project).name
        manifest_name = "Manifest.toml"
        lines.extend(
            [
                "# Install Julia and instantiate the project",
                "ENV JULIA_VERSION=1.10.4",
                "RUN apt-get update && apt-get install -y --no-install-recommends \\",
                "    curl ca-certificates && \\",
                "    rm -rf /var/lib/apt/lists/* && \\",
                "    curl -fsSL "
                '"https://julialang-s3.julialang.org/bin/linux/x64/'
                '${JULIA_VERSION%.*}/julia-${JULIA_VERSION}-linux-x86_64.tar.gz"'
                " | tar -xz -C /opt && \\",
                "    ln -s /opt/julia-${JULIA_VERSION}/bin/julia /usr/local/bin/julia",
                f"COPY {env.julia_project} {workdir}/{proj_path}",
                f"COPY {manifest_name} {workdir}/{manifest_name}",
                f"RUN julia --project={workdir} -e " '"using Pkg; Pkg.instantiate()"',
                "",
            ]
        )

    # Copy project files (skip dependency artifacts already COPYed above to
    # avoid duplicate COPY instructions).
    if project_files:
        already_copied = {
            Path(p).name
            for p in (
                env.python_requirements,
                env.conda_environment,
                env.r_renv,
                env.julia_project,
                "Manifest.toml" if env.julia_project else None,
            )
            if p
        }
        lines.append("# Copy project files")
        for f in project_files:
            # Use normpath to avoid path issues
            safe = Path(f).as_posix()
            if Path(safe).name in already_copied:
                continue
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
            'CMD ["echo", "Use repropack run to execute the defined steps"]',
        ]
    )

    return "\n".join(lines)


def write_dockerfile(content: str, path: Path) -> None:
    """Write the Dockerfile to disk."""
    path.write_text(content, encoding="utf-8")
