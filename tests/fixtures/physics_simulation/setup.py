"""Build script for the physics simulation C extension."""

from setuptools import Extension, setup

setup(
    name="physics_simulation",
    version="0.1.0",
    ext_modules=[
        Extension(
            "physics_simulation",
            sources=["simulation.c"],
        ),
    ],
)
