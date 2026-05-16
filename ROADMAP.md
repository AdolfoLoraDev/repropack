# ReproPack Roadmap

**Current version:** 0.1.0 (MVP)
**Last updated:** May 2026
**Status:** Core CLI, manifest engine, W3C PROV graph, and `.rpk` packaging are functional. Ready for early adopters and community feedback.

---

## ✅ Current State (MVP v0.1.0)

The foundation is solid, tested, and installable. All items below are complete and verified in CI.

- [x] **CLI framework** with Typer: `capture`, `run`, `graph`, `version`
- [x] **Pydantic + YAML manifest** (`repropack.yml`) with automatic/manual steps
- [x] **Strict Dockerfile generation** with base-image digests and `--require-hashes`
- [x] **W3C PROV provenance graph** via the `prov` library and NetworkX
- [x] **Graph exports**: DOT, Mermaid, and self-contained HTML
- [x] **`.rpk` packaging** (internal ZIP format with `project/`, `Dockerfile`, `provenance.json`, `repropack.yml`)
- [x] **Environment detection**: pip, Conda, Poetry
- [x] **Lockfile generation**: `requirements.lock` (pip freeze fallback) and `conda-lock.yml`
- [x] **Automatic step inference** from common script names (`prepare.py`, `train.py`, `evaluate.py`)
- [x] **Manual step support** in manifest and CLI (`--skip-manual`, interactive prompts)
- [x] **Rich terminal UI** with progress spinners and styled output
- [x] **Test suite**: 18/18 tests passing (pytest + coverage)
- [x] **Linting & formatting**: ruff, black, mypy, pre-commit hooks
- [x] **CI/CD**: GitHub Actions workflow (Python 3.10–3.12)
- [x] **Open source setup**: MIT `LICENSE`, `README.md`, `CONTRIBUTING.md`

---

## Development Phases

### Phase 0: Preparation and Initial Release *(wrapping up)*

> Polish the MVP so early users can install from PyPI and reproduce real projects without friction.

- [ ] Publish v0.1.0 to PyPI with `hatchling` build
- [ ] Add a `Makefile` with common dev commands (`test`, `lint`, `format`, `clean`)
- [ ] Create a `.github/ISSUE_TEMPLATE/` (bug report + feature request)
- [ ] Add `.github/PULL_REQUEST_TEMPLATE.md`
- [ ] Write a **quick-start tutorial** in `docs/tutorial.md`
- [ ] Record a 2-minute GIF demo for the README
- [ ] Tag `v0.1.0` and write release notes

---

### Phase 1: Stabilization and Basic Reproducibility *(1–2 weeks)*

> Harden the capture pipeline and make the first real-world reproductions reliable.

- [ ] **Robust lockfile generation**
  - [ ] Prefer `pip-compile --generate-hashes` when available
  - [ ] Fallback to `pip freeze` with a warning about missing hashes
  - [ ] Support `conda env export --no-builds` and `conda-lock` correctly
  - [ ] Detect and warn about editable installs (`-e .`) in lockfiles
- [ ] **Resolve real Docker base-image SHA256 digests**
  - [ ] Query Docker Hub / registry API to pin exact digests at capture time
  - [ ] Allow users to override with `--base-image`
- [ ] **Add `repropack inspect <file.rpk>`**
  - [ ] Pretty-print manifest metadata, steps, and environment summary
- [ ] **Add `repropack validate <file.rpk>`**
  - [ ] Check internal structure, schema validity, and file hashes
- [ ] **File-hash verification on package creation**
  - [ ] Compute SHA256 for every file inside the `.rpk`
  - [ ] Store hashes in manifest for integrity checks
- [ ] **Improve automatic step inference**
  - [ ] Detect Jupyter notebooks (`.ipynb`) and generate `jupyter execute` steps
  - [ ] Detect R scripts (`.R`) and shell scripts (`.sh`)
  - [ ] Parse `Makefile` targets and offer them as manual/automatic steps
- [ ] **End-to-end real-project tests**
  - [ ] A Jupyter-based machine-learning notebook
  - [ ] A physics simulation with Python + C++ extension
  - [ ] A genomics pipeline with Conda + R + shell scripts
- [ ] **Better error handling**
  - [ ] Catch missing Docker daemon and suggest Podman / Apptainer
  - [ ] Validate that captured paths exist before writing the `.rpk`

---

### Phase 2: Full Multi-language Support *(2–4 weeks)*

> ReproPack should feel native to researchers regardless of their language stack.

- [ ] **R ecosystem support**
  - [ ] Detect `renv/` and generate `renv.lock`
  - [ ] Install R + renv in the Dockerfile
  - [ ] Add R step inference (`script.R`, `run_analysis.R`)
- [ ] **Julia ecosystem support**
  - [ ] Detect `Project.toml` and `Manifest.toml`
  - [ ] Install Julia + instantiate packages in Dockerfile
  - [ ] Add Julia step inference (`script.jl`, `run.jl`)
- [ ] **Compiled-language support**
  - [ ] Detect `Makefile` and generate `make` steps
  - [ ] Detect `CMakeLists.txt` and generate `cmake` + `make` steps
  - [ ] Basic Fortran / C support via system compilers
- [ ] **MATLAB / Octave detection**
  - [ ] Detect `.m` scripts and add Octave-compatible Dockerfile steps
- [ ] **Multi-language Dockerfile**
  - [ ] Build a multi-stage or fat image when the project mixes Python + R + Julia
  - [ ] Allow `environment.system_packages` to specify language runtimes
- [ ] **Mixed-flow detection**
  - [ ] Automatically tag steps with their inferred language
  - [ ] Validate that the Dockerfile contains all required runtimes
- [ ] **Enhanced manual-step tracking**
  - [ ] Record which files were affected by a manual step (user-declared)
  - [ ] Timestamp manual-step completion in the provenance graph
- [ ] **Language-specific ignore patterns**
  - [ ] `.Rhistory`, `.ipynb_checkpoints`, `Manifest.toml` auto-excluded

---

### Phase 3: Strong Reproducibility and Production Tooling *(4–8 weeks)*

> Move from "works on my machine" to "verifiably identical results anywhere."

- [ ] **Apptainer / Singularity support**
  - [ ] `repropack capture --container apptainer` to generate `.def` files
  - [ ] `repropack run` auto-detects Apptainer on HPC clusters
- [ ] **`--strict` mode**
  - [ ] Re-run the experiment and compare output hashes against the manifest
  - [ ] Fail reproduction if any output hash differs
  - [ ] Store expected output hashes at capture time
- [ ] **Large-data handling**
  - [ ] `repropack capture --exclude-data` to skip big files
  - [ ] Support external data references (DOI, Zenodo, S3, DVC)
  - [ ] Generate `data_manifest.json` with checksums for external datasets
- [ ] **Publish command**
  - [ ] `repropack publish --to zenodo` (Zenodo API integration)
  - [ ] `repropack publish --to osf` (Open Science Framework API)
  - [ ] Generate a citable `CITATION.cff` from manifest metadata
- [ ] **Provenance graph enhancements**
  - [ ] Interactive HTML graph with pan/zoom (Mermaid or D3.js)
  - [ ] Export provenance as W3C PROV-XML
  - [ ] Link provenance to ORCID authors when available
- [ ] **Lite mode (no container)**
  - [ ] `repropack run --lite` executes steps directly in the current environment
  - [ ] Warn if Python version or packages mismatch the lockfile
- [ ] **Performance profiling**
  - [ ] Optional `--profile` flag to record step duration and resource usage
  - [ ] Store profiling data in the manifest for reproducibility reporting
- [ ] **Diff / merge for manifests**
  - [ ] `repropack diff experiment_v1.rpk experiment_v2.rpk`
  - [ ] Show changed steps, packages, and files side-by-side

---

### Phase 4: Adoption, Community, and Long-term Maintenance *(3+ months)*

> Transition from a promising tool to a sustainable, community-owned standard.

- [ ] **Funding & sustainability**
  - [ ] Activate GitHub Sponsors with tiered goals
  - [ ] Set up Open Collective page and transparent budget
  - [ ] Apply for NumFOCUS small development grant
- [ ] **Domain-specific examples**
  - [ ] `examples/physics-lattice-simulation/` (Python + Cython)
  - [ ] `examples/bioinformatics-pipeline/` (Conda + Snakemake + R)
  - [ ] `examples/math-proof-verification/` (Julia + Lean)
  - [ ] `examples/climate-model-analysis/` (Jupyter + Dask + NetCDF)
- [ ] **Editor & IDE integrations**
  - [ ] JupyterLab extension: "Export to ReproPack" button
  - [ ] RStudio add-in for one-click capture
  - [ ] VS Code extension with tree view of `.rpk` contents
- [ ] **Academic recognition**
  - [ ] Write and submit a short preprint to arXiv (cs.SE or cs.DC)
  - [ ] Present at conferences: SciPy, RSECon, FORCE11, FOSDEM
  - [ ] Publish a reproducibility checklist based on ReproPack workflows
- [ ] **Documentation & onboarding**
  - [ ] Migrate to MkDocs with versioning
  - [ ] Video tutorials in English and Spanish
  - [ ] "ReproPack for Reviewers" guide for journals
- [ ] **Governance**
  - [ ] Define a lightweight RFC process for major changes
  - [ ] Elect a small steering committee from active contributors
  - [ ] Quarterly public maintenance reports (issues closed, releases, roadmap updates)
- [ ] **Ecosystem integrations**
  - [ ] Plugin API so third parties can add custom exporters (e.g. Nextflow, Galaxy)
  - [ ] Integration with `reprozip` for legacy compatibility
  - [ ] Integration with `repo2docker` for Binder compatibility

---

## Nice-to-Have / Future Ideas

> Prioritized backlog. These are valuable but not on the critical path until a Phase milestone is reached.

1. **Cloud runners**: `repropack run --cloud aws` or `--cloud gcp` for remote reproduction.
2. **GUI wrapper**: A minimal PyQt / Tauri desktop app for non-CLI users.
3. **Blockchain anchoring**: Optional SHA256 anchoring of provenance to a public ledger for tamper-proofing.
4. **AI-assisted step inference**: Use LLMs to suggest missing manual steps from READMEs.
5. **ReproPack registry**: A public, searchable index of `.rpk` packages (similar to Docker Hub).
6. **Mobile-friendly provenance viewer**: A lightweight PWA to inspect `.rpk` graphs on a phone.

---

## How to Maintain This Roadmap

1. **Update after every release**: Tick completed boxes, move items between phases, and adjust dates.
2. **Use GitHub Issues & Milestones**: Every unchecked item should have a corresponding issue labeled `roadmap`.
3. **Quarterly review**: The maintainers will open a discussion thread to reprioritize based on community feedback.
4. **Contributions welcome**: If you want to own an item, comment on the related issue and tag `@repropack/maintainers`.

> *"Reproducibility is not a feature you add at the end. It is a practice you build from the first line of code."*
