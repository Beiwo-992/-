"""契约采集器的错误场景测试。

用法：
    python3 test_collector.py

覆盖：正常采集、协商版本头、401、initialize 返回错误时不继续调用、
SSE 明确报错、分页 cursor 重复。
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
COLLECTOR = os.path.join(os.path.dirname(HERE), "collect_contract.py")


class Upstream(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    mode = "ok"
    records = []
    negotiated = "2024-11-05"

    def log_message(self, *args):
        pass

    def _json(self, status, obj):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        req = json.loads(self.rfile.read(n) or b"{}")
        Upstream.records.append({
            "method": req.get("method"),
            "protocol": self.headers.get("MCP-Protocol-Version"),
        })

        if Upstream.mode == "sse":
            body = b"event: message\ndata: {}\n\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if Upstream.mode == "401":
            self._json(401, {"error": {"code": 401, "message": "Missing API key"}})
            return

        method = req.get("method")
        if method == "initialize":
            if Upstream.mode == "init_error":
                self._json(200, {"jsonrpc": "2.0", "id": req.get("id"),
                                 "error": {"code": -32602, "message": "bad protocol"}})
                return
            self._json(200, {"jsonrpc": "2.0", "id": req.get("id"), "result": {
                "protocolVersion": Upstream.negotiated,
                "capabilities": {},
                "serverInfo": {"name": "mock"},
            }})
            return

        if method == "tools/list":
            if Upstream.mode == "paging_loop":
                self._json(200, {"jsonrpc": "2.0", "id": req.get("id"), "result": {
                    "tools": [{"name": "mdi_click", "inputSchema": {"type": "object"}}],
                    "nextCursor": "c1",
                }})
                return
            self._json(200, {"jsonrpc": "2.0", "id": req.get("id"), "result": {
                "tools": [
                    {"name": "mdi_click", "inputSchema": {"type": "object", "properties": {}}},
                    {"name": "mdi_snapshot", "inputSchema": {"type": "object", "properties": {}}},
                ],
            }})
            return

        if method == "tools/call":
            self._json(200, {"jsonrpc": "2.0", "id": req.get("id"),
                             "result": {"content": [{"type": "text", "text": "ok"}]}})
            return

        self.send_response(202)
        self.send_header("Content-Length", "0")
        self.end_headers()


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def run(mode, extra=()):
    Upstream.mode = mode
    Upstream.records.clear()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    out = tempfile.mktemp(suffix=".json")
    env = {k: v for k, v in os.environ.items() if not k.startswith("MCP_")}
    env.update({
        "MCP_URL": f"http://127.0.0.1:{port}/mcp",
        "MCP_TOKEN": "test-token",
    })
    try:
        p = subprocess.run([sys.executable, COLLECTOR, "--out", out, *extra],
                           env=env, capture_output=True, timeout=30)
        with open(out, encoding="utf-8") as f:
            data = json.load(f)
        return p.returncode, data, Upstream.records
    finally:
        srv.shutdown()
        srv.server_close()
        if os.path.exists(out):
            os.remove(out)


def test_ok_and_negotiated_protocol():
    code, data, records = run("ok")
    assert code == 0, (code, data)
    assert data["ok"] is True
    assert len(data["tools"]) == 2
    assert data["meta"]["protocol_negotiated"] == "2024-11-05"
    later = [r for r in records if r["method"] in ("tools/list", "tools/call")]
    assert later and all(r["protocol"] == "2024-11-05" for r in later), later
    print("PASS ok: 2 tools, negotiated protocol used on later requests")


def test_401_is_not_success():
    code, data, _ = run("401")
    assert code == 2, code
    assert data["ok"] is False
    assert any(e["kind"] == "http_error" for e in data["errors"])
    assert data.get("tools", []) == []
    print("PASS 401: non-zero exit, diagnostics saved, no tools reported")


def test_init_error_stops_before_call():
    code, data, records = run("init_error", ("--call", "mdi_top_app"))
    assert code == 2, code
    assert any(e["kind"] == "jsonrpc_error" for e in data["errors"])
    assert not any(r["method"] == "tools/call" for r in records), records
    assert "controlled_call" not in data
    print("PASS initialize error: stopped before tools/list and tools/call")


def test_sse_rejected():
    code, data, _ = run("sse")
    assert code == 3, code
    assert any(e["kind"] == "unsupported_transport" for e in data["errors"])
    print("PASS SSE: explicit unsupported_transport")


def test_pagination_loop_detected():
    code, data, _ = run("paging_loop")
    assert code == 4, code
    assert any(e["kind"] == "pagination_loop" for e in data["errors"])
    print("PASS pagination: repeated cursor detected")


if __name__ == "__main__":
    test_ok_and_negotiated_protocol()
    test_401_is_not_success()
    test_init_error_stops_before_call()
    test_sse_rejected()
    test_pagination_loop_detected()
    print("ALL PASS")
