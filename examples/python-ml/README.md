# python-ml example

A minimal Python + pip experiment. `prepare.py` generates `data/raw.csv`
and `train.py` fits a one-line linear model, writing `results/model.json`.

ReproPack auto-detects `prepare.py` and `train.py` as ordered steps and
generates a pip lockfile.

```bash
repropack capture -p . -o python-ml.rpk
repropack run python-ml.rpk --lite
```
