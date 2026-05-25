# HermesMesh

HermesMesh is an independent, privacy-first capability mesh core extracted from Hermes Agent.

It provides schema validation, local node registry helpers, dispatch prompt construction,
result filtering, verification primitives, and a standalone CLI for capability-network experiments.

Hermes Agent is only one possible adapter/node runtime. The mesh core does not import Hermes internals and must not read or expose local memory, sessions, raw logs, reasoning traces, environment variables, or local skills.

## Install for development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
```

## CLI

```bash
python -m hermes_mesh.cli --help
python -m hermes_mesh.cli manifest --node-id local-node --display-name Local --task-type code_review --tool python
python -m hermes_mesh.cli --mesh-home /tmp/mesh register manifest.yaml
python -m hermes_mesh.cli --mesh-home /tmp/mesh list --json
```

Registry home resolution:

1. `--mesh-home`
2. `$HERMES_MESH_HOME`
3. `~/.hermes-mesh`

## Privacy model

By default manifests must not expose:

- local skills
- memory
- session history
- reasoning traces
- raw logs
- environment variables
- secrets

Task results are filtered through explicit allow/deny fields and secret-like strings are redacted.
Team/public skill contribution requires explicit human consent.
