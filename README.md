# Capability Mesh

Capability Mesh 是一个 privacy-first 的分布式 A2A Agent 网络实验库。

核心架构已经从传统的 Server / Client 改为 Hub / Node：

- Hub：负责节点注册、AgentCard 发现、能力检索、Relay URL、策略和审计入口。
- Node：每台机器上的 Agent 节点。每个 Node 既是 A2A Server，也是 A2A Client。
- Relay：当 Node 位于 NAT、防火墙或个人电脑后面时，Hub 可以暴露可访问的 relay AgentCard URL，把 A2A 请求转发到目标 Node。

Capability Mesh 默认不会读取或暴露本地 memory、session、raw logs、reasoning traces、environment variables、local skills、token 或 secrets。

Production note: HTTP relay, gRPC helper, and long-poll tunnel APIs in this repository are Capability Mesh deployment bindings unless explicitly described as A2A SDK models. Public A2A payloads are validated with Google A2A SDK models where practical, and relay forwarding preserves A2A Task, Message, and Artifact JSON semantics rather than rewriting them.

---

## 1. 安装开发环境

```bash
git clone https://github.com/Omvira/capability-mesh.git
cd capability-mesh

python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
```

如果只想使用 CLI，也可以直接在仓库中运行：

```bash
python3 -m capability_mesh.cli --help
```

---

## 2. 基本概念

### Hub

Hub 是中心协调层，不是唯一执行者。它负责：

- 保存 Node 的 AgentCard
- 按 skill / tag 发现 Agent
- 生成 relay URL
- 启动 HTTP 服务和 dashboard
- 提供兼容旧接口的 registry / task API

### Node

Node 是实际执行能力的节点。一个 Node 应该具备两个身份：

```text
Node = A2A Server + A2A Client
```

它可以：

- 暴露自己的 AgentCard
- 被其他 Node 通过 A2A 调用
- 主动调用其他 Node
- 调用本地 Hermes / Codex / OpenCode / MCP / Shell / Python Agent 等适配器

### AgentCard

AgentCard 是 A2A 的能力发现入口。Capability Mesh 中：

- Hub 有自己的 Hub AgentCard
- 每个 Node 也有自己的 Node AgentCard
- Node AgentCard 只暴露公开能力，不暴露本机私有执行细节

---

## 3. 启动 Hub

本地启动：

```bash
python3 -m capability_mesh.cli --mesh-home ~/.capability-mesh hub start \
  --host 127.0.0.1 \
  --port 8765
```

如果要让可信内网 / VPN 中的其他机器访问：

```bash
python3 -m capability_mesh.cli --mesh-home ~/.capability-mesh hub start \
  --host 0.0.0.0 \
  --port 8765
```

安全建议：只有在可信网络、VPN 或 HTTPS 反向代理后面才使用 `0.0.0.0`。

检查 Hub：

```bash
curl -fsS http://<HUB_HOST>:8765/health
```

查看 Hub AgentCard：

```bash
curl -fsS http://<HUB_HOST>:8765/.well-known/agent-card.json
```

生产环境应设置 bearer token，而不是把 Hub 暴露为 unauthenticated mutating API：

```bash
export CAPABILITY_MESH_AUTH_TOKEN='[REDACTED]'
python3 -m capability_mesh.cli --mesh-home /var/lib/capability-mesh hub start \
  --host 127.0.0.1 \
  --port 8765
```

`--auth-token` 也可显式传入；如果未传入，Hub 会使用 `CAPABILITY_MESH_AUTH_TOKEN`。

兼容旧命令仍然可用：

```bash
python3 -m capability_mesh.cli --mesh-home ~/.capability-mesh server \
  --host 127.0.0.1 \
  --port 8765
```

---

## 4. 创建 Node manifest

Node manifest 描述一个节点愿意公开的能力。

示例：

```bash
python3 -m capability_mesh.cli manifest \
  --node-id node-a \
  --display-name "Node A" \
  --task-type code_review \
  --task-type test_running \
  --tool python \
  --tool git \
  --output node-a.yaml
```

manifest 中应该只写公开能力标签，例如：

- `code_review`
- `test_running`
- `python_debugging`
- `python`
- `git`
- `browser`
- `mcp`

不要写入：

- API key
- token
- 密码
- 本地绝对私密路径
- 环境变量
- memory / session / raw logs
- 本地 skills 内容

验证 manifest：

```bash
python3 -m capability_mesh.cli validate node-a.yaml --kind manifest
```

---

## 5. 生成 Node AgentCard

直接从 manifest 生成 Node AgentCard：

```bash
python3 -m capability_mesh.cli node agent-card \
  --manifest node-a.yaml \
  --public-url http://node-a.example.com/a2a
```

如果 Node 没有公网入口，使用 relay URL：

```bash
python3 -m capability_mesh.cli node agent-card \
  --manifest node-a.yaml \
  --relay-url https://mesh.example.com/relay/nodes/node-a/a2a
```

Node AgentCard 会包含：

- Node 名称
- Node URL
- HTTP+JSON A2A interface
- skills
- input/output modes
- streaming / push notification 能力声明

不会包含：

- `transport.command`
- `dispatch_command`
- `wake_token`
- secrets
- private runtime state

---

## 6. 将 Node 注册到 Hub

如果 Node 有直接可访问地址：

```bash
python3 -m capability_mesh.cli --mesh-home ~/.capability-mesh hub register-node \
  --manifest node-a.yaml
```

如果 Node 需要通过 Hub relay 被访问：

```bash
python3 -m capability_mesh.cli --mesh-home ~/.capability-mesh hub register-node \
  --manifest node-a.yaml \
  --relay-base-url https://mesh.example.com
```

上面的命令会生成类似 URL：

```text
https://mesh.example.com/relay/nodes/node-a/a2a
```

列出 Hub 已知 Agent：

```bash
python3 -m capability_mesh.cli --mesh-home ~/.capability-mesh hub agents
```

按 skill 查询：

```bash
python3 -m capability_mesh.cli --mesh-home ~/.capability-mesh hub agents \
  --skill code_review
```

---

## 7. 节点互相通信的推荐模式

### 模式 A：直接 A2A 通信

如果两个 Node 网络互通：

```text
Node A -> Node B A2A endpoint
```

例如 Node B 的 AgentCard URL 是：

```text
https://node-b.example.com/a2a
```

Node A 可以直接使用 A2A Client 调用 Node B。库内提供最小 Node-side helper：`capability_mesh.node.client.NodeA2AClient`。它读取目标 AgentCard 的 `supportedInterfaces[].url`，向 `{url}/message:send` 发送 SDK-validated A2A message envelope，并校验 `SendMessageResponse`。

适用场景：

- 同一内网
- VPN / Tailscale
- 两台服务器都有公网 HTTPS endpoint

### 模式 B：通过 Hub 发现，直接调用

```text
Node A -> Hub: search skill code_review
Hub -> Node A: return Node B AgentCard
Node A -> Node B: A2A call
```

Hub 只负责发现，不参与任务执行。

### 模式 C：通过 Hub Relay 通信

如果 Node B 在 NAT / 防火墙 / 个人 PC 后面：

```text
Node A -> Hub relay URL -> reverse tunnel -> Node B
```

对 Node A 来说，它仍然是在调用 Node B 的 AgentCard URL。Hub 只负责转发，不应该改变 A2A Task ID 或协议语义。

Hub relay endpoint 为 `POST /relay/nodes/{node_id}/a2a/{operation}`。它是 Capability Mesh 自定义 HTTP relay，不是官方 A2A transport binding。目标不可达时返回 JSON `502 {"error":"relay target unavailable"}`。

当前还提供 documented placeholder：`GET /relay/pull/nodes/{node_id}`。该接口返回空 long-poll 队列结构，`binding` 标记为 `custom-long-poll-placeholder`。它用于后续 tunnel/pull relay 生产化设计，不应被描述为官方 A2A binding。

---

## 8. A2A 消息测试

向 Hub 的 A2A Protocol 1.0 HTTP+JSON endpoint 发送文本消息：

```bash
python3 -m capability_mesh.cli client \
  --url http://<HUB_HOST>:8765 \
  send-a2a --text "hello mesh"
```

等价 HTTP 请求：

```bash
curl -X POST http://<HUB_HOST>:8765/message:send \
  -H 'Content-Type: application/a2a+json' \
  -H 'Accept: application/a2a+json' \
  -d '{
    "message": {
      "role": "ROLE_USER",
      "parts": [
        {"text": "hello mesh"}
      ]
    }
  }'
```

发送图片 FilePart：

```bash
python3 -m capability_mesh.cli client \
  --url http://<HUB_HOST>:8765 \
  send-a2a \
  --text "inspect image" \
  --image /path/to/example.png \
  --mime-type image/png
```

当前 stdlib HTTP+JSON endpoint 会返回通过官方 `a2a-sdk` protobuf model 校验的 `SendMessageResponse`：

```json
{
  "task": {
    "id": "a2a-...",
    "contextId": "a2a-...",
    "status": {
      "state": "TASK_STATE_COMPLETED"
    },
    "history": [],
    "artifacts": []
  }
}
```

---

## 9. 兼容旧 Client 工作循环

旧的 client 命令仍可用于轻量远程节点注册、heartbeat 和 assignment 轮询。

交互式安装：

```bash
python3 -m capability_mesh.cli client \
  --url http://<HUB_HOST>:8765 \
  install
```

非交互式注册并发送一次 heartbeat：

```bash
python3 -m capability_mesh.cli client \
  --url http://<HUB_HOST>:8765 \
  install \
  --yes \
  --node-id node-a \
  --display-name "Node A" \
  --task-type code_review \
  --tool python \
  --once
```

保持在线：

```bash
python3 -m capability_mesh.cli client \
  --url http://<HUB_HOST>:8765 \
  heartbeat-loop node-a \
  --interval 30
```

使用 manifest 启动工作循环：

```bash
python3 -m capability_mesh.cli client \
  --url http://<HUB_HOST>:8765 \
  loop ~/.capability-mesh/client/node-a.manifest.json \
  --interval 30 \
  --run-next
```

注意：这是旧的 assignment/poll 模式，适合兼容现有功能。新的分布式架构推荐使用 Hub / Node / AgentCard / A2A 通信模型。

---

## 10. HTTP API 速查

Hub / service 常用端点：

```text
GET  /health
GET  /.well-known/agent-card.json
GET  /agent-card.json
GET  /api/agent-card

GET  /api/nodes
GET  /api/nodes/{node_id}
POST /api/nodes
GET  /api/nodes/statuses
POST /api/nodes/{node_id}/heartbeat

GET  /tasks
GET  /tasks/{task_id}
POST /tasks/{task_id}:cancel
POST /message:send
POST /message:stream
POST /a2a/jsonrpc
GET  /tasks/{task_id}/push-notification-configs
POST /tasks/{task_id}/push-notification-configs
GET  /relay/pull/nodes/{node_id}
POST /api/a2a/messages
POST /api/a2a/tasks/send
GET  /api/a2a/tasks

GET  /api/tasks
POST /api/tasks
POST /api/tasks/plan
POST /api/tasks/plan-step
POST /api/tasks/route

GET  /api/assignments
POST /api/assignments
GET  /api/nodes/{node_id}/assignments
POST /api/assignments/{assignment_id}/claim
POST /api/assignments/{assignment_id}/wake
POST /api/assignments/{assignment_id}/complete

GET  /api/results
POST /api/results
```

Hub 的 A2A AgentCard 会声明 `streaming=true` 和 `pushNotifications=true`。`POST /message:stream` 使用 SSE 返回官方 `StreamResponse` 事件；`/tasks/{task_id}/push-notification-configs` 持久化并返回官方 `TaskPushNotificationConfig`；当异步任务完成时 Hub 会向已配置 webhook POST 官方 `Task` 对象；`POST /a2a/jsonrpc` 支持 `message/send`、`tasks/get`、`tasks/list`、`tasks/cancel`。所有 A2A 响应都会通过官方 `a2a-sdk` protobuf model 校验。

异步 long-running 任务：在 message metadata 中传入 `capabilityMesh.async=true`，Hub 会先返回 `202` 和 `TASK_STATE_WORKING`，后台完成后更新为 `TASK_STATE_COMPLETED`，并触发 push webhook。Hub 使用 bounded durable worker runtime，生命周期记录保存在 `runtime/tasks`，包含 transitions、attempts、timestamps、errors，并在 server shutdown 时安全关闭 worker pool。

```json
{
  "message": {
    "role": "ROLE_USER",
    "parts": [{"text": "run async"}],
    "metadata": {"capabilityMesh": {"async": true, "delaySeconds": 0.5}}
  }
}
```

生产部署建议：Hub 使用 `--auth-token` 或 `CAPABILITY_MESH_AUTH_TOKEN` 开启 Bearer token 保护和 `audit.log` 审计；Node 可用 `capability-mesh node start --manifest node-a.yaml` 独立启动为 A2A Server；Hub relay 可通过 `/relay/nodes/{node_id}/a2a/...` 转发到已注册 Node；`deploy/` 目录提供 nginx TLS reverse proxy、systemd service、docker-compose 示例；`capability_mesh/grpc/a2a.proto` 提供 gRPC binding 契约。

生产 baseline：

- Hub 监听 `127.0.0.1` 并放在 TLS reverse proxy 后，或只在可信 VPN/LAN 中开放。
- `CAPABILITY_MESH_AUTH_TOKEN` 从 env file 或 secret manager reference 注入；不要提交真实 token。
- `CAPABILITY_MESH_HOME` 使用持久化目录，例如 `/var/lib/capability-mesh`。
- 在 `CAPABILITY_MESH_HOME` 安装显式 `policy.yaml`。没有 policy 文件时为了本地开发兼容默认 allow；生产建议 deny-by-default。
- `audit.log` 是 JSONL，包含 timestamp、action、status、path、remote_addr、可选 node_id、redacted headers 和 redacted body。
- push delivery attempts 保存在 `push-deliveries`，包含 timeout/retry/attempt status，bearer credentials 不会写入 audit 或 delivery records。
- gRPC 是 optional generated-free adapter。默认 `capability_mesh.grpc.binding` 提供 SDK-validated JSON helpers；需要生成 concrete stubs 时安装 `.[grpc]` 并从 `capability_mesh/grpc/a2a.proto` 生成。

示例 `policy.yaml`：

```yaml
default: deny
allow:
  - message:send
  - message:stream
  - tasks/*
  - api/nodes
  - api/nodes/*/heartbeat
deny:
  - relay/*
```

---

## 11. MCP stdio 适配器

Capability Mesh 可以作为 MCP stdio server 暴露给支持 MCP 的客户端。

安装 MCP 依赖：

```bash
pip install -e '.[mcp]'
```

启动：

```bash
python3 -m capability_mesh.cli mcp-server \
  --mesh-url http://<HUB_HOST>:8765
```

MCP 客户端配置示例：

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
        "http://<HUB_HOST>:8765"
      ],
      "env": {}
    }
  }
}
```

可用 MCP tools 包括：

- `list_clients`
- `get_client`
- `call_client_async`
- `create_assignment`
- `get_assignment_status`
- `send_a2a_message`

MCP 适配器只返回 JSON-serializable 的安全字段，不暴露 `wake_token`、`wake_url`、`dispatch_command`、transport command、private logs、memory、session、environment variables 或 secrets。

---

## 12. Python API 示例

```python
from capability_mesh.core import build_default_capability_manifest
from capability_mesh.node import build_node_agent_card
from capability_mesh.hub import register_node_agent_card, list_agent_cards

manifest = build_default_capability_manifest(
    node_id="node-a",
    display_name="Node A",
    task_types=["code_review"],
    tools_available=["python", "git"],
)

card = build_node_agent_card(
    manifest,
    relay_url="https://mesh.example.com/relay/nodes/node-a/a2a",
)

print(card["name"])
print(card["supportedInterfaces"][0]["url"])

register_node_agent_card(
    manifest,
    relay_base_url="https://mesh.example.com",
)

print(list_agent_cards())
```

HTTP client 示例：

```python
from capability_mesh.client import CapabilityMeshClient

client = CapabilityMeshClient("http://<HUB_HOST>:8765")
print(client.health())
print(client.agent_card())
print(client.list_nodes())
```

---

## 13. 隐私模型

Capability Mesh 默认不上传、不暴露：

- local skills
- memory
- session history
- reasoning traces
- raw logs
- environment variables
- secrets
- token
- password
- API key
- private transport command
- dispatch command
- wake token

公开 Node 信息应只包含：

- `node_id`
- `display_name`
- public AgentCard URL
- public skills
- public capability labels
- heartbeat 派生在线状态

Task result 会通过 allow/deny 字段过滤，secret-like 字符串会被 redaction。

Team/public skill contribution 必须显式 human consent。

---

## 14. 常用命令汇总

```bash
# Help
python3 -m capability_mesh.cli --help
python3 -m capability_mesh.cli hub --help
python3 -m capability_mesh.cli node --help

# Start Hub
python3 -m capability_mesh.cli --mesh-home ~/.capability-mesh hub start --host 127.0.0.1 --port 8765

# Build manifest
python3 -m capability_mesh.cli manifest --node-id node-a --display-name "Node A" --task-type code_review --tool python -o node-a.yaml

# Validate manifest
python3 -m capability_mesh.cli validate node-a.yaml --kind manifest

# Build Node AgentCard
python3 -m capability_mesh.cli node agent-card --manifest node-a.yaml --relay-url https://mesh.example.com/relay/nodes/node-a/a2a

# Register Node with Hub registry
python3 -m capability_mesh.cli --mesh-home ~/.capability-mesh hub register-node --manifest node-a.yaml --relay-base-url https://mesh.example.com

# List Agents
python3 -m capability_mesh.cli --mesh-home ~/.capability-mesh hub agents

# Search Agents by skill
python3 -m capability_mesh.cli --mesh-home ~/.capability-mesh hub agents --skill code_review

# A2A message smoke test
python3 -m capability_mesh.cli client --url http://127.0.0.1:8765 send-a2a --text "hello mesh"

# Run tests
pytest -q
```

---

## 15. 设计原则

Capability Mesh 的目标不是把一台中心服务器变成所有任务的执行者，而是构建一个分布式 A2A Node 网络：

```text
Node A = A2A Client + A2A Server
Node B = A2A Client + A2A Server
Hub    = Registry + Discovery + Relay + Policy + Audit
```

当节点互相可达时，节点之间直接用 A2A 通信。

当节点不可达时，Hub 提供 relay URL，但 relay 只做网络转发，不改变 A2A Task ID、Message、Artifact 或协议语义。
