---
title: HermesMesh Independent Service
sidebar_position: 95
---

# HermesMesh Independent Service

HermesMesh is a privacy-first capability access network for task-capable nodes.
Each node can register what it can do and can post tasks that need to be done.
The HermesMesh service plans parent tasks and invokes allowlisted server-local tools and nodes as callable capability tools according to declared capabilities.

The first principle is:

> No skill, memory, session, trace, local workflow, or private experience is shared by default. Nodes contribute task capability; knowledge/skill contribution requires explicit human consent.

## Architecture

- `hermes_mesh.core`: schemas, validators, local registries, deterministic routing, mixed server/node tool-call planning, assignment orchestration, result filtering, verification primitives, and contribution records.
- `hermes_mesh.dashboard`: standalone stdlib HTTP service. It serves the dashboard and JSON APIs from this repository; it is not a Hermes Agent plugin.
- `hermes_mesh.client`: stdlib HTTP client for registering nodes, posting tasks, routing tasks, and recording filtered results against a running service.
- `hermes_mesh.cli`: standalone CLI for local registry operations, the service, and client calls.

Hermes Agent is only one possible node runtime/adapter. HermesMesh must not import Hermes internals and must not read or expose local memory, sessions, raw logs, reasoning traces, environment variables, or local skills.

## What Alpha includes

- Capability manifests describing task types and tools available on a node.
- Task contracts with explicit allowed and forbidden result fields.
- Local task posting and deterministic task routing by task type/tool match.
- Server-planned mixed tool calls for subtasks/partial operations under a parent task: 0..n allowlisted server-local tools plus 0..n node capability tools.
- Safe server-local tool execution for deterministic non-dangerous tools only, currently `aggregate_results`, `verify_result`, and `echo_sanitized`; no filesystem, network, or shell execution is available through this registry.
- Assignment polling/claiming/completion records and privacy-filtered result records.
- Contribution records that remain local-private unless explicit human consent is supplied for team/public visibility.
- A standalone HTTP service and a bundled HTTP client.

## What Alpha intentionally does not include

- No token, DAO, or reward system.
- No automatic skill upload.
- No memory/session/trace sharing.
- No arbitrary remote execution protocol by default.
- No arbitrary server-local execution tools.
- No default sharing of private local workflows.
- No public exposure of local dispatch commands; node execution uses the node's own local Agent command.

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

The HTTP service also exposes `POST /api/tasks/plan` for compatibility with node-only planning and `POST /api/tasks/plan-step` for mixed server/node orchestration. `plan-step` returns an action such as `invoke_server_tool`, `invoke_node`, `orchestration_action`, `completed`, or `no_match`.

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
python -m hermes_mesh.cli client --url http://127.0.0.1:8765 poll local-node-1
python -m hermes_mesh.cli client --url http://127.0.0.1:8765 run-next capability-manifest.yaml
```

HTTP endpoints:

- `GET /health`
- `GET /api/nodes`, `GET /api/nodes/{node_id}`, `POST /api/nodes`
- `GET /api/tasks`, `POST /api/tasks`
- `POST /api/tasks/plan`
- `POST /api/tasks/plan-step`
- `POST /api/tasks/route`
- `GET /api/assignments`, `POST /api/assignments`
- `GET /api/nodes/{node_id}/assignments`
- `POST /api/assignments/{assignment_id}/claim`
- `POST /api/assignments/{assignment_id}/complete`
- `GET /api/results`, `POST /api/results`

## Orchestration Decisions

HermesMesh is the planner/controller. For one parent task, the service may invoke 0..n server-local tools and 0..n nodes. A server-local invocation is an allowlisted deterministic tool call whose result is filtered and recorded locally. A node invocation is a single assigned subtask or partial operation selected by task type, required tools, and capability metadata. A node does not own the whole parent task unless the assigned subtask actually completes it.

Mixed plan steps use explicit kinds: `server_tool_call`, `node_tool_call`, and `orchestration_tool_call`. A planning response maps those to `invoke_server_tool`, `invoke_node`, `orchestration_action`, `completed`, or `no_match`. Server-local builders reject private node transport/dispatch fields and reject non-allowlisted server tools. Nodes never directly call Server tools and never receive Server private tool definitions, node private transport commands, or dispatch commands in assignments.

When a node completes an assignment, the service filters the result through the task contract, accumulates only the filtered output, records verification and a local-private contribution record, then returns a decision:

- `completed`: the result completed and verification passed.
- `awaiting_more_results`: the node marked the output as partial or needing more results.
- `route_next`: the result failed or did not verify, and another capable node was assigned.
- `no_match`: no remaining registered node matches the task requirements.

The task prompt generated for local dispatch explicitly instructs the node Agent to handle only the assigned subtask and not to load or expose memory, local skills, session history, reasoning traces, raw logs, environment variables, or secrets. A node response may include only allowed result fields plus optional boolean `partial` and `needs_more_results` signals. Public node views and aggregated results expose only routing/output metadata that passed filtering; they never expose memory, skills, sessions, raw logs, env vars, reasoning traces, transport commands, or dispatch commands.

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
