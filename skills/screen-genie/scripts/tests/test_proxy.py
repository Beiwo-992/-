"""代理回归测试。

用法：
    python3 test_proxy.py

覆盖：nonce 不匹配不重放（ENFORCE / PIN）、请求头大小写、代填 key、
缺认证 401、会话头透传、工具白名单。全部使用本地模拟上游，不连接手机。

端口动态分配，启动时轮询健康检查而不是固定 sleep；测试环境变量显式清理，
避免继承调用方的 MCP_* 配置。
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROXY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "mcp_proxy_stdlib.py")


class Upstream(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    records = []
    nonce_mode = "fixed"  # fixed | changing | always_b

    def log_message(self, *args):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        try:
            req = json.loads(raw or b"{}")
        except Exception:
            req = {}
        method = req.get("method")
        Upstream.records.append({
            "method": method,
            "name": ((req.get("params") or {}).get("name")),
            "headers": {k.lower(): v for k, v in self.headers.items()},
        })
        idx = len(Upstream.records)
        if Upstream.nonce_mode == "changing":
            nonce = "A" if idx == 1 else "B"
        elif Upstream.nonce_mode == "always_b":
            nonce = "B"
        else:
            nonce = "A"

        if method == "tools/list":
            result = {"tools": [
                {"name": "mdi_click", "description": "click"},
                {"name": "mdi_snapshot", "description": "snapshot"},
                {"name": "mdi_unrelated", "description": "should be filtered"},
            ]}
        else:
            result = {"ok": True}

        body = json.dumps({"jsonrpc": "2.0", "id": req.get("id"), "result": result}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Mcp-Nonce", nonce)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def serve():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def clean_env(extra):
    env = {
        k: v for k, v in os.environ.items()
        if not k.startswith("MCP_") and k not in {"PORT", "HOST", "UPSTREAM"}
    }
    env.update(extra)
    return env


def start_proxy(up_port, px_port, extra):
    env = clean_env({
        "UPSTREAM": f"http://127.0.0.1:{up_port}/mcp",
        "HOST": "127.0.0.1",
        "PORT": str(px_port),
    })
    env.update(extra)
    p = subprocess.Popen([sys.executable, PROXY], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 8
    while time.time() < deadline:
        try:
            conn = HTTPConnection("127.0.0.1", px_port, timeout=0.5)
            conn.request("GET", "/health")
            r = conn.getresponse()
            r.read()
            conn.close()
            if r.status == 200:
                return p
        except Exception:
            pass
        if p.poll() is not None:
            raise RuntimeError("proxy exited before becoming ready")
        time.sleep(0.1)
    p.terminate()
    raise RuntimeError("proxy did not become ready")


def post(px_port, headers, method="tools/call", name="mdi_click"):
    conn = HTTPConnection("127.0.0.1", px_port, timeout=5)
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": {"name": name, "arguments": {}}}).encode()
    conn.request("POST", "/mcp", body=body, headers=headers)
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r.status, data


class Session:
    def __init__(self, extra_env=None, nonce_mode="fixed"):
        self.nonce_mode = nonce_mode
        self.extra_env = extra_env or {}
        self.srv = None
        self.proc = None
        self.px_port = free_port()

    def __enter__(self):
        Upstream.records.clear()
        Upstream.nonce_mode = self.nonce_mode
        self.srv = serve()
        self.up_port = self.srv.server_address[1]
        self.proc = start_proxy(self.up_port, self.px_port, self.extra_env)
        return self

    def __exit__(self, *exc):
        self.proc.terminate()
        self.proc.wait(timeout=5)
        self.srv.shutdown()
        self.srv.server_close()


def test_health_and_missing_auth():
    with Session() as s:
        conn = HTTPConnection("127.0.0.1", s.px_port, timeout=5)
        conn.request("GET", "/health")
        r = conn.getresponse()
        body = r.read()
        conn.close()
        assert r.status == 200 and b"MCP Compatibility Proxy" in body

        status, _ = post(s.px_port, {"Content-Type": "application/json"})
        assert status == 401, status
        assert Upstream.records == [], "401 不应转发到上游"
        print("PASS health 200 / missing auth 401, upstream untouched")


def test_lowercase_headers():
    with Session() as s:
        status, _ = post(s.px_port, {
            "authorization": "Bearer case-test",
            "mcp-session-id": "sess-1",
            "mcp-protocol-version": "2025-06-18",
            "content-type": "application/json",
            "accept": "application/json",
        })
        h = Upstream.records[-1]["headers"]
        assert status == 200, status
        assert h.get("authorization") == "Bearer case-test"
        assert h.get("mcp-session-id") == "sess-1"
        assert h.get("mcp-protocol-version") == "2025-06-18"
        print("PASS lowercase headers forwarded")


def test_mixed_case_not_overridden():
    with Session({"MCP_API_KEY": "fallback"}) as s:
        status, _ = post(s.px_port, {
            "AuThOrIzAtIoN": "Bearer client-key",
            "Content-Type": "application/json",
        })
        h = Upstream.records[-1]["headers"]
        assert status == 200, status
        assert h.get("authorization") == "Bearer client-key", h.get("authorization")
        print("PASS mixed-case auth not overridden by fallback")


def test_fallback_key_used():
    with Session({"MCP_API_KEY": "fallback"}) as s:
        status, _ = post(s.px_port, {"Content-Type": "application/json"})
        h = Upstream.records[-1]["headers"]
        assert status == 200, status
        assert h.get("authorization") == "Bearer fallback", h.get("authorization")
        print("PASS fallback key used when client sends none")


def test_no_replay_enforce():
    with Session({"MCP_NONCE_ENFORCE": "1"}, nonce_mode="changing") as s:
        s1, _ = post(s.px_port, {"Authorization": "Bearer k"})
        s2, _ = post(s.px_port, {"Authorization": "Bearer k"})
        assert s1 == 200, s1
        assert s2 == 502, s2
        assert len(Upstream.records) == 2, f"upstream saw {len(Upstream.records)} calls"
        print("PASS ENFORCE mismatch: no replay (2 upstream calls)")


def test_no_replay_pin():
    with Session({"MCP_NONCE_PIN": "A"}, nonce_mode="always_b") as s:
        status, _ = post(s.px_port, {"Authorization": "Bearer k"})
        assert status == 502, status
        assert len(Upstream.records) == 1, f"upstream saw {len(Upstream.records)} calls"
        print("PASS PIN mismatch: no replay (1 upstream call)")


def test_tool_allowlist_filters_list():
    with Session({"MCP_TOOL_ALLOWLIST": "mdi_click,mdi_snapshot"}) as s:
        status, data = post(s.px_port, {"Authorization": "Bearer k"},
                            method="tools/list", name="")
        assert status == 200, status
        tools = json.loads(data)["result"]["tools"]
        names = [t["name"] for t in tools]
        assert names == ["mdi_click", "mdi_snapshot"], names
        print("PASS allowlist filters tools/list")


def test_tool_allowlist_blocks_call():
    with Session({"MCP_TOOL_ALLOWLIST": "mdi_click"}) as s:
        status, data = post(s.px_port, {"Authorization": "Bearer k"},
                            name="mdi_unrelated")
        obj = json.loads(data)
        assert status == 200, status
        assert obj.get("error"), obj
        assert obj["error"]["code"] == -32601
        assert obj.get("id") == 1
        assert Upstream.records == [], "被拒的工具不应转发到上游"
        print("PASS allowlist blocks disallowed tools/call, upstream untouched")


if __name__ == "__main__":
    test_health_and_missing_auth()
    test_lowercase_headers()
    test_mixed_case_not_overridden()
    test_fallback_key_used()
    test_no_replay_enforce()
    test_no_replay_pin()
    test_tool_allowlist_filters_list()
    test_tool_allowlist_blocks_call()
    print("ALL PASS")
