# ReproPack

**ReproPack** turns chaotic project folders into self-contained, reproducible
packages (`.rpk`) with a frozen environment, a W3C PROV provenance graph and a
declarative manifest.

## Why

> *"Reproducibility is not a feature you add at the end. It is a practice you
> build from the first line of code."*

A `.rpk` bundles everything needed to re-run an experiment:

- **`repropack.yml`** — manifest with metadata, environment and ordered steps.
- **`Dockerfile`** (and optionally **`apptainer.def`**) — the frozen environment.
- **`provenance.json`** — the W3C PROV graph of agents, activities and entities.
- **`project/`** — your code and (small) data, hashed with SHA256.
- **`data_manifest.json`** — checksums and external references for large data.

## Install

```bash
pip install repropack
```

## At a glance

```bash
repropack capture --project ./my_experiment --output my_experiment.rpk
repropack inspect my_experiment.rpk
repropack run my_experiment.rpk --strict
```

## Multi-language

ReproPack detects and containerises Python (pip/conda/poetry), R (`renv`),
Julia (`Project.toml`), MATLAB/Octave, CMake/Make and shell pipelines, tagging
each inferred step with its language.

See the [Quick-Start Tutorial](tutorial.md) and the
[Commands reference](commands.md).
