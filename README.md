# HermesMesh

HermesMesh is an independent, privacy-first capability mesh core extracted from Hermes Agent.

It provides schema validation, local node registry helpers, dispatch prompt construction,
result filtering, verification primitives, a standalone CLI, an HTTP service, and a small HTTP client for capability-network experiments.

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
python -m hermes_mesh.cli --mesh-home /tmp/mesh post-task task.yaml
python -m hermes_mesh.cli --mesh-home /tmp/mesh route-task task.yaml --json
```

## Standalone service and client

Run the HermesMesh service from this project; it exposes both the dashboard and JSON APIs:

```bash
python -m hermes_mesh.cli --mesh-home /tmp/mesh server --host 127.0.0.1 --port 8765
# For trusted LAN/VPN access only:
python -m hermes_mesh.cli --mesh-home /tmp/mesh server --host 0.0.0.0 --port 8765
```

Service endpoints:

- `GET /health`
- `GET /api/nodes`, `GET /api/nodes/{node_id}`, `POST /api/nodes`
- `GET /api/tasks`, `POST /api/tasks`
- `POST /api/tasks/route`
- `GET /api/assignments`, `POST /api/assignments`
- `GET /api/results`, `POST /api/results`

Use the bundled client CLI against a running service:

```bash
python -m hermes_mesh.cli client --url http://127.0.0.1:8765 health
python -m hermes_mesh.cli client --url http://127.0.0.1:8765 nodes
python -m hermes_mesh.cli client --url http://127.0.0.1:8765 register manifest.yaml
python -m hermes_mesh.cli client --url http://127.0.0.1:8765 post-task task.yaml
python -m hermes_mesh.cli client --url http://127.0.0.1:8765 route-task task.yaml --required-tool python
```

Python client:

```python
from hermes_mesh.client import HermesMeshClient

client = HermesMeshClient("http://127.0.0.1:8765")
print(client.health())
print(client.list_nodes())
```

Registry home resolution:

1. `--mesh-home`
2. `$HERMES_MESH_HOME`
3. `~/.hermes-mesh`

## Register a node without cloning HermesMesh

A remote Hermes instance can register with a running HermesMesh service using one stdlib-only Python script fetched over HTTPS. The script submits only a privacy-first capability manifest; it does not read or upload skills, memory, sessions, raw logs, env vars, or secrets.

```bash
curl -fsSL https://raw.githubusercontent.com/Omvira/HermesMesh/main/scripts/register_node.py | \
  python3 - \
    --mesh-url http://10.0.16.11:8765 \
    --node-id hermes-node-a \
    --display-name "Hermes Node A" \
    --task-type code_review \
    --task-type python_debugging \
    --tool hermes \
    --tool python \
    --tool git
```

Preview the manifest without registering:

```bash
curl -fsSL https://raw.githubusercontent.com/Omvira/HermesMesh/main/scripts/register_node.py | \
  python3 - --mesh-url http://10.0.16.11:8765 --task-type code_review --tool hermes --dry-run
```

After registration, verify from any machine that can reach the service:

```bash
curl -fsSL http://10.0.16.11:8765/api/nodes
```

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
