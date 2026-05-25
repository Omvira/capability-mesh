---
title: HermesMesh Independent Service
sidebar_position: 95
---

# HermesMesh Independent Service

HermesMesh is a privacy-first capability access network for task-capable nodes.
Each node can register what it can do and can post tasks that need to be done.
The HermesMesh service routes tasks according to declared capabilities.

The first principle is:

> No skill, memory, session, trace, local workflow, or private experience is shared by default. Nodes contribute task capability; knowledge/skill contribution requires explicit human consent.

## Architecture

- `hermes_mesh.core`: schemas, validators, local registries, deterministic routing, result filtering, verification primitives, and contribution records.
- `hermes_mesh.dashboard`: standalone stdlib HTTP service. It serves the dashboard and JSON APIs from this repository; it is not a Hermes Agent plugin.
- `hermes_mesh.client`: stdlib HTTP client for registering nodes, posting tasks, routing tasks, and recording filtered results against a running service.
- `hermes_mesh.cli`: standalone CLI for local registry operations, the service, and client calls.

Hermes Agent is only one possible node runtime/adapter. HermesMesh must not import Hermes internals and must not read or expose local memory, sessions, raw logs, reasoning traces, environment variables, or local skills.

## What Alpha includes

- Capability manifests describing task types and tools available on a node.
- Task contracts with explicit allowed and forbidden result fields.
- Local task posting and deterministic task routing by task type/tool match.
- Assignment records and privacy-filtered result records.
- Contribution records that remain local-private unless explicit human consent is supplied for team/public visibility.
- A standalone HTTP service and a bundled HTTP client.

## What Alpha intentionally does not include

- No token, DAO, or reward system.
- No automatic skill upload.
- No memory/session/trace sharing.
- No arbitrary remote execution protocol by default.
- No default sharing of private local workflows.

## CLI examples

Generate and register a privacy-first node manifest:

```bash
python -m hermes_mesh.cli manifest \
  --node-id local-node-1 \
  --display-name "Local Hermes" \
  --task-type code_review \
  --task-type test_running \
  --tool terminal \
  --tool file \
  --output capability-manifest.yaml
python -m hermes_mesh.cli --mesh-home /tmp/mesh register capability-manifest.yaml
```

Post and route a task locally:

```bash
python -m hermes_mesh.cli --mesh-home /tmp/mesh post-task task-contract.yaml
python -m hermes_mesh.cli --mesh-home /tmp/mesh route-task task-contract.yaml --json
```

Filter and record a result:

```bash
python -m hermes_mesh.cli filter-result result.yaml --contract task-contract.yaml
python -m hermes_mesh.cli --mesh-home /tmp/mesh record-result result.yaml
```

## Service and client

Run the standalone service:

```bash
python -m hermes_mesh.cli --mesh-home /tmp/mesh server --host 127.0.0.1 --port 8765
```

For trusted LAN/VPN access only:

```bash
python -m hermes_mesh.cli --mesh-home /tmp/mesh server --host 0.0.0.0 --port 8765
```

Client CLI:

```bash
python -m hermes_mesh.cli client --url http://127.0.0.1:8765 health
python -m hermes_mesh.cli client --url http://127.0.0.1:8765 nodes
python -m hermes_mesh.cli client --url http://127.0.0.1:8765 register capability-manifest.yaml
python -m hermes_mesh.cli client --url http://127.0.0.1:8765 post-task task-contract.yaml
python -m hermes_mesh.cli client --url http://127.0.0.1:8765 route-task task-contract.yaml --required-tool terminal
```

HTTP endpoints:

- `GET /health`
- `GET /api/nodes`, `GET /api/nodes/{node_id}`, `POST /api/nodes`
- `GET /api/tasks`, `POST /api/tasks`
- `POST /api/tasks/route`
- `GET /api/assignments`, `POST /api/assignments`
- `GET /api/results`, `POST /api/results`

For public or team contribution, optional contribution/skill proposal records must include:

```yaml
human_consent: true
human_review_note: "Reviewed and approved for sharing."
```

For local-only proposals, use:

```yaml
proposed_visibility: local_private
human_consent: false
```
