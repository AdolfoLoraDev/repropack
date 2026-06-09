# Commands

## `repropack capture`

Capture a project into a `.rpk` package.

```bash
repropack capture --project ./exp --output exp.rpk [options]
```

| Option | Description |
|--------|-------------|
| `--project, -p` | Path to the project folder. |
| `--output, -o` | Output `.rpk` path. |
| `--manual-step, -m` | Add a manual step (repeatable). |
| `--base-image, -b` | Override the Docker base image. |
| `--container, -c` | `docker` (default), `apptainer`, or `both`. |
| `--exclude-data` | Exclude large files into `data_manifest.json`. |
| `--data-threshold-mb` | Size threshold for `--exclude-data` (default 50). |
| `--data-ref` | Declare an external dataset as `path=source` (repeatable). |
| `--allow-secrets` | Keep files flagged as secrets (excluded by default). |

If the project contains a hand-authored `repropack.yml`, its steps, authors,
description, base image and system packages take precedence over
auto-inference. Capture also records Git provenance (commit/branch/remote/dirty)
and, with `SOURCE_DATE_EPOCH` set, produces byte-identical `.rpk` archives.

## `repropack run`

Reproduce a `.rpk` package.

| Option | Description |
|--------|-------------|
| `--tag, -t` | Docker image tag. |
| `--skip-manual` | Skip manual steps. |
| `--lite` | Run directly on the host (no container). |
| `--no-cache` | Disable Docker build cache. |
| `--strict` | Re-hash declared outputs and fail on drift. |
| `--container, -c` | `auto` (Docker, fallback Apptainer), `docker`, `apptainer`. |
| `--profile` | Record per-step timing to `reproduction-profile.json`. |
| `--fetch-data` | Download external datasets from `data_manifest.json` first. |

## `repropack inspect`

Pretty-print manifest metadata, environment, steps, file hashes and the
archive tree.

## `repropack validate`

Check structure, schema, file hashes and that the Dockerfile provides every
runtime required by the steps.

## `repropack graph`

Render the provenance graph.

```bash
repropack graph exp.rpk --format mermaid --output graph.mmd
```

Formats: `dot`, `mermaid`, `html` (interactive pan/zoom), `png`, `provxml`.

## `repropack diff`

Compare two packages: steps, environment, packages and files.

```bash
repropack diff v1.rpk v2.rpk
```

## `repropack export`

Run a (possibly third-party) exporter plugin. See [Plugins](plugins.md).

```bash
repropack export exp.rpk                       # list exporters
repropack export exp.rpk -e citation -o CITATION.cff
```

## `repropack publish`

Generate `CITATION.cff` and optionally deposit to Zenodo or the OSF.

```bash
repropack publish exp.rpk --to citation
repropack publish exp.rpk --to zenodo --token "$ZENODO_TOKEN"
repropack publish exp.rpk --to osf --token "$OSF_TOKEN"
```

## `repropack sign` / `repropack verify`

Attest and verify package integrity. By default a SHA256 attestation is
written next to the package; with `--cosign` a sigstore signature is used.

```bash
repropack sign exp.rpk                 # writes exp.rpk.attestation.json
repropack verify exp.rpk               # checks the attestation
repropack sign exp.rpk --cosign --key cosign.key
repropack verify exp.rpk --cosign --signature exp.rpk.sig --key cosign.pub
```
