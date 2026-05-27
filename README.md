# HermesMesh

HermesMesh is an independent, privacy-first capability mesh core extracted from Hermes Agent.

It provides schema validation, local node registry helpers, dispatch prompt construction,
result filtering, verification primitives, mixed server/node task orchestration, a standalone CLI, an HTTP service, and a small HTTP client for capability-network experiments.

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

Task orchestration flow:

1. Submit a task with `POST /api/tasks`.
2. Plan the next server-controlled node tool call with `POST /api/tasks/plan`, or plan one mixed step with `POST /api/tasks/plan-step`. The mixed step may invoke an allowlisted server-local tool, assign a node capability tool, return an orchestration action, complete, or report `no_match`.
3. The Server is the parent-task planner over 0..n server-local tools plus 0..n node capability tools. Server-local tools are deterministic and non-dangerous, such as `aggregate_results`, `verify_result`, and `echo_sanitized`; they never perform filesystem, network, or shell execution.
4. A node sends a privacy-safe heartbeat with `POST /api/nodes/{node_id}/heartbeat`, polls `GET /api/nodes/{node_id}/assignments`, claims one assigned tool call/subtask, invokes its local Agent via its private `dispatch_command` or transport command, and completes only that assignment. Poll, claim, and complete refresh last-seen status as node activity. If the node manifest declares `transport.type: webhook`, the server may call `POST /api/assignments/{assignment_id}/wake` to send a minimal `assignment_available` notification to the node's private `wake_url`; the node still performs poll/claim/complete itself.
5. Nodes never directly call Server tools and never receive Server private tool definitions, node transport commands, or dispatch commands as task inputs.
6. The server privacy-filters the result, accumulates the filtered output, records contribution/verification locally, and decides whether the parent task is `completed`, needs `awaiting_more_results`, should `route_next`, or has `no_match`.

`POST /api/tasks/route` remains available as the compatibility route-and-assign endpoint. New orchestration code should prefer `POST /api/tasks/plan-step` or `POST /api/tasks/plan` because HermesMesh is the planner/controller and nodes are callable capability tools, not whole-task owners.

Node prompts explicitly say the node is responsible only for the assigned subtask. Nodes should return JSON containing only the parent task's allowed result fields plus optional boolean `partial` and `needs_more_results` signals. The server never exposes memory, skills, sessions, raw logs, env vars, reasoning traces, transport/dispatch commands, or private execution details in public node views or aggregated results. Dispatch commands remain in the node manifest registry and are used only by the local client running on that node.

Public node status is derived from `last_seen_at` only: recently seen nodes are `online`, older heartbeats become `stale`, expired heartbeats become `offline`, and registered nodes without activity are `never_seen`. Reported heartbeat details are stored only as privacy-safe status metadata and are not exposed as private runtime state.

The Server/Client split is explicit: `server` runs the HermesMesh HTTP service and registry; `client` commands can run independently on another machine and communicate only through JSON APIs. The client detects Server liveness with `GET /health`. The Server detects Client liveness from `POST /api/nodes/{node_id}/heartbeat`, assignment poll/claim/complete activity, and derived public presence.

HermesMesh also exposes a stdlib-only A2A-shaped JSON surface. `GET /.well-known/agent-card.json` returns a privacy-safe Agent Card. `POST /message:send` accepts a message envelope with `role` and `parts`; supported parts are TextPart (`{"text":"..."}`), FilePart (`{"raw":"<base64>","filename":"screenshot.png","mediaType":"image/png"}` or `{"file":{"uri":"...","mimeType":"image/png"}}` for compatibility), and DataPart (`{"data":{...},"mediaType":"application/json"}`). Compatibility endpoints `POST /api/a2a/messages` and `POST /api/a2a/tasks/send` remain available. Responses use an A2A-style `{ "task": ... }` envelope with `status`, `history`, and `artifacts`. Image transfer is represented as a FilePart with either base64 `raw`/legacy `bytes` or a `uri` plus media type.

### Trial Client installer

For a first remote Client, use the interactive installer. It guides the user through Server URL, node id, public task types/tools, optional auto-accept policy, registration, and a foreground heartbeat loop to keep the Client online:

```bash
python3 -m hermes_mesh.cli client --url http://10.0.16.11:8765 install
```

Non-interactive one-shot registration plus one heartbeat:

```bash
python3 -m hermes_mesh.cli client --url http://10.0.16.11:8765 install \
  --yes \
  --node-id remote-client-a \
  --display-name "Remote Client A" \
  --task-type smoke \
  --tool hermes \
  --allow-auto-accept \
  --once
```

Keep the Client online in the foreground:

```bash
python3 -m hermes_mesh.cli client --url http://10.0.16.11:8765 install \
  --yes --node-id remote-client-a --task-type smoke --tool hermes \
  --allow-auto-accept --keep-online --interval 30
```

The generated manifest is saved under `~/.hermes-mesh/client/`. The installer is stdlib-only and can also be fetched directly when published:

```bash
curl -fsSL https://raw.githubusercontent.com/Omvira/HermesMesh/main/scripts/install_client.py | \
  python3 - --mesh-url http://10.0.16.11:8765 --keep-online
```

It does not read or upload local skills, memory, sessions, raw logs, environment variables, credentials, or secrets.

Use the bundled client CLI against a running service:

```bash
python -m hermes_mesh.cli client --url http://127.0.0.1:8765 health
python -m hermes_mesh.cli client --url http://127.0.0.1:8765 agent-card
python -m hermes_mesh.cli client --url http://127.0.0.1:8765 nodes
python -m hermes_mesh.cli client --url http://127.0.0.1:8765 register manifest.yaml
python -m hermes_mesh.cli client --url http://127.0.0.1:8765 post-task task.yaml
python -m hermes_mesh.cli client --url http://127.0.0.1:8765 route-task task.yaml --required-tool python
python -m hermes_mesh.cli client --url http://127.0.0.1:8765 install
python -m hermes_mesh.cli client --url http://127.0.0.1:8765 install --yes --node-id local-node --task-type smoke --tool hermes --allow-auto-accept --keep-online
python -m hermes_mesh.cli client --url http://127.0.0.1:8765 heartbeat local-node
python -m hermes_mesh.cli client --url http://127.0.0.1:8765 heartbeat-loop local-node --interval 30
python -m hermes_mesh.cli client --url http://127.0.0.1:8765 loop manifest.yaml --interval 30 --run-next
python -m hermes_mesh.cli client --url http://127.0.0.1:8765 send-a2a --text "hello mesh"
python -m hermes_mesh.cli client --url http://127.0.0.1:8765 send-a2a --text "inspect image" --image screenshot.png --mime-type image/png
python -m hermes_mesh.cli client --url http://127.0.0.1:8765 wake task-001-local-node
python -m hermes_mesh.cli client --url http://127.0.0.1:8765 poll local-node
python -m hermes_mesh.cli client --url http://127.0.0.1:8765 claim task-001-local-node --node-id local-node
python -m hermes_mesh.cli client --url http://127.0.0.1:8765 complete task-001-local-node result.yaml --node-id local-node
python -m hermes_mesh.cli client --url http://127.0.0.1:8765 run-next manifest.yaml
```

Python client:

```python
from hermes_mesh.client import HermesMeshClient

client = HermesMeshClient("http://127.0.0.1:8765")
print(client.health())
print(client.list_nodes())
```

Webhook wake-up is notification-only, not remote execution. The wake payload contains only `schema_version`, `event`, `assignment_id`, `node_id`, and `server_url`. Public node views expose only `transport.type`; they never expose `wake_url`, `wake_token`, `dispatch_command`, or transport commands.

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

### Register a wake-up capable remote client

For server-initiated wake-up, the remote client must expose a small HTTP endpoint that the HermesMesh server can reach. This wake endpoint is notification-only: after receiving `assignment_available`, the client still calls `poll`, `claim`, runs its local agent/tools, and `complete`.

On the remote machine, first run your wake receiver/client adapter, for example on a trusted LAN/VPN address:

```bash
# Example only: your adapter should verify X-HermesMesh-Wake-Token, then run poll/claim/complete.
python3 wake_client_adapter.py --listen 0.0.0.0 --port 9876 \
  --mesh-url http://10.0.16.11:8765 \
  --node-id remote-node-a
```

Then register that node with `transport.type: webhook` by passing `--wake-url` to the one-shot registration script:

```bash
curl -fsSL https://raw.githubusercontent.com/Omvira/HermesMesh/main/scripts/register_node.py | \
  python3 - \
    --mesh-url http://10.0.16.11:8765 \
    --node-id remote-node-a \
    --display-name "Remote Node A" \
    --task-type code_review \
    --tool hermes \
    --tool python \
    --wake-url http://<remote-client-lan-ip>:9876/wake \
    --wake-token '<shared-random-token>'
```

Use `--dry-run --print-manifest` first if you want to inspect the exact manifest before registering. The manifest still contains no local skills, memory, sessions, logs, env vars, or secrets except the explicit wake token you choose to send. Public node APIs expose only `transport.type`, never `wake_url` or `wake_token`.

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
