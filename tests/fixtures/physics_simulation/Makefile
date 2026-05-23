# Physics simulation Makefile

PYTHON ?= python3

.PHONY: build run clean

build:
	$(PYTHON) setup.py build_ext --inplace
	@echo "Extension built."

run: build
	$(PYTHON) run_simulation.py

clean:
	rm -rf build/ *.so *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
