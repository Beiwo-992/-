"""
MCP 标准化代理 —— 零依赖版（仅 Python 标准库，专为 Termux / Android 设计）。

把手机内置 MCP 服务端（http://127.0.0.1:8765/mcp）的非标准响应改写成
符合 MCP / JSON-RPC 2.0 规范的响应，供严格校验的客户端（官方 Kotlin / Python SDK、
harness App）使用。
本脚本不保存任何凭证。默认模式下客户端必须自带
     `Authorization: Bearer <apiKey>`，原样转发给上游；缺失则直接返回 401。
宿主不能自定义请求头时，可在只监听本机（HOST=127.0.0.1）的前提下用本机模式：
     MCP_API_KEY=<apiKey> python3 mcp_proxy_stdlib.py
客户端没带 Authorization 时才用这个值代填；绑定到非回环地址时拒绝启动。

不使用 requests / httpx，因此**不受系统 HTTP_PROXY 环境变量影响**（http.client 直连），
也不需要在手机上装任何 pip 包。


"""
from __future__ import annotations

import http.client
import ipaddress
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

UPSTREAM = os.environ.get("UPSTREAM", "http://127.0.0.1:8765/mcp")
HOST = os.environ.get("HOST", "127.0.0.1")  # 手机上只监听本机即可
PORT = int(os.environ.get("PORT", "8766"))
TIMEOUT = 120

# 本机模式：宿主不支持自定义请求头时，用环境变量提供 key。仅限回环地址。
MCP_API_KEY = os.environ.get("MCP_API_KEY", "").strip()
LOCAL_ONLY = False
try:
    LOCAL_ONLY = ipaddress.ip_address(HOST).is_loopback
except ValueError:
    LOCAL_ONLY = HOST.lower() == "localhost"
if MCP_API_KEY and not LOCAL_ONLY:
    sys.stderr.write(
        "[mcp-proxy] 拒绝启动：MCP_API_KEY 只允许在监听回环地址时使用，"
        f"当前 HOST={HOST}。请改回 HOST=127.0.0.1，或去掉 MCP_API_KEY。\n"
    )
    raise SystemExit(2)

_url = urlparse(UPSTREAM)
UP_HOST = _url.hostname or "127.0.0.1"
UP_PORT = _url.port or 80
UP_PATH = _url.path or "/mcp"

# ---- X-Mcp-Nonce 校验（官方 JsonRpc$send$2 语义，默认关闭，按需开启）----
# 官方：每次响应的 X-Mcp-Nonce 必须严格等于握手 nonce，缺失/不等 => McpException(-1)，
# RetryPolicy 允许重新握手后重试一次。防"假服务端抢注端口"；经 adb forward 直连的场景
# 不存在该威胁，故默认不校验、仅透传响应头。需要时：
#   1) MCP_NONCE_PIN=<nonce值>  硬固定校验（最强，官方等价）；
#   2) MCP_NONCE_ENFORCE=1      TOFU：首个成功响应的 nonce 视为可信值，之后严格校验。
MCP_NONCE_PIN = os.environ.get("MCP_NONCE_PIN", "")
MCP_NONCE_ENFORCE = os.environ.get("MCP_NONCE_ENFORCE", "") == "1"
_expected_nonce: str | None = MCP_NONCE_PIN or None

_IDENTITY_ERR = json.dumps({
    "jsonrpc": "2.0", "id": None,
    "error": {"code": -32010,
              "message": "服务端身份校验失败：X-Mcp-Nonce 不匹配，拒绝继续通信"
                         "（对齐官方 JsonRpc$send$2 / McpException(-1) 语义）"},
}).encode()


def _nonce_mismatch(nh: str | None) -> bool:
    """True=身份校验失败（官方等价：McpException(-1)）。ENFORCE 模式下首响应负责学习。"""
    global _expected_nonce
    if not MCP_NONCE_PIN and not MCP_NONCE_ENFORCE:
        return False
    if _expected_nonce is None:
        if nh:
            _expected_nonce = nh
        return False
    return nh != _expected_nonce


def _reset_nonce() -> None:
    """等价官方"丢弃 endpoint 重新握手"：清空 TOFU 学习值（PIN 模式下不重置）。"""
    global _expected_nonce
    if not MCP_NONCE_PIN:
        _expected_nonce = None

def normalize_response(obj: dict) -> dict:
    """字段语义以官方 SDK 反编译为准（_analysis/mcp_sdk/：
    McpCallCodec.parseCallResult / JsonRpc$send$2）。"""
    err = obj.get("error")
    if err is None:
        obj.pop("error", None)
    elif isinstance(err, dict):
        obj.pop("result", None)

    # result:null —— 官方 SDK 对此直接抛 McpException（"响应缺少 result 字段"），
    # 转成标准 JSON-RPC error 保持一致
    if obj.get("result") is None and "result" in obj and "error" not in obj:
        obj.pop("result")
        obj["error"] = {
            "code": -32603,
            "message": "响应缺少 result 字段（上游返回 result:null）",
        }
        return obj

    result = obj.get("result")
    if not isinstance(result, dict):
        return obj

    # tools/list：inputSchema 必须是 object。
    # 官方 listTools 根本不读 inputSchema，服务端可能不下发；MCP 规范必填 —— 缺失时补。
    tools = result.get("tools")
    if isinstance(tools, list):
        for t in tools:
            if not isinstance(t, dict):
                continue
            s = t.get("inputSchema")
            if not isinstance(s, dict):
                t["inputSchema"] = {"type": "object", "properties": {}}
            elif s.get("type") != "object":
                s["type"] = "object"
                if s.get("properties") is None:
                    s["properties"] = {}

    # tools/call：content 必须是 ContentBlock 数组。
    # 不依赖 id->method 映射（多客户端并发会串号），按响应结构判断：
    # 官方 result 载荷 {success, content:string|null, structuredContent,
    # errorCode, errorMsg, meta}，success=false 即工具失败（官方 isError()），
    # 与 content 字段是否存在无关。
    if "success" in result:
        if result.get("success") is False:
            result["isError"] = True
        c = result.get("content")
        if isinstance(c, str) and c.strip():
            result["content"] = [{"type": "text", "text": c}]
        elif c is None or (isinstance(c, str) and not c.strip()):
            if result.get("success") is False:
                msg = result.get("errorMsg") or "Tool execution failed"
                result["content"] = [{"type": "text", "text": msg}]
            else:
                # 部分工具只回 structuredContent、content=null（如
                # common_ability_get_app_list），补序列化 JSON 文本块
                sc = result.get("structuredContent")
                result["content"] = (
                    [{"type": "text", "text": json.dumps(sc, ensure_ascii=False)}]
                    if sc is not None else []
                )
    return obj


def _hget(headers, name: str) -> str:
    """HTTP 头名不区分大小写。兼容 email.message.Message、dict 和任意 Mapping。"""
    target = name.lower()
    items = headers.items() if hasattr(headers, "items") else []
    for k, v in items:
        if str(k).lower() == target:
            return v or ""
    return ""


# 可选工具白名单：只暴露当前连接需要的工具，减少工具定义占用的上下文。
# 不设置时保持原样透传。
TOOL_ALLOWLIST = {
    t.strip()
    for t in os.environ.get("MCP_TOOL_ALLOWLIST", "").split(",")
    if t.strip()
}


def filter_tools(obj: dict) -> dict:
    """按白名单裁剪 tools/list 结果；未配置时原样返回。"""
    if not TOOL_ALLOWLIST:
        return obj
    result = obj.get("result")
    if not isinstance(result, dict):
        return obj
    tools = result.get("tools")
    if isinstance(tools, list):
        result["tools"] = [
            t for t in tools
            if isinstance(t, dict) and t.get("name") in TOOL_ALLOWLIST
        ]
    return obj


def disallowed_tool(payload: bytes) -> tuple[str, object] | None:
    """返回 (被拒工具名, 请求 id)；不在白名单时才返回。"""
    if not TOOL_ALLOWLIST:
        return None
    try:
        obj = json.loads(payload)
    except Exception:
        return None
    if not isinstance(obj, dict) or obj.get("method") != "tools/call":
        return None
    name = str((obj.get("params") or {}).get("name") or "")
    if name and name not in TOOL_ALLOWLIST:
        return name, obj.get("id")
    return None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # noqa: ANN001
        sys.stderr.write("[mcp-proxy] " + (fmt % args) + "\n")

    # ---------- 工具方法 ----------
    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _relay(self, payload: bytes, headers, auth_override: str = "") -> tuple[int, bytes, str, dict]:
        conn = http.client.HTTPConnection(UP_HOST, UP_PORT, timeout=TIMEOUT)
        # 头名不区分大小写，统一用 _hget 读取，不先转成普通 dict 再按原样键取
        hdrs = {
            "Content-Type": _hget(headers, "Content-Type") or "application/json",
            # 凭证优先由客户端提供，原样透传；仅本机模式才用环境变量代填
            "Authorization": _hget(headers, "Authorization") or auth_override,
            "Accept": _hget(headers, "Accept") or "application/json",
        }
        # 会话与协议头必须原样透传，否则 initialize 之后的上游调用会丢失会话
        for k in ("Mcp-Session-Id", "MCP-Protocol-Version"):
            v = _hget(headers, k)
            if v:
                hdrs[k] = v
        try:
            conn.request("POST", UP_PATH, body=payload, headers=hdrs)
            r = conn.getresponse()
            data = r.read()
            extra = {}
            for k in ("X-Mcp-Nonce", "Mcp-Session-Id", "MCP-Protocol-Version"):
                if r.getheader(k):
                    extra[k] = r.getheader(k)
            ctype = r.getheader("Content-Type", "application/json")
            return r.status, data, ctype, extra
        finally:
            conn.close()

    # ---------- 路由 ----------
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        payload = self.rfile.read(length) if length else b""

        denied = disallowed_tool(payload)
        if denied:
            name, req_id = denied
            body = json.dumps({
                "jsonrpc": "2.0", "id": req_id,
                "error": {"code": -32601,
                          "message": f"tool not allowed by MCP_TOOL_ALLOWLIST: {name}"},
            }).encode()
            self._send(200, body, "application/json")
            return

        # 凭证优先由客户端提供并原样透传；本机模式下可用 MCP_API_KEY 代填
        auth_override = ""
        if not _hget(self.headers, "Authorization"):
            if MCP_API_KEY:
                auth_override = f"Bearer {MCP_API_KEY}"
            else:
                body = json.dumps(
                    {"jsonrpc": "2.0", "id": None,
                     "error": {"code": -32001,
                               "message": "Missing Authorization header. "
                                          "Send 'Authorization: Bearer <apiKey>' on every request, "
                                          "or start the proxy with MCP_API_KEY on 127.0.0.1."}}
                ).encode()
                self._send(401, body, "application/json")
                return

        try:
            status, data, ctype, extra = self._relay(payload, self.headers, auth_override)
        except Exception as e:  # noqa: BLE001
            body = json.dumps(
                {"jsonrpc": "2.0", "id": None,
                 "error": {"code": -32000, "message": f"upstream unreachable: {e}"}}
            ).encode()
            self._send(502, body, "application/json")
            return

        # 身份校验失败：请求已经发到上游、可能已被执行，这里不重放。
        # 只清掉 TOFU 学习值，让客户端重新握手后的下一次请求重新学习。
        if _nonce_mismatch(extra.get("X-Mcp-Nonce")):
            _reset_nonce()
            self._send(502, _IDENTITY_ERR, "application/json")
            return

        try:
            obj = json.loads(data)
            obj = normalize_response(obj)
            obj = filter_tools(obj)
            self._send(status, json.dumps(obj, ensure_ascii=False).encode(),
                       "application/json; charset=utf-8", extra)
        except Exception:
            self._send(status, data, ctype, extra)  # 非 JSON 响应原样透传

    def do_GET(self):  # noqa: N802
        if self.path in ("/", "/health"):
            body = json.dumps(
                {"name": "MCP Compatibility Proxy (stdlib)", "upstream": UPSTREAM,
                 "endpoint": f"http://{HOST}:{PORT}/mcp"}
            ).encode()
            self._send(200, body, "application/json")
        else:
            self._send(405, b"This proxy implements POST-only JSON-RPC (no SSE). Use POST /mcp.",
                       "text/plain")


def main() -> int:
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[mcp-proxy] http://{HOST}:{PORT}/mcp  ->  {UPSTREAM}")
    print("[mcp-proxy] 已启用规范化：error:null / content 数组 / inputSchema / isError")
    if MCP_API_KEY:
        print("[mcp-proxy] 本机模式：客户端未带 Authorization 时，用 MCP_API_KEY 代填")
    if TOOL_ALLOWLIST:
        print(f"[mcp-proxy] 工具白名单已启用：{len(TOOL_ALLOWLIST)} 项")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
