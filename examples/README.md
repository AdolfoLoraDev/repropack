# ReproPack Examples

Small, self-contained example projects you can capture and reproduce with
ReproPack. Each folder is a complete experiment.

| Example | Stack | What it shows |
|---------|-------|---------------|
| [`python-ml/`](python-ml/) | Python + pip | Automatic `prepare` / `train` / `evaluate` step inference and a pip lockfile. |
| [`r-analysis/`](r-analysis/) | R + renv | R script step inference and `renv.lock` detection. |
| [`julia-sim/`](julia-sim/) | Julia | `Project.toml` detection and `.jl` step inference. |

## Try it

```bash
# Capture an example into a .rpk
repropack capture --project examples/python-ml --output python-ml.rpk

# Inspect what was captured
repropack inspect python-ml.rpk

# Reproduce without Docker (lite mode)
repropack run python-ml.rpk --lite

# Verify outputs reproduce bit-for-bit
repropack run python-ml.rpk --lite --strict
```
