#!/usr/bin/env python3
"""Run the projectile-motion simulation."""

import ctypes
import math
from pathlib import Path

import numpy as np


def load_simulation_lib() -> ctypes.CDLL:
    """Load the compiled C shared library."""
    so_path = Path(__file__).with_suffix(".so")
    if not so_path.exists():
        raise FileNotFoundError(
            f"Compiled extension not found: {so_path}\n" "Run 'make build' first."
        )
    lib = ctypes.CDLL(str(so_path))
    lib.projectile_range.argtypes = [ctypes.c_double, ctypes.c_double, ctypes.c_double]
    lib.projectile_range.restype = ctypes.c_double
    return lib


def main() -> None:
    """Simulate ranges for a grid of angles."""
    lib = load_simulation_lib()
    v0 = 50.0  # m/s
    g = 9.81  # m/s^2
    angles = np.linspace(0, 90, 10)

    print(f"Projectile range for v0={v0} m/s, g={g} m/s^2")
    print("-" * 40)
    for angle in angles:
        rng = lib.projectile_range(v0, float(angle), g)
        print(f"  angle={angle:5.1f}°  range={rng:8.2f} m")

    # Verify maximum range at 45°
    max_range = lib.projectile_range(v0, 45.0, g)
    theoretical = v0 * v0 / g
    print("-" * 40)
    print(f"Max range (45°) = {max_range:.2f} m")
    print(f"Theoretical max = {theoretical:.2f} m")
    assert math.isclose(max_range, theoretical, rel_tol=1e-9)
    print("Simulation successful!")


if __name__ == "__main__":
    main()
