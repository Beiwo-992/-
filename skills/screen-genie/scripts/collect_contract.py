"""采集上游 MCP 的工具契约与受控调用样本。

只读默认：完成 initialize 和 tools/list，不执行任何业务工具。
需要采集某个工具的真实返回时，显式加 --call，例如：

    MCP_URL=http://127.0.0.1:8765/mcp MCP_TOKEN=... \
      python3 collect_contract.py --out contract.json

    MCP_URL=http://127.0.0.1:8765/mcp MCP_TOKEN=... \
      python3 collect_contract.py --call mdi_top_app --args '{}'

失败时会把诊断写进输出文件，并以非零退出码结束：
    2 = 认证、HTTP 或 JSON-RPC 错误
    3 = 传输方式不支持（SSE）
    4 = 分页异常（次数超限或 cursor 重复）

令牌只从环境变量读取，不写进输出文件，也不打印。
采集结果里可能含界面或业务数据，分享前请自行检查。
"""

from __future__ import annotations

import argparse
import datetime
import http.client
import json
import os
import sys
from urllib.parse import urlparse

PROTOCOL = os.environ.get("MCP_PROTOCOL", "2025-06-18")
MAX_PAGES = 20


class CollectError(Exception):
    def __init__(self, kind: str, detail: str, code: int = 2):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail
        self.code = code


def connect(url: str):
    u = urlparse(url)
    if u.scheme != "http":
        raise CollectError("unsupported_scheme", f"只支持 http:// 地址，当前是 {u.scheme}://", 3)
    return http.client.HTTPConnection(u.hostname or "127.0.0.1", u.port or 80, timeout=30), (u.path or "/mcp")


def call(conn, path, payload, token, session_id=None, protocol=None):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {token}",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    if protocol:
        headers["MCP-Protocol-Version"] = protocol
    conn.request("POST", path, body=json.dumps(payload).encode(), headers=headers)
    r = conn.getresponse()
    raw = r.read()
    ctype = r.getheader("Content-Type") or ""
    info = {
        "status": r.status,
        "session_id": r.getheader("Mcp-Session-Id"),
        "content_type": ctype,
    }
    if "text/event-stream" in ctype.lower():
        raise CollectError(
            "unsupported_transport",
            f"上游返回 SSE（{ctype}），本采集器只支持一次性 JSON 响应",
            3,
        )
    if not 200 <= r.status < 300:
        raise CollectError(
            "http_error",
            f"HTTP {r.status}: {raw[:500].decode('utf-8', 'replace')}",
        )
    try:
        parsed = json.loads(raw)
    except Exception:
        raise CollectError(
            "invalid_json",
            f"响应不是 JSON：{raw[:300].decode('utf-8', 'replace')}",
        )
    if isinstance(parsed, dict) and parsed.get("error"):
        raise CollectError("jsonrpc_error", json.dumps(parsed["error"], ensure_ascii=False))
    return info, parsed, raw.decode("utf-8", "replace")


def notify(conn, path, payload, token, session_id=None, protocol=None):
    """发送通知，不解析响应体：通知通常返回 202 或空体。"""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {token}",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    if protocol:
        headers["MCP-Protocol-Version"] = protocol
    conn.request("POST", path, body=json.dumps(payload).encode(), headers=headers)
    r = conn.getresponse()
    r.read()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="contract-sample.json")
    ap.add_argument("--call", help="要受控调用的工具名，例如 mdi_top_app")
    ap.add_argument("--args", default="{}", help="受控调用的参数 JSON")
    ap.add_argument("--no-initialize", action="store_true")
    args = ap.parse_args()

    url = os.environ.get("MCP_URL", "http://127.0.0.1:8765/mcp")
    token = os.environ.get("MCP_TOKEN", "")
    if not token:
        print("缺少 MCP_TOKEN 环境变量", file=sys.stderr)
        return 2

    sample = {
        "meta": {
            "url": url,
            "protocol_requested": PROTOCOL,
            "collected_at": datetime.datetime.now().isoformat(timespec="seconds"),
        },
        "ok": False,
        "errors": [],
    }
    code = 2
    conn = None
    try:
        conn, path = connect(url)
        session_id = None
        protocol = PROTOCOL

        if args.no_initialize:
            sample["meta"]["protocol_negotiated"] = None

        else:
            info, parsed, raw = call(conn, path, {
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL,
                    "capabilities": {},
                    "clientInfo": {"name": "screen-genie-contract", "version": "2"},
                },
            }, token)
            sample["initialize"] = {"http": info, "raw": raw, "response": parsed}
            if not isinstance(parsed, dict) or "result" not in parsed:
                raise CollectError("initialize_failed", "initialize 没有返回 result")
            result = parsed.get("result") or {}
            session_id = info.get("session_id") or None
            protocol = result.get("protocolVersion") or PROTOCOL
            sample["meta"]["protocol_negotiated"] = result.get("protocolVersion")
            sample["meta"]["server_info"] = result.get("serverInfo")
            sample["meta"]["capabilities"] = result.get("capabilities")
            notify(conn, path, {"jsonrpc": "2.0", "method": "notifications/initialized"},
                   token, session_id, protocol)

        tools, cursor, seen = [], None, set()
        pages = []
        while True:
            if len(pages) >= MAX_PAGES:
                raise CollectError("pagination_limit", f"超过 {MAX_PAGES} 页仍未结束", 4)
            params = {"cursor": cursor} if cursor else {}
            info, parsed, raw = call(conn, path, {
                "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": params,
            }, token, session_id, protocol)
            result = parsed.get("result") if isinstance(parsed, dict) else None
            if not isinstance(result, dict):
                raise CollectError("tools_list_failed", "tools/list 没有返回 result")
            page_tools = result.get("tools") or []
            pages.append({
                "cursor": cursor,
                "http": info,
                "raw": raw,
                "tool_count": len(page_tools),
            })
            tools.extend(page_tools)
            cursor = result.get("nextCursor")
            if not cursor:
                break
            if cursor in seen:
                raise CollectError("pagination_loop", f"重复的 cursor：{cursor}", 4)
            seen.add(cursor)

        sample["tools"] = tools
        sample["tool_names"] = [t.get("name") for t in tools]
        sample["pages"] = pages
        approx_chars = len(json.dumps(tools, ensure_ascii=False))
        sample["meta"]["tool_definition_chars"] = approx_chars
        sample["meta"]["tool_definition_tokens_estimate"] = round(approx_chars / 4)

        if args.call:
            try:
                call_args = json.loads(args.args)
            except json.JSONDecodeError as e:
                raise CollectError("bad_args", f"--args 不是合法 JSON: {e}")
            info, parsed, raw = call(conn, path, {
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": args.call, "arguments": call_args},
            }, token, session_id, protocol)
            sample["controlled_call"] = {
                "tool": args.call,
                "arguments": call_args,
                "http": info,
                "raw": raw,
                "response": parsed,
            }

        sample["ok"] = True
        code = 0

    except CollectError as e:
        sample["errors"].append({"kind": e.kind, "detail": e.detail})
        code = e.code
        print(f"采集失败（{e.kind}）：{e.detail}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        sample["errors"].append({"kind": "unexpected", "detail": f"{type(e).__name__}: {e}"})
        code = 2
        print(f"采集失败（unexpected）：{type(e).__name__}: {e}", file=sys.stderr)
    finally:
        if conn is not None:
            conn.close()

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(sample, f, ensure_ascii=False, indent=2)

    if code == 0:
        print(f"wrote {args.out}: {len(sample.get('tools', []))} tools"
              + (f", controlled call {args.call}" if args.call else ""))
    else:
        print(f"diagnostics saved to {args.out}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
