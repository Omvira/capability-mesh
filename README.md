# Capability Mesh

Capability Mesh is an independent, privacy-first capability mesh core extracted from Hermes Agent.

It provides schema validation, local node registry helpers, dispatch prompt construction,
result filtering, verification primitives, mixed server/node task orchestration, a standalone CLI, an HTTP service, and a small HTTP client for capability-network experiments.

Hermes Agent is only one possible adapter/node runtime. The mesh core does not import Hermes internals and must not read or expose local memory, sessions, raw logs, reasoning traces, environment variables, or local skills.



## 中文教程：Server 与 Client 快速使用

本节使用占位地址，避免暴露任何真实机器、局域网或个人环境信息。请根据自己的部署替换：

- Server 监听地址：`<SERVER_HOST>`，例如 `localhost`、内网域名、VPN 地址或反向代理域名。
- Server 端口：`<SERVER_PORT>`，默认示例为 `8765`。
- Server URL：`http://<SERVER_HOST>:<SERVER_PORT>`。
- Client 节点 ID：`<CLIENT_NODE_ID>`，例如 `remote-client-a`。

安全建议：

- 只在可信本机、可信内网或 VPN 中使用 `--host 0.0.0.0`。
- 如果需要公网访问，请放在 HTTPS 反向代理、认证层、VPN 或隧道后面。
- 不要把 token、密码、API key、私有日志、session、memory、skills、环境变量或本机绝对路径写进 manifest、README、issue 或聊天记录。
- Client 公开的是“能力标签”，不是私有数据。`task_types` 和 `tools_available` 应只写泛化能力，例如 `code_review`、`test_running`、`python`、`git`。

### 1. 启动 Server

在 Server 机器上进入 Capability Mesh 项目目录，启动 HTTP 服务：

```bash
python3 -m capability_mesh.cli --mesh-home ~/.capability-mesh server \
  --host localhost \
  --port 8765
```

如果要让同一可信内网或 VPN 中的 Client 接入，可以监听所有网卡：

```bash
python3 -m capability_mesh.cli --mesh-home ~/.capability-mesh server \
  --host 0.0.0.0 \
  --port 8765
```

检查 Server 是否在线：

```bash
curl -fsS http://<SERVER_HOST>:<SERVER_PORT>/health
```

正常会返回类似：

```json
{ "ok": true }
```

查看 A2A Agent Card：

```bash
curl -fsS http://<SERVER_HOST>:<SERVER_PORT>/.well-known/agent-card.json
```

### 2. 使用交互式 Client 安装器接入

在 Client 机器上运行：

```bash
python3 -m capability_mesh.cli client \
  --url http://<SERVER_HOST>:<SERVER_PORT> \
  install
```

安装器会逐步询问：

- Client node id
- display name
- 可公开的 task types
- 可公开的 tools/capability labels
- 是否允许自动接受特定任务类型
- 是否生成本地 manifest
- 是否立即启动 heartbeat loop 让 Client 保持在线

生成的 manifest 默认保存到：

```text
~/.capability-mesh/client/<CLIENT_NODE_ID>.manifest.json
```

### 3. 非交互式注册并保持在线

如果你已经知道要公开的能力标签，可以一行注册并启动前台 heartbeat loop：

```bash
python3 -m capability_mesh.cli client \
  --url http://<SERVER_HOST>:<SERVER_PORT> \
  install \
  --yes \
  --node-id <CLIENT_NODE_ID> \
  --display-name "Remote Client" \
  --task-type code_review \
  --task-type test_running \
  --tool hermes \
  --tool python \
  --allow-auto-accept \
  --keep-online \
  --interval 30
```

只注册并发送一次 heartbeat，不常驻在线：

```bash
python3 -m capability_mesh.cli client \
  --url http://<SERVER_HOST>:<SERVER_PORT> \
  install \
  --yes \
  --node-id <CLIENT_NODE_ID> \
  --task-type code_review \
  --tool hermes \
  --once
```

### 4. 直接使用独立安装脚本

如果不想先安装整个项目，可以使用 stdlib-only 的安装脚本。发布后可以用：

```bash
curl -fsSL https://raw.githubusercontent.com/Omvira/CapabilityMesh/main/scripts/install_client.py | \
  python3 - \
    --mesh-url http://<SERVER_HOST>:<SERVER_PORT> \
    --node-id <CLIENT_NODE_ID> \
    --task-type code_review \
    --tool hermes \
    --keep-online
```

本地仓库中也可以直接运行：

```bash
python3 scripts/install_client.py \
  --mesh-url http://<SERVER_HOST>:<SERVER_PORT> \
  --node-id <CLIENT_NODE_ID> \
  --task-type code_review \
  --tool hermes \
  --keep-online
```

### 5. 让 Client 以 systemd user service 常驻

在 Linux 用户环境中，可以让安装脚本写入 user-level systemd service：

```bash
python3 scripts/install_client.py \
  --mesh-url http://<SERVER_HOST>:<SERVER_PORT> \
  --node-id <CLIENT_NODE_ID> \
  --task-type code_review \
  --tool hermes \
  --install-systemd
```

然后按脚本提示执行：

```bash
systemctl --user daemon-reload
systemctl --user enable --now capability-mesh-client-<CLIENT_NODE_ID>.service
```

查看状态：

```bash
systemctl --user status capability-mesh-client-<CLIENT_NODE_ID>.service
```

注意：如果使用 `curl | python3 -` 方式运行，脚本来自 stdin，不能可靠生成 systemd 的 `ExecStart` 路径。需要先把脚本保存为本地文件再使用 `--install-systemd`。

### 6. 验证 Client 已接入

从任意能访问 Server 的机器查看节点列表：

```bash
curl -fsS http://<SERVER_HOST>:<SERVER_PORT>/api/nodes
```

查看某个 Client：

```bash
curl -fsS http://<SERVER_HOST>:<SERVER_PORT>/api/nodes/<CLIENT_NODE_ID>
```

如果 Client 正在发送 heartbeat，公开状态中会出现类似：

```json
{
  "online_status": {
    "status": "online",
    "label": "online"
  }
}
```

### 7. 测试 A2A 文本和图片消息

发送文本：

```bash
python3 -m capability_mesh.cli client \
  --url http://<SERVER_HOST>:<SERVER_PORT> \
  send-a2a --text "hello mesh"
```

发送图片：

```bash
python3 -m capability_mesh.cli client \
  --url http://<SERVER_HOST>:<SERVER_PORT> \
  send-a2a \
  --text "inspect image" \
  --image /path/to/example.png \
  --mime-type image/png
```

等价 HTTP API：

```bash
curl -X POST http://<SERVER_HOST>:<SERVER_PORT>/message:send \
  -H 'Content-Type: application/a2a+json' \
  -H 'Accept: application/a2a+json' \
  -d '{
    "message": {
      "role": "ROLE_USER",
      "parts": [
        {"text": "hello mesh"},
        {
          "raw": "<BASE64_IMAGE_BYTES>",
          "filename": "example.png",
          "mediaType": "image/png"
        }
      ]
    }
  }'
```

### 8. Client 工作循环

如果已经有 manifest 文件，也可以直接启动 Client loop：

```bash
python3 -m capability_mesh.cli client \
  --url http://<SERVER_HOST>:<SERVER_PORT> \
  loop ~/.capability-mesh/client/<CLIENT_NODE_ID>.manifest.json \
  --interval 30
```

如果希望 Client 自动领取并执行分配给它的任务，加上：

```bash
--run-next
```

完整示例：

```bash
python3 -m capability_mesh.cli client \
  --url http://<SERVER_HOST>:<SERVER_PORT> \
  loop ~/.capability-mesh/client/<CLIENT_NODE_ID>.manifest.json \
  --interval 30 \
  --run-next
```

这个循环会：

1. 轮询 `/health` 检测 Server 是否在线。
2. 定期发送 heartbeat，让 Server 看到 Client online。
3. 可选地 poll/claim/execute/complete 分配给该 Client 的任务。

### 9. 隐私边界

Capability Mesh Client onboarding 默认不上传：

- 本地 skills
- memory
- session history
- reasoning trace
- raw logs
- environment variables
- secrets、token、password、API key
- 本机私有路径或本地命令输出

公开节点信息应只包含：

- `node_id`
- `display_name`
- `task_types`
- `tools_available`
- 安全的 resource 标签
- heartbeat 派生的在线状态

### 10. 通过 MCP stdio 连接客户端

Capability Mesh 可以作为 MCP stdio server 暴露给支持 MCP 的客户端，包括 legacy Hermes adapter。这个适配器只调用已有 Capability Mesh HTTP API，并只返回 JSON-serializable 的安全字段；不会暴露 `wake_token`、`wake_url`、`dispatch_command`、transport command、私有日志、memory、session、环境变量或 secrets。

先确保 Python MCP SDK 已安装：

```bash
python3 -m pip install '.[mcp]'
# 或者只安装 SDK：
python3 -m pip install mcp
```

本地测试启动：

```bash
python3 -m capability_mesh.cli mcp-server \
  --mesh-url http://<SERVER_HOST>:<SERVER_PORT>
```

如果没有安装 MCP SDK，命令会明确报错并退出，不会静默失败。

MCP 客户端配置示例，仅使用占位符，不要写真实本机路径、真实局域网 IP、token 或 secret：

```json
{
  "mcp_servers": {
    "capability-mesh": {
      "command": "<PYTHON_EXECUTABLE>",
      "args": [
        "-m",
        "capability_mesh.cli",
        "mcp-server",
        "--mesh-url",
        "http://<SERVER_HOST>:<SERVER_PORT>"
      ],
      "env": {}
    }
  }
}
```

可用 MCP tools：

- `list_clients`：列出公开 Client/Node。
- `get_client`：查看单个公开 Client/Node。
- `call_client_async`：通过任务 contract 创建异步 assignment。
- `create_assignment`：`call_client_async` 的别名，语义更直接。
- `get_assignment_status`：查看 assignment 当前状态。
- `send_a2a_message`：发送 A2A-like message envelope。


## Install for development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
```

## CLI

```bash
capability-mesh --help
python -m capability_mesh.cli manifest --node-id local-node --display-name Local --task-type code_review --tool python
python -m capability_mesh.cli --mesh-home /tmp/mesh register manifest.yaml
python -m capability_mesh.cli --mesh-home /tmp/mesh list --json
python -m capability_mesh.cli --mesh-home /tmp/mesh post-task task.yaml
python -m capability_mesh.cli --mesh-home /tmp/mesh route-task task.yaml --json
```

## Standalone service and client

Run the Capability Mesh service from this project; it exposes both the dashboard and JSON APIs:

```bash
python -m capability_mesh.cli --mesh-home /tmp/mesh server --host localhost --port 8765
# For trusted LAN/VPN access only:
python -m capability_mesh.cli --mesh-home /tmp/mesh server --host 0.0.0.0 --port 8765
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

`POST /api/tasks/route` remains available as the compatibility route-and-assign endpoint. New orchestration code should prefer `POST /api/tasks/plan-step` or `POST /api/tasks/plan` because Capability Mesh is the planner/controller and nodes are callable capability tools, not whole-task owners.

Node prompts explicitly say the node is responsible only for the assigned subtask. Nodes should return JSON containing only the parent task's allowed result fields plus optional boolean `partial` and `needs_more_results` signals. The server never exposes memory, skills, sessions, raw logs, env vars, reasoning traces, transport/dispatch commands, or private execution details in public node views or aggregated results. Dispatch commands remain in the node manifest registry and are used only by the local client running on that node.

Public node status is derived from `last_seen_at` only: recently seen nodes are `online`, older heartbeats become `stale`, expired heartbeats become `offline`, and registered nodes without activity are `never_seen`. Reported heartbeat details are stored only as privacy-safe status metadata and are not exposed as private runtime state.

The Server/Client split is explicit: `server` runs the Capability Mesh HTTP service and registry; `client` commands can run independently on another machine and communicate only through JSON APIs. The client detects Server liveness with `GET /health`. The Server detects Client liveness from `POST /api/nodes/{node_id}/heartbeat`, assignment poll/claim/complete activity, and derived public presence.

Capability Mesh also exposes a stdlib-only A2A-shaped JSON surface. `GET /.well-known/agent-card.json` returns a privacy-safe Agent Card. `POST /message:send` accepts a message envelope with `role` and `parts`; supported parts are TextPart (`{"text":"..."}`), FilePart (`{"raw":"<base64>","filename":"screenshot.png","mediaType":"image/png"}` or `{"file":{"uri":"...","mimeType":"image/png"}}` for compatibility), and DataPart (`{"data":{...},"mediaType":"application/json"}`). Compatibility endpoints `POST /api/a2a/messages` and `POST /api/a2a/tasks/send` remain available. Responses use an A2A-style `{ "task": ... }` envelope with `status`, `history`, and `artifacts`. Image transfer is represented as a FilePart with either base64 `raw`/legacy `bytes` or a `uri` plus media type.

### Trial Client installer

For a first remote Client, use the interactive installer. It guides the user through Server URL, node id, public task types/tools, optional auto-accept policy, registration, and a foreground heartbeat loop to keep the Client online:

```bash
python3 -m capability_mesh.cli client --url http://<SERVER_HOST>:<SERVER_PORT> install
```

Non-interactive one-shot registration plus one heartbeat:

```bash
python3 -m capability_mesh.cli client --url http://<SERVER_HOST>:<SERVER_PORT> install \
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
python3 -m capability_mesh.cli client --url http://<SERVER_HOST>:<SERVER_PORT> install \
  --yes --node-id remote-client-a --task-type smoke --tool hermes \
  --allow-auto-accept --keep-online --interval 30
```

The generated manifest is saved under `~/.capability-mesh/client/` by default. `~/.hermes-mesh/client/` remains available only when selected through legacy `HERMES_MESH_CLIENT_HOME`. The installer is stdlib-only and can also be fetched directly when published:

```bash
curl -fsSL https://raw.githubusercontent.com/Omvira/CapabilityMesh/main/scripts/install_client.py | \
  python3 - --mesh-url http://<SERVER_HOST>:<SERVER_PORT> --keep-online
```

It does not read or upload local skills, memory, sessions, raw logs, environment variables, credentials, or secrets.

Use the bundled client CLI against a running service:

```bash
python -m capability_mesh.cli client --url http://<SERVER_HOST>:<SERVER_PORT> health
python -m capability_mesh.cli client --url http://<SERVER_HOST>:<SERVER_PORT> agent-card
python -m capability_mesh.cli client --url http://<SERVER_HOST>:<SERVER_PORT> nodes
python -m capability_mesh.cli client --url http://<SERVER_HOST>:<SERVER_PORT> register manifest.yaml
python -m capability_mesh.cli client --url http://<SERVER_HOST>:<SERVER_PORT> post-task task.yaml
python -m capability_mesh.cli client --url http://<SERVER_HOST>:<SERVER_PORT> route-task task.yaml --required-tool python
python -m capability_mesh.cli client --url http://<SERVER_HOST>:<SERVER_PORT> install
python -m capability_mesh.cli client --url http://<SERVER_HOST>:<SERVER_PORT> install --yes --node-id local-node --task-type smoke --tool hermes --allow-auto-accept --keep-online
python -m capability_mesh.cli client --url http://<SERVER_HOST>:<SERVER_PORT> heartbeat local-node
python -m capability_mesh.cli client --url http://<SERVER_HOST>:<SERVER_PORT> heartbeat-loop local-node --interval 30
python -m capability_mesh.cli client --url http://<SERVER_HOST>:<SERVER_PORT> loop manifest.yaml --interval 30 --run-next
python -m capability_mesh.cli client --url http://<SERVER_HOST>:<SERVER_PORT> send-a2a --text "hello mesh"
python -m capability_mesh.cli client --url http://<SERVER_HOST>:<SERVER_PORT> send-a2a --text "inspect image" --image screenshot.png --mime-type image/png
python -m capability_mesh.cli client --url http://<SERVER_HOST>:<SERVER_PORT> wake task-001-local-node
python -m capability_mesh.cli client --url http://<SERVER_HOST>:<SERVER_PORT> poll local-node
python -m capability_mesh.cli client --url http://<SERVER_HOST>:<SERVER_PORT> claim task-001-local-node --node-id local-node
python -m capability_mesh.cli client --url http://<SERVER_HOST>:<SERVER_PORT> complete task-001-local-node result.yaml --node-id local-node
python -m capability_mesh.cli client --url http://<SERVER_HOST>:<SERVER_PORT> run-next manifest.yaml
```

Python client:

```python
from capability_mesh.client import CapabilityMeshClient

client = CapabilityMeshClient("http://<SERVER_HOST>:<SERVER_PORT>")
print(client.health())
print(client.list_nodes())
```

Webhook wake-up is notification-only, not remote execution. The wake payload contains only `schema_version`, `event`, `assignment_id`, `node_id`, and `server_url`. Public node views expose only `transport.type`; they never expose `wake_url`, `wake_token`, `dispatch_command`, or transport commands.

Registry home resolution:

1. `--mesh-home`
2. `$CAPABILITY_MESH_HOME`
3. legacy `$HERMES_MESH_HOME`
4. `~/.capability-mesh`

## Register a node without cloning Capability Mesh

A remote client, including a legacy Hermes adapter, can register with a running Capability Mesh service using one stdlib-only Python script fetched over HTTPS. The script submits only a privacy-first capability manifest; it does not read or upload skills, memory, sessions, raw logs, env vars, or secrets.

```bash
curl -fsSL https://raw.githubusercontent.com/Omvira/CapabilityMesh/main/scripts/register_node.py | \
  python3 - \
    --mesh-url http://<SERVER_HOST>:<SERVER_PORT> \
    --node-id capability-node-a \
    --display-name "Capability Node A" \
    --task-type code_review \
    --task-type python_debugging \
    --tool hermes \
    --tool python \
    --tool git
```

Preview the manifest without registering:

```bash
curl -fsSL https://raw.githubusercontent.com/Omvira/CapabilityMesh/main/scripts/register_node.py | \
  python3 - --mesh-url http://<SERVER_HOST>:<SERVER_PORT> --task-type code_review --tool hermes --dry-run
```

After registration, verify from any machine that can reach the service:

```bash
curl -fsSL http://<SERVER_HOST>:<SERVER_PORT>/api/nodes
```

### Register a wake-up capable remote client

For server-initiated wake-up, the remote client must expose a small HTTP endpoint that the Capability Mesh server can reach. This wake endpoint is notification-only: after receiving `assignment_available`, the client still calls `poll`, `claim`, runs its local agent/tools, and `complete`.

On the remote machine, first run your wake receiver/client adapter, for example on a trusted LAN/VPN address:

```bash
# Example only: your adapter should verify X-CapabilityMesh-Wake-Token, accepting X-HermesMesh-Wake-Token only for legacy compatibility, then run poll/claim/complete.
python3 wake_client_adapter.py --listen 0.0.0.0 --port 9876 \
  --mesh-url http://<SERVER_HOST>:<SERVER_PORT> \
  --node-id remote-node-a
```

Then register that node with `transport.type: webhook` by passing `--wake-url` to the one-shot registration script:

```bash
curl -fsSL https://raw.githubusercontent.com/Omvira/CapabilityMesh/main/scripts/register_node.py | \
  python3 - \
    --mesh-url http://<SERVER_HOST>:<SERVER_PORT> \
    --node-id remote-node-a \
    --display-name "Remote Node A" \
    --task-type code_review \
    --tool hermes \
    --tool python \
    --wake-url http://<CLIENT_HOST>:9876/wake \
    --wake-token '<SHARED_WAKE_TOKEN>'
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
