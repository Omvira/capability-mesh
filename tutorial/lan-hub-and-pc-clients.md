# 局域网 Capability Mesh 部署教程

本教程说明如何在一台服务器上启动 Central Hub Server，并让局域网内多台 PC 作为 Capability Mesh Clients/Nodes 接入。适用于所有机器都在同一局域网、可以通过 `192.168.x.x` / `10.x.x.x` 等内网 IP 互相访问的场景。

## 0. 架构说明

```text
你的 Hermes / MCP stdio adapter
        |
        v
Central Hub Server
http://<HUB_LAN_IP>:8765
        |
        +--> PC A Node  http://<PC_A_LAN_IP>:8766
        +--> PC B Node  http://<PC_B_LAN_IP>:8766
        +--> PC C Node  http://<PC_C_LAN_IP>:8766
```

角色说明：

- Central Hub Server：运行在服务器上，负责 AgentCard、注册表、任务路由、审计、policy、push、relay。
- LAN PC Node：运行在每台个人 PC 上，公开自己的 A2A AgentCard 和 A2A HTTP+JSON endpoint。
- Hermes MCP stdio adapter：运行在使用 Hermes 的机器上，把 Hermes 工具调用转成 Capability Mesh HTTP 调用。

重要原则：

- Hub 地址要写局域网内其它机器能访问的地址，例如 `http://192.168.1.10:8765`。
- Node 的 `--public-url` 必须写该 PC 的局域网 IP，例如 `http://192.168.1.23:8766`。
- 不要把 `127.0.0.1` 或 `localhost` 写进公开给 Hub 使用的 Node URL；那只对本机有效。
- 不要把 token、密码、API key 写进 manifest 或 AgentCard。

---

## 1. 服务器端：启动 Central Hub Server

以下步骤在服务器上执行。假设服务器局域网 IP 是：

```text
192.168.1.10
```

请替换成你的实际服务器 IP。

### 1.1 安装系统依赖

Ubuntu / Debian 示例：

```bash
sudo apt-get update
sudo apt-get install -y \
  git curl ca-certificates build-essential \
  python3 python3-venv python3-pip
```

如果系统默认 Python 低于 3.10，建议安装 Python 3.11：

```bash
sudo apt-get install -y python3.11 python3.11-venv python3.11-dev
```

### 1.2 Clone 仓库

```bash
mkdir -p ~/apps
cd ~/apps
git clone https://github.com/Omvira/capability-mesh.git
cd capability-mesh
```

### 1.3 创建虚拟环境并安装

如果服务器已安装 `uv`：

```bash
uv venv .venv --python 3.11
uv pip install -e ".[mcp,grpc]"
```

如果没有 `uv`：

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/pip install -e ".[mcp,grpc]"
```

### 1.4 启动 Hub

开发/局域网测试启动：

```bash
export CAPABILITY_MESH_AUTH_TOKEN='[REDACTED]'

.venv/bin/capability-mesh --mesh-home ~/.capability-mesh hub start \
  --host 0.0.0.0 \
  --port 8765
```

说明：

- `--host 0.0.0.0` 表示允许局域网机器访问。
- `--port 8765` 是 Hub HTTP 端口。
- `CAPABILITY_MESH_AUTH_TOKEN` 用于保护 mutating API。实际部署时请替换成强随机值，不要提交到 Git。

如果只允许本机反向代理访问，例如 Nginx/Caddy 在前面做 TLS：

```bash
export CAPABILITY_MESH_AUTH_TOKEN='[REDACTED]'

.venv/bin/capability-mesh --mesh-home /var/lib/capability-mesh hub start \
  --host 127.0.0.1 \
  --port 8765
```

然后用 `deploy/nginx-capability-mesh.conf` 作为反向代理模板。

### 1.5 验证 Hub

在服务器上：

```bash
curl -fsS http://127.0.0.1:8765/health
curl -fsS http://127.0.0.1:8765/.well-known/agent-card.json
```

在你的个人机器上：

```bash
curl -fsS http://192.168.1.10:8765/health
```

如果访问失败，检查：

```bash
ip addr
ss -ltnp | grep ':8765'
sudo ufw status
```

如需放行端口：

```bash
sudo ufw allow 8765/tcp
```

### 1.6 可选：使用 systemd 持久运行

仓库提供模板：

```text
deploy/capability-mesh.service
```

一个常见安装方式：

```bash
sudo mkdir -p /opt/capability-mesh /var/lib/capability-mesh /etc/capability-mesh
sudo cp -a ~/apps/capability-mesh/. /opt/capability-mesh/
sudo chown -R "$USER":"$USER" /opt/capability-mesh
cd /opt/capability-mesh

python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/pip install -e ".[mcp,grpc]"
```

写入环境文件：

```bash
sudo tee /etc/capability-mesh/capability-mesh.env >/dev/null <<'EOF'
CAPABILITY_MESH_AUTH_TOKEN=[REDACTED]
EOF
sudo chmod 600 /etc/capability-mesh/capability-mesh.env
```

安装 service：

```bash
sudo cp deploy/capability-mesh.service /etc/systemd/system/capability-mesh.service
sudo systemctl daemon-reload
sudo systemctl enable --now capability-mesh
sudo systemctl status capability-mesh --no-pager
```

查看日志：

```bash
journalctl -u capability-mesh -f
```

---

## 2. 局域网 PC 端：启动 Client/Node

以下步骤在每台 PC 上执行。假设：

```text
Hub IP:        192.168.1.10
Hub URL:       http://192.168.1.10:8765
当前 PC IP:    192.168.1.23
当前 Node ID:  pc-a
Node URL:      http://192.168.1.23:8766
```

请按每台 PC 的实际 IP 和 node id 替换。

### 2.1 安装依赖并 clone 仓库

Linux / WSL / macOS：

```bash
mkdir -p ~/apps
cd ~/apps
git clone https://github.com/Omvira/capability-mesh.git
cd capability-mesh
```

创建虚拟环境：

```bash
uv venv .venv --python 3.11
uv pip install -e ".[mcp]"
```

如果没有 `uv`：

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/pip install -e ".[mcp]"
```

Windows PowerShell 原生 Python 示例：

```powershell
git clone https://github.com/Omvira/capability-mesh.git
cd capability-mesh
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.\.venv\Scripts\pip.exe install -e ".[mcp]"
```

### 2.2 查看当前 PC 的局域网 IP

Linux / WSL：

```bash
ip addr
```

macOS：

```bash
ifconfig
```

Windows PowerShell：

```powershell
ipconfig
```

找到类似 `192.168.1.23` 的局域网地址。后续 `--public-url` 要使用这个地址。

### 2.3 创建 Node manifest

每台 PC 都应该有唯一 `node_id`。

Linux / WSL / macOS：

```bash
.venv/bin/capability-mesh manifest \
  --node-id pc-a \
  --display-name "PC A" \
  --task-type code_review \
  --task-type test_running \
  --tool python \
  --tool git \
  --output pc-a.yaml
```

Windows PowerShell：

```powershell
.\.venv\Scripts\capability-mesh.exe manifest `
  --node-id pc-a `
  --display-name "PC A" `
  --task-type code_review `
  --task-type test_running `
  --tool python `
  --tool git `
  --output pc-a.yaml
```

验证 manifest：

```bash
.venv/bin/capability-mesh validate pc-a.yaml --kind manifest
```

Windows：

```powershell
.\.venv\Scripts\capability-mesh.exe validate pc-a.yaml --kind manifest
```

### 2.4 启动 PC Node A2A Server

Linux / WSL / macOS：

```bash
.venv/bin/capability-mesh --mesh-home ~/.capability-mesh-node node start \
  --manifest pc-a.yaml \
  --host 0.0.0.0 \
  --port 8766 \
  --public-url http://192.168.1.23:8766
```

Windows PowerShell：

```powershell
.\.venv\Scripts\capability-mesh.exe --mesh-home "$env:USERPROFILE\.capability-mesh-node" node start `
  --manifest pc-a.yaml `
  --host 0.0.0.0 `
  --port 8766 `
  --public-url http://192.168.1.23:8766
```

说明：

- `--host 0.0.0.0` 允许 Hub 通过局域网访问此 PC。
- `--public-url` 必须是 Hub 可以访问的 URL。
- 不要写 `http://127.0.0.1:8766`，否则 Hub 访问的是 Hub 自己的 localhost，不是这台 PC。

### 2.5 放行 PC 防火墙

Windows PowerShell 以管理员身份执行：

```powershell
New-NetFirewallRule `
  -DisplayName "Capability Mesh Node 8766" `
  -Direction Inbound `
  -Action Allow `
  -Protocol TCP `
  -LocalPort 8766
```

Linux：

```bash
sudo ufw allow 8766/tcp
```

### 2.6 从 Hub 服务器验证能访问 PC Node

在 Hub 服务器上执行：

```bash
curl -fsS http://192.168.1.23:8766/health
curl -fsS http://192.168.1.23:8766/.well-known/agent-card.json
```

如果失败，优先检查：

- PC Node 是否仍在运行
- PC IP 是否写错
- PC 防火墙是否放行 8766
- Hub 和 PC 是否在同一网段 / VLAN
- 路由器是否开启了 AP isolation / client isolation

### 2.7 把 PC Node 注册到 Hub

当前最直接的方式是在 Hub 服务器上保存同一份 manifest，然后注册为可直连 Node。

在 Hub 服务器上创建或复制 `pc-a.yaml`，然后执行：

```bash
cd ~/apps/capability-mesh

.venv/bin/capability-mesh --mesh-home ~/.capability-mesh hub register-node \
  --manifest pc-a.yaml \
  --hub-url http://192.168.1.23:8766
```

列出 Hub 已注册 Agent：

```bash
.venv/bin/capability-mesh --mesh-home ~/.capability-mesh hub agents
```

你应该能看到 `pc-a` 对应的 AgentCard，且 `supportedInterfaces[0].url` 指向：

```text
http://192.168.1.23:8766
```

### 2.8 从 Hub relay 测试调用 PC Node

在 Hub 服务器或任意能访问 Hub 的机器上：

```bash
curl -fsS \
  -H 'Content-Type: application/a2a+json' \
  -d '{"message":{"role":"ROLE_USER","parts":[{"text":"hello from hub"}]}}' \
  http://192.168.1.10:8765/relay/nodes/pc-a/a2a/message:send
```

如果 Hub 开启了认证，请按你的实际认证方式追加对应 HTTP header；不要把凭证写进文档或提交到 Git。

---

## 3. Hermes MCP stdio adapter 指向 Central Hub

在使用 Hermes 的机器上，把 Capability Mesh MCP server 的 URL 指向 Hub：

```text
http://192.168.1.10:8765
```

Hermes 配置示例：

```yaml
mcp_servers:
  capability_mesh:
    command: /home/uiosa/.local/bin/uv
    args:
      - run
      - --project
      - /home/uiosa/capability-mesh
      - --with
      - mcp
      - --with-editable
      - /home/uiosa/capability-mesh
      - python
      - -m
      - capability_mesh.mcp_server
      - --url
      - http://192.168.1.10:8765
      - --timeout
      - "10"
    timeout: 120
    connect_timeout: 60
    sampling:
      enabled: false
```

验证：

```bash
hermes mcp test capability_mesh
```

在 Hermes 会话中可用的典型工具：

```text
mcp_capability_mesh_list_clients
mcp_capability_mesh_get_client
mcp_capability_mesh_call_client_async
mcp_capability_mesh_send_a2a_message
```

注意：修改 MCP 配置后通常需要重启 Hermes 会话，或者 reload MCP，工具列表才会更新。

---

## 4. 多台 PC 的命名建议

建议为每台 PC 分配稳定的 node id：

```text
pc-wanghaoyu
pc-sunhanyao
pc-finance-01
pc-dev-01
```

每台 PC 使用自己的 manifest：

```text
pc-wanghaoyu.yaml
pc-sunhanyao.yaml
pc-finance-01.yaml
```

每台 PC 使用固定 IP 或 DHCP reservation：

```text
pc-wanghaoyu -> 192.168.1.23
pc-sunhanyao -> 192.168.1.24
pc-finance-01 -> 192.168.1.25
```

这样 Hub registry 和 AgentCard URL 不会因为 IP 变化而失效。

---

## 5. 常见问题

### 5.1 Hub 可以访问，但 PC Node 访问不到

在 Hub 上检查：

```bash
curl -v http://<PC_LAN_IP>:8766/health
```

常见原因：

- Node 启动时用了 `--host 127.0.0.1`，应改成 `--host 0.0.0.0`。
- `--public-url` 写成了 localhost，应改成 PC 局域网 IP。
- Windows 防火墙没有放行 8766。
- PC IP 变了。
- 路由器开启 AP isolation / client isolation。

### 5.2 Hub relay 返回 403 outbound target denied

Capability Mesh 默认会对公网暴露的 Hub 做 outbound SSRF 防护。局域网部署如果 Hub 需要访问内网 PC Node，可以：

- 让 Hub 绑定在 loopback 后面，用 Nginx/Caddy 做受控入口；或
- 在可信局域网内显式允许 private outbound：

```bash
export CAPABILITY_MESH_ALLOW_PRIVATE_OUTBOUND=1
```

然后重启 Hub。

只应在可信内网/VPN 中开启，不要在公开互联网无保护地开启。

### 5.3 Hub 开启了 auth token 后 MCP adapter 调用失败

Hub mutating API 会要求：

```text
Authorization: Bearer <token>
```

当前 MCP stdio adapter 主要配置 Hub URL。如果需要让 MCP adapter 直接调用受保护的 Hub，需要继续为 adapter 增加 auth header 参数，或先把 Hub 放在可信内网/VPN 中，仅对内开放。

### 5.4 PC 没有公网 IP 是否可以接入

同一局域网可以直接用 PC 的内网 IP，不需要公网 IP。

跨 NAT / 不同网络时，不能直接访问 PC 内网 IP，需要反向 tunnel、long-poll 或 WebSocket relay。当前局域网教程不覆盖跨 NAT 场景。

---

## 6. 最小可用检查清单

Hub 服务器：

```bash
curl -fsS http://127.0.0.1:8765/health
curl -fsS http://192.168.1.10:8765/health
```

PC Node：

```bash
curl -fsS http://127.0.0.1:8766/health
```

Hub 访问 PC Node：

```bash
curl -fsS http://192.168.1.23:8766/health
```

Hub registry：

```bash
.venv/bin/capability-mesh --mesh-home ~/.capability-mesh hub agents
```

Hub relay：

```bash
curl -fsS \
  -H 'Content-Type: application/a2a+json' \
  -d '{"message":{"role":"ROLE_USER","parts":[{"text":"ping"}]}}' \
  http://192.168.1.10:8765/relay/nodes/pc-a/a2a/message:send
```

Hermes MCP：

```bash
hermes mcp test capability_mesh
```
