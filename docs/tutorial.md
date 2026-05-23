# ReproPack Quick-Start Tutorial

This tutorial walks you through capturing and reproducing your first experiment with ReproPack.

## Prerequisites

- Python 3.10, 3.11 or 3.12
- Docker installed (for reproduction)

## Install ReproPack

```bash
pip install repropack
```

Or in development mode:

```bash
git clone https://github.com/tu-org/repropack.git
cd repropack
pip install -e ".[dev]"
```

## Step 1: Prepare a sample project

Create a folder with a simple experiment:

```bash
mkdir my_experiment
cd my_experiment
```

Create `train.py`:

```python
# train.py
import json
from datetime import datetime

print("Training model...")
metrics = {"auc": 0.92, "timestamp": datetime.utcnow().isoformat()}
with open("results/metrics.json", "w") as f:
    json.dump(metrics, f)
print("Done! Results saved to results/metrics.json")
```

Create `requirements.txt`:

```
numpy>=1.24.0
```

Create the results directory:

```bash
mkdir results
```

## Step 2: Capture the experiment

From inside `my_experiment`, run:

```bash
repropack capture --project . --output ../my_experiment.rpk
```

ReproPack will:

1. Detect the pip environment.
2. Generate a `requirements.lock`.
3. Build `repropack.yml` with inferred steps.
4. Generate a strict `Dockerfile`.
5. Create a W3C PROV provenance graph.
6. Package everything into `my_experiment.rpk`.

## Step 3: Inspect the package

List the contents of the `.rpk` (it is a ZIP file):

```bash
unzip -l my_experiment.rpk
```

You should see:

- `project/` – your code and data
- `repropack.yml` – manifest
- `Dockerfile` – frozen environment
- `provenance.json` – W3C PROV graph

## Step 4: Reproduce the experiment

Run:

```bash
repropack run my_experiment.rpk
```

ReproPack will:

1. Unpack the `.rpk`.
2. Build the Docker image.
3. Execute automatic steps in order.
4. Prompt you for any manual steps.

Use `--skip-manual` to bypass manual steps:

```bash
repropack run my_experiment.rpk --skip-manual
```

## Step 5: Visualize provenance

Generate a Mermaid diagram:

```bash
repropack graph my_experiment.rpk --format mermaid --output graph.mmd
```

Or a self-contained HTML page:

```bash
repropack graph my_experiment.rpk --format html --output graph.html
```

Open `graph.html` in your browser to explore the provenance graph.

## Next steps

- Edit `repropack.yml` to add manual steps or custom metadata.
- Share `.rpk` files with collaborators for guaranteed reproducibility.
- Check the CLI help with `repropack --help`.
