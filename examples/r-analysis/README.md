# r-analysis example

A minimal R experiment. `analysis.R` writes `results/summary.txt`.
ReproPack detects `renv.lock` and installs R + renv in the container.

```bash
repropack capture -p . -o r-analysis.rpk
repropack inspect r-analysis.rpk
```
