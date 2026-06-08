# julia-sim example

A minimal Julia experiment estimating pi via Monte Carlo, writing
`results/pi.txt`. ReproPack detects `Project.toml`/`Manifest.toml` and
installs Julia + instantiates the project.

```bash
repropack capture -p . -o julia-sim.rpk
repropack inspect julia-sim.rpk
```
