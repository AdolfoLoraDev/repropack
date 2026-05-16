# ReproPack

[![CI](https://github.com/tu-org/repropack/actions/workflows/ci.yml/badge.svg)](https://github.com/tu-org/repropack/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

**ReproPack** is an open-source CLI tool for researchers that turns chaotic project folders into self-contained reproducible packages (`.rpk`).

## Key Features

- **Frozen exact environment**: Generates strict Dockerfiles with base image digests and lockfiles.
- **W3C PROV provenance graph**: Tracks every step, file, and agent involved in the experiment.
- **Declarative manifest**: Defines automatic and manual steps in `repropack.yml`.
- **Single command**: Capture and reproduce complex experiments effortlessly.
- **Multi-language**: Detects Python, Conda, R, Julia, and more.

## Installation

```bash
pip install repropack
```

Or in development mode:

```bash
git clone https://github.com/tu-org/repropack.git
cd repropack
pip install -e ".[dev]"
pre-commit install
```

## Quick Start

### Capture an experiment

```bash
repropack capture --project ./my_experiment --output my_experiment.rpk
```

This generates:
- `repropack.yml`: Manifest with metadata and reproduction steps.
- `Dockerfile`: Frozen environment with base image hash.
- `provenance.json`: Complete W3C PROV graph.
- Packaged into a single `.rpk` archive.

### Reproduce an experiment

```bash
repropack run my_experiment.rpk
```

Unpacks, builds the Docker image (or reuses cache), and runs steps in order.

### Visualize the provenance graph

```bash
repropack graph my_experiment.rpk --format mermaid --output graph.html
```

Supported formats: `dot`, `mermaid`, `png` (requires Graphviz installed).

## Example Manifest (repropack.yml)

```yaml
repropack_version: "0.1.0"
metadata:
  name: "my_experiment"
  created_at: "2026-05-16T12:00:00Z"
  authors:
    - "Ana Garcia <ana@example.com>"
  description: "Genomic sequence analysis"
environment:
  base_image: "python:3.11-slim@sha256:abc123..."
  python_requirements: "requirements.lock"
  system_packages:
    - "build-essential"
steps:
  - id: "prepare_data"
    type: "automatic"
    command: "python scripts/prepare.py"
    inputs: ["data/raw/"]
    outputs: ["data/processed/"]
  - id: "fit_model"
    type: "automatic"
    command: "python scripts/train.py"
    inputs: ["data/processed/", "configs/model.yml"]
    outputs: ["results/model.pkl", "results/metrics.json"]
  - id: "manual_validation"
    type: "manual"
    description: "Review metrics and approve model before publishing"
    instructions: "Open results/metrics.json and verify AUC > 0.85"
```

## Architecture

```
repropack/
├── cli.py              # Typer interface (capture, run, graph)
├── core/
│   ├── capture.py      # Project capture logic
│   ├── run.py          # Reproduction logic
│   ├── manifest.py     # YAML manifest validation
│   ├── provenance.py   # W3C PROV graph with NetworkX
│   └── docker_generator.py  # Dockerfile generation
└── utils/
    └── environment.py  # Environment detection (pip, conda, etc.)
```

## Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

MIT © ReproPack Team
