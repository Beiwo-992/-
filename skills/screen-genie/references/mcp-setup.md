# 连接手机 MCP 服务

先分清两个端口，这是最容易卡住的一步。

| 端口 | 是什么 | 谁启动 |
| --- | --- | --- |
| `8765` | 手机内置 MCP 服务，`/mcp` 单端点、只接受 POST | 系统常驻，不需要你启动 |
| `8766` | 本 skill 自带的规范化代理 | 需要你手动运行，不启动就没有进程监听 |

`8766` 连接被拒，不代表手机能力不可用，只代表代理没启动。先确认你要走哪条接法。

本机实测（StepX PZ261A / Android 16 / `com.stepos.ability` v1.1.2）：`8765` 上的服务返回的是标准 JSON-RPC 2.0，`GET /` 是 `405 Only POST is supported`，`/health` 等路径是 `404`。

## 接法一：直连 8765（首选）

宿主支持自定义请求头时，直接用手机内置服务，链路最短、故障点最少。

```json
{
  "mcpServers": {
    "screen-genie": {
      "type": "http",
      "url": "http://127.0.0.1:8765/mcp",
      "headers": {
        "Authorization": "Bearer <apiKey>"
      }
    }
  }
}
```

宿主在桌面、手机通过 ADB 连接时，先在桌面执行：

```bash
adb forward tcp:8765 tcp:8765
```

`adb forward` 在断开或重启后失效，需要重新执行。

验证：

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8765/mcp \
  -H 'Content-Type: application/json' -d '{}'
```

返回 `401` 说明链路通了、只是没带 key，属于正常；`Connection refused` 才是服务没起。

## 接法二：经 8766 代理

只有两种情况需要它：宿主不能自定义请求头，或者上游返回的响应不标准（缺 `inputSchema`、`content` 不是数组、`success:false` 没转 `isError`）。

代理只做两件事：把非标准响应改写成标准 JSON-RPC 2.0；在宿主带不了 header 时用环境变量代填 `Authorization`。

### 启动

先确认手机上的上游服务在运行。它是提供 `mdi_*` 的系统服务，未运行时工具列表会是空的：

```bash
adb shell dumpsys package com.stepx.companion.app | grep -A2 TOOLS_SERVICE
```

然后在手机上（Termux 或任意 Python 3 环境）进入本 skill 的 `scripts/` 目录：

```bash
cd <skill 目录>/scripts
python3 mcp_proxy_stdlib.py
```

默认监听 `127.0.0.1:8766`，上游为 `http://127.0.0.1:8765/mcp`。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `UPSTREAM` | `http://127.0.0.1:8765/mcp` | 上游 MCP 地址 |
| `HOST` | `127.0.0.1` | 监听地址，保持本机，不要改成 `0.0.0.0` |
| `PORT` | `8766` | 监听端口 |
| `MCP_NONCE_PIN` | 空 | 固定校验 `X-Mcp-Nonce`，最强 |
| `MCP_NONCE_ENFORCE` | 空 | 设为 `1` 时，首个成功响应的 nonce 视为可信值 |
| `MCP_API_KEY` | 空 | 客户端没带 `Authorization` 时用它代填。仅允许 `HOST` 为回环地址时使用，否则拒绝启动 |

### 登记

```json
{
  "mcpServers": {
    "screen-genie": {
      "type": "http",
      "url": "http://127.0.0.1:8766/mcp"
    }
  }
}
```

宿主在桌面、手机通过 ADB 连接时，先把代理端口转发出来：

```bash
adb forward tcp:8766 tcp:8766
```

### 代填 key

宿主没有 `headers` 字段时，用本机模式让代理代填：

```bash
MCP_API_KEY='<apiKey>' python3 mcp_proxy_stdlib.py
```

`MCP_API_KEY` 只存在于该进程的环境变量里，不要写进脚本、配置仓库或对话。客户端自带 `Authorization` 时以客户端的为准。

### 验证

```bash
curl -s http://127.0.0.1:8766/health
```

期望返回包含 `MCP Compatibility Proxy (stdlib)` 的 JSON。再确认能列出工具：

```bash
curl -s -X POST http://127.0.0.1:8766/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -H 'Authorization: Bearer <apiKey>' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

## 服务提供方

本机实测，`mdi_*` 由 `com.stepx.companion.app/.NativeMcpService` 提供，action 是 `com.stepos.mcp.sdk.TOOLS_SERVICE`。

同一 action 在 `com.stepos.common.ability.proxy`、`com.stepos.ailauncher`、`com.stepos.safe`、`com.android.mms` 上也有实现，是一套多 provider 架构。工具列表为空时，先确认是哪个 provider 该提供 `mdi_*`，不要笼统地「重启代理」。

**这个服务不能由 adb 启动。** 直接启动会报：

```text
Error: Requires permission com.stepos.mcp.sdk.permission.ACCESS_MCP_SERVICES
```

该权限是 `signature|privileged`，adb/shell 不在授权名单里，试多少次都不会成功。正确做法是打开持有该权限的客户端应用，由它 bind；用户侧不需要也不能敲命令启动。

## 限流

手机内置 MCP 服务端有限流：

```text
120 calls/min per API key (burst 20)
429 → back off, do not retry immediately
401 → API key invalid or revoked, stop
```

这也说明为什么不能用 `mdi_capture` 轮询做实时预览：5～10 fps 会瞬间吃光每分钟 120 次的预算，实际操作就没额度了。预览要流畅只能由原生侧提供帧流。

## 常见问题

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 连接拒绝（8766） | 代理没启动 | 回到「接法二」启动代理 |
| 连接拒绝（8765） | 上游服务没起，或 `adb forward` 失效 | 检查 provider 状态；重新执行 forward |
| `8765` 返回 401 | 链路通了，但没带或带错 key | 补 `Authorization: Bearer <apiKey>`，这是正常响应 |
| `429` | 触发限流 | 退避后再试，不要立即重试，更不要加大频率 |
| `401` 且 key 确认无误 | key 失效或被吊销 | 停止，重新获取 key |
| 工具列表为空 | 对应 provider 没有加载工具 | 确认 `mdi_*` 的 provider 在运行 |
| 桌面宿主连不上、手机 curl 正常 | 没做 `adb forward` | 在桌面执行 `adb forward tcp:<port> tcp:<port>` |
| 能列出工具，调用时报错 | 会话头没有往返 | 代理已透传 `Mcp-Session-Id`；确认客户端保存了 `initialize` 返回的会话头 |
| 返回 405 | 客户端尝试 GET/SSE | 该服务只接受 POST `/mcp` |
| 模型说「没有 MCP / 无法调用」 | 宿主没有登记该 MCP 地址 | 完成登记后重启会话，让宿主重新加载工具 |
