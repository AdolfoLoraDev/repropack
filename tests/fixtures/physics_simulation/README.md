# Physics Simulation

A minimal projectile-motion simulation that mixes Python and a C extension.

## Structure

- `simulation.c` – C implementation of projectile range calculation.
- `setup.py` – Builds the C extension via `setuptools`.
- `run_simulation.py` – Python driver that loads the compiled shared library.
- `Makefile` – Convenience targets `build` and `run`.
- `requirements.txt` – Python dependencies (`numpy`).

## Reproducing

```bash
make build   # compiles the C extension
make run     # executes the simulation
```

Or with ReproPack:

```bash
repropack capture -p . -o physics.rpk
repropack run physics.rpk
```
