---
title: Capability Mesh Independent Service
sidebar_position: 95
---

# Capability Mesh Independent Service

Capability Mesh is a privacy-first capability access network for task-capable nodes.
Each node can register what it can do and can post tasks that need to be done.
The Capability Mesh service plans parent tasks and invokes allowlisted server-local tools and nodes as callable capability tools according to declared capabilities.

The first principle is:

> No skill, memory, session, trace, local workflow, or private experience is shared by default. Nodes contribute task capability; knowledge/skill contribution requires explicit human consent.

## Architecture

- `capability_mesh.core`: public namespace for schemas, validators, local registries, deterministic routing, mixed server/node tool-call planning, assignment orchestration, privacy-safe node heartbeat/status records, result filtering, verification primitives, and contribution records.
- `capability_mesh.dashboard`: public namespace for the standalone stdlib Server. It runs the Capability Mesh HTTP service, dashboard, JSON APIs, Agent Card, A2A Protocol 1.0 message/task endpoints, and local registry.
- `capability_mesh.client`: public namespace for the independent stdlib Client for health polling, heartbeats, node registration, task polling/claiming/completion, and A2A Protocol 1.0 message sending against a running Server.
- `capability_mesh.cli`: public namespace for the standalone CLI for local registry operations, starting the Server, starting Client commands/loops, and launching the guided trial Client installer.
- `scripts/install_client.py`: stdlib-only guided installer for a first Client. It prompts for safe public metadata, registers with a Server, writes a local manifest, and optionally keeps the Client online with heartbeat.

Capability Mesh must not read or expose local memory, sessions, raw logs, reasoning traces, environment variables, or local skills.

## What Alpha includes

- Capability manifests describing task types and tools available on a node.
- Task contracts with explicit allowed and forbidden result fields.
- Local task posting and deterministic task routing by task type/tool match.
- Server-planned mixed tool calls for subtasks/partial operations under a parent task: 0..n allowlisted server-local tools plus 0..n node capability tools.
- Safe server-local tool execution for deterministic non-dangerous tools only, currently `aggregate_results`, `verify_result`, and `echo_sanitized`; no filesystem, network, or shell execution is available through this registry.
- Assignment polling/claiming/completion records, privacy-safe node heartbeat/status records, and privacy-filtered result records.
- A Server/Client split where Client liveness is reported through heartbeat/presence and Server liveness is detected by Client health polling.
- A guided trial Client installer for first-run onboarding: generate a safe manifest, register, save local config, send initial heartbeat, optionally install a user systemd keep-online service or run a foreground heartbeat loop.
- A2A Protocol 1.0 JSON APIs validated with the official `a2a-sdk`: Agent Card, message envelopes with `role` and `parts`, TextPart/FilePart/DataPart content, task envelopes, response artifacts, SSE `StreamResponse` events, push notification configs, and JSON-RPC core operation binding.
- Contribution records that remain local-private unless explicit human consent is supplied for team/public visibility.
- A standalone HTTP service and a bundled HTTP client.
- Hub/Node production layer: Hub AgentCard registry and discovery, standalone Node A2A server, Node-side A2A client helper, HTTP relay, bearer-auth guarded mutating routes, file policy engine, structured audit, durable async worker records, push delivery retry records, optional gRPC adapter helpers, and deployment templates.

## What Alpha intentionally does not include

- No token, DAO, or reward system.
- No automatic skill upload.
- No memory/session/trace sharing.
- No arbitrary remote execution protocol by default.
- No arbitrary server-local execution tools.
- No default sharing of private local workflows.
- No public exposure of local dispatch commands; node execution uses the node's own local Agent command.
- No claim that Capability Mesh HTTP relay, long-poll placeholder, WebSocket/tunnel plans, or gRPC helper are official A2A transport bindings. They are custom deployment bindings around SDK-validated A2A JSON surfaces.

## CLI examples

Generate and register a privacy-first node manifest:

```bash
python -m capability_mesh.cli manifest \
  --node-id local-node-1 \
  --display-name "Local Agent" \
  --task-type code_review \
  --task-type test_running \
  --tool terminal \
  --tool file \
  --output capability-manifest.yaml
python -m capability_mesh.cli --mesh-home /tmp/mesh register capability-manifest.yaml
```

Post and route a task locally:

```bash
python -m capability_mesh.cli --mesh-home /tmp/mesh post-task task-contract.yaml
python -m capability_mesh.cli --mesh-home /tmp/mesh route-task task-contract.yaml --json
```

The HTTP service also exposes `POST /api/tasks/plan` for compatibility with node-only planning and `POST /api/tasks/plan-step` for mixed server/node orchestration. `plan-step` returns an action such as `invoke_server_tool`, `invoke_node`, `orchestration_action`, `completed`, or `no_match`.

Filter and record a result:

```bash
python -m capability_mesh.cli filter-result result.yaml --contract task-contract.yaml
python -m capability_mesh.cli --mesh-home /tmp/mesh record-result result.yaml
```

## Service and client

Run the standalone service:

```bash
python -m capability_mesh.cli --mesh-home /tmp/mesh server --host 127.0.0.1 --port 8765
```

For trusted LAN/VPN access only:

```bash
python -m capability_mesh.cli --mesh-home /tmp/mesh server --host 0.0.0.0 --port 8765
```

Client CLI:

```bash
python -m capability_mesh.cli client --url http://127.0.0.1:8765 health
python -m capability_mesh.cli client --url http://127.0.0.1:8765 agent-card
python -m capability_mesh.cli client --url http://127.0.0.1:8765 nodes
python -m capability_mesh.cli client --url http://127.0.0.1:8765 register capability-manifest.yaml
python -m capability_mesh.cli client --url http://127.0.0.1:8765 post-task task-contract.yaml
python -m capability_mesh.cli client --url http://127.0.0.1:8765 route-task task-contract.yaml --required-tool terminal
python -m capability_mesh.cli client --url http://127.0.0.1:8765 install
python -m capability_mesh.cli client --url http://127.0.0.1:8765 install --yes --node-id local-node-1 --task-type test_running --tool terminal --allow-auto-accept --keep-online
python -m capability_mesh.cli client --url http://127.0.0.1:8765 heartbeat local-node-1
python -m capability_mesh.cli client --url http://127.0.0.1:8765 heartbeat-loop local-node-1 --interval 30
python -m capability_mesh.cli client --url http://127.0.0.1:8765 loop capability-manifest.yaml --interval 30 --run-next
python -m capability_mesh.cli client --url http://127.0.0.1:8765 send-a2a --text "hello mesh"
python -m capability_mesh.cli client --url http://127.0.0.1:8765 send-a2a --text "image" --image screenshot.png --mime-type image/png
python -m capability_mesh.cli client --url http://127.0.0.1:8765 poll local-node-1
python -m capability_mesh.cli client --url http://127.0.0.1:8765 run-next capability-manifest.yaml
```

HTTP endpoints:

- `GET /health`
- `GET /.well-known/agent-card.json`, `GET /agent-card.json`, `GET /api/agent-card`
- `GET /api/nodes`, `GET /api/nodes/{node_id}`, `POST /api/nodes`
- `GET /api/nodes/statuses`, `POST /api/nodes/{node_id}/heartbeat`
- `POST /api/a2a/messages`, `POST /api/a2a/tasks/send`, `GET /api/a2a/tasks`
- `GET /api/tasks`, `POST /api/tasks`
- `POST /api/tasks/plan`
- `POST /api/tasks/plan-step`
- `POST /api/tasks/route`
- `GET /api/assignments`, `POST /api/assignments`
- `GET /api/nodes/{node_id}/assignments`
- `POST /api/assignments/{assignment_id}/claim`
- `POST /api/assignments/{assignment_id}/wake`
- `POST /api/assignments/{assignment_id}/complete`
- `GET /api/results`, `POST /api/results`

## Orchestration Decisions

Capability Mesh is the planner/controller. For one parent task, the service may invoke 0..n server-local tools and 0..n nodes. A server-local invocation is an allowlisted deterministic tool call whose result is filtered and recorded locally. A node invocation is a single assigned subtask or partial operation selected by task type, required tools, and capability metadata. A node does not own the whole parent task unless the assigned subtask actually completes it.

Mixed plan steps use explicit kinds: `server_tool_call`, `node_tool_call`, and `orchestration_tool_call`. A planning response maps those to `invoke_server_tool`, `invoke_node`, `orchestration_action`, `completed`, or `no_match`. Server-local builders reject private node transport/dispatch fields and reject non-allowlisted server tools. Nodes never directly call Server tools and never receive Server private tool definitions, node private transport commands, or dispatch commands in assignments.

Server-initiated wake-up is supported only as a notification layer. A node may opt in with `transport.type: webhook` plus private `wake_url`/`wake_token` metadata. When `POST /api/assignments/{assignment_id}/wake` is called, the server sends only an `assignment_available` event containing `schema_version`, `assignment_id`, `node_id`, and `server_url`; the node must still poll, claim, execute locally, and complete the assignment. Public node views continue to expose only `transport.type`, never the wake URL, token, transport command, or dispatch command.

Node heartbeat/status persistence is intentionally narrow. `POST /api/nodes/{node_id}/heartbeat` records a validated node id, `last_seen_at`, and a coarse reported state for local service bookkeeping. Public node APIs and the dashboard derive only `online`, `stale`, `offline`, or `never_seen` from `last_seen_at`; they do not expose environment variables, logs, skills, sessions, transport commands, dispatch commands, wake URLs, tokens, or private runtime details. Polling, claiming, completing, and `run-next` activity refreshes last-seen status through the same server-side status registry.

## A2A Shape

Capability Mesh follows Google's A2A protocol shape as closely as practical with stdlib-only JSON APIs. The Server publishes an Agent Card at `/.well-known/agent-card.json`. Clients send envelopes to `POST /api/a2a/messages` with `role` (`user` or `agent`) and `parts`. Supported part shapes are text parts, file parts, and data parts. File parts can carry image content as base64 `bytes` or a `uri` plus `mimeType`. The Server responds with a task envelope containing `id`, `contextId`, `status`, `history`, and `artifacts`.

Public A2A surfaces are validated with Google A2A SDK models where practical: AgentCard, SendMessageResponse, Task, ListTasksResponse, StreamResponse, and TaskPushNotificationConfig. Node AgentCards expose only public capabilities and supported interface URLs; private command, token, wake, session, memory, and dispatch data are excluded.

Production runtime records are intentionally separate from public A2A Tasks. Async message execution persists lifecycle metadata under `runtime/tasks`, while public task state remains under `a2a-tasks` and keeps A2A Task semantics. Push delivery attempts are recorded under `push-deliveries` with redacted auth metadata. Structured audit is JSONL and redacts secret-looking headers/body fields.

Policy files live at `CAPABILITY_MESH_HOME/policy.yaml`, `policy.yml`, or `policy.json`. Mutating Hub routes and relay are checked before execution. A missing policy file defaults to allow for development compatibility; production deployments should install explicit deny-by-default policy.

Relay is a Capability Mesh custom HTTP forwarding layer. It preserves A2A JSON payload semantics and maps unavailable targets to a JSON `502`. `GET /relay/pull/nodes/{node_id}` is only a custom long-poll placeholder for future tunnel work and is not an official A2A binding.

This A2A surface is intentionally a content-transfer/API shape, not a private state channel. Agent Cards and public node views do not expose local skills, memory, sessions, logs, environment variables, secrets, wake URLs, tokens, transport commands, or dispatch commands.

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
