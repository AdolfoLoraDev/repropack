"""Apptainer/Singularity definition-file generation for HPC reproducibility.

Apptainer (formerly Singularity) is the de-facto container runtime on HPC
clusters where Docker is unavailable. This module renders a ``.def`` file
equivalent to the strict Dockerfile produced by :mod:`docker_generator`.
"""

from __future__ import annotations

from pathlib import Path

from repropack.core.manifest import EnvironmentSpec


def generate_apptainer_def(
    env: EnvironmentSpec,
    workdir: str = "/workspace",
    project_files: list[str] | None = None,
    pip_require_hashes: bool = False,
) -> str:
    """Generate an Apptainer ``.def`` file from an environment specification.

    The definition bootstraps from the same Docker base image (via the
    ``docker`` bootstrap agent) and mirrors the Dockerfile install steps:
    system packages, Python/Conda/R/Julia dependencies and project files.

    Args:
        env: Environment specification.
        workdir: Working directory inside the container.
        project_files: Relative paths to copy into the image.
        pip_require_hashes: Pass ``--require-hashes`` to pip (only when the
            lockfile carries ``--hash=`` entries).

    Returns:
        Apptainer definition file content.
    """
    # The base image may carry a digest (image@sha256:...). Apptainer's docker
    # bootstrap expects the From: field without the scheme.
    base = env.base_image

    post: list[str] = [f"mkdir -p {workdir}"]
    files: list[str] = []

    if env.system_packages:
        pkgs = " ".join(env.system_packages)
        post.append("apt-get update")
        post.append(f"apt-get install -y --no-install-recommends {pkgs}")
        post.append("rm -rf /var/lib/apt/lists/*")

    if env.python_requirements:
        req = Path(env.python_requirements).name
        files.append(f"{env.python_requirements} {workdir}/{req}")
        hashes = " --require-hashes" if pip_require_hashes else ""
        post.append(f"pip install --no-cache-dir{hashes} -r {workdir}/{req}")

    if env.conda_environment:
        conda = Path(env.conda_environment).name
        files.append(f"{env.conda_environment} {workdir}/{conda}")
        post.append(
            f"conda env update -n base -f {workdir}/{conda} && conda clean -afy"
        )

    if env.r_renv:
        renv = Path(env.r_renv).name
        post.append(
            "apt-get update && apt-get install -y --no-install-recommends r-base"
        )
        post.append("rm -rf /var/lib/apt/lists/*")
        post.append(
            "R -e \"install.packages('renv', repos='https://cloud.r-project.org')\""
        )
        files.append(f"{env.r_renv} {workdir}/{renv}")
        post.append(f"R -e \"renv::restore(lockfile='{workdir}/{renv}')\"")

    if env.julia_project:
        proj = Path(env.julia_project).name
        post.append(
            "JULIA_VERSION=1.10.4 && "
            "apt-get update && apt-get install -y --no-install-recommends "
            "curl ca-certificates && rm -rf /var/lib/apt/lists/* && "
            'curl -fsSL "https://julialang-s3.julialang.org/bin/linux/x64/'
            '${JULIA_VERSION%.*}/julia-${JULIA_VERSION}-linux-x86_64.tar.gz" '
            "| tar -xz -C /opt && "
            "ln -s /opt/julia-${JULIA_VERSION}/bin/julia /usr/local/bin/julia"
        )
        files.append(f"{env.julia_project} {workdir}/{proj}")
        files.append(f"Manifest.toml {workdir}/Manifest.toml")
        post.append(f'julia --project={workdir} -e "using Pkg; Pkg.instantiate()"')

    already = {
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
    if project_files:
        for f in project_files:
            safe = Path(f).as_posix()
            if Path(safe).name in already:
                continue
            files.append(f"{safe} {workdir}/{safe}")

    sections: list[str] = [
        "Bootstrap: docker",
        f"From: {base}",
        "",
        "%files",
    ]
    sections.extend(f"    {entry}" for entry in files)
    sections.extend(
        [
            "",
            "%post",
            "    export DEBIAN_FRONTEND=noninteractive",
        ]
    )
    sections.extend(f"    {cmd}" for cmd in post)
    sections.extend(
        [
            "",
            "%environment",
            f'    export PYTHONPATH="{workdir}"',
            "",
            "%runscript",
            '    echo "Use repropack run to execute the defined steps"',
        ]
    )
    return "\n".join(sections) + "\n"
