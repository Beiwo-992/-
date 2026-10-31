"""代理回归测试。

用法：
    python3 test_proxy.py

覆盖：nonce 不匹配不重放（ENFORCE / PIN）、请求头大小写、代填 key、
缺认证 401、会话头透传、健康检查。全部使用本地模拟上游，不连接手机。
"""

import json
import os
import subprocess
import sys
import threading
import time
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROXY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "mcp_proxy_stdlib.py")
UP_PORT = 18765
PX_PORT = 18766


class Upstream(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    records = []
    nonce_mode = "fixed"  # fixed: 始终 A；changing: 第一次 A，之后 B；always_b: 始终 B

    def log_message(self, *args):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        Upstream.records.append({k.lower(): v for k, v in self.headers.items()})
        idx = len(Upstream.records)
        if Upstream.nonce_mode == "changing":
            nonce = "A" if idx == 1 else "B"
        elif Upstream.nonce_mode == "always_b":
            nonce = "B"
        else:
            nonce = "A"
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Mcp-Nonce", nonce)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve():
    srv = ThreadingHTTPServer(("127.0.0.1", UP_PORT), Upstream)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def start_proxy(extra):
    env = dict(os.environ)
    env.update({
        "UPSTREAM": f"http://127.0.0.1:{UP_PORT}/mcp",
        "HOST": "127.0.0.1",
        "PORT": str(PX_PORT),
    })
    env.update(extra)
    p = subprocess.Popen([sys.executable, PROXY], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    return p


def post(headers):
    conn = HTTPConnection("127.0.0.1", PX_PORT, timeout=5)
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "mdi_click", "arguments": {}},
    }).encode()
    conn.request("POST", "/mcp", body=body, headers=headers)
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r.status, data


def run(extra_env, headers, nonce_mode="fixed"):
    Upstream.records.clear()
    Upstream.nonce_mode = nonce_mode
    srv = serve()
    p = start_proxy(extra_env)
    try:
        return post(headers)
    finally:
        p.terminate()
        p.wait(timeout=5)
        srv.shutdown()
        srv.server_close()


def test_health_and_missing_auth():
    Upstream.records.clear()
    Upstream.nonce_mode = "fixed"
    srv = serve()
    p = start_proxy({})
    try:
        conn = HTTPConnection("127.0.0.1", PX_PORT, timeout=5)
        conn.request("GET", "/health")
        r = conn.getresponse()
        body = r.read()
        conn.close()
        assert r.status == 200 and b"MCP Compatibility Proxy" in body

        status, _ = post({"Content-Type": "application/json"})
        assert status == 401, status
        assert Upstream.records == [], "401 不应转发到上游"
        print("PASS health 200 / missing auth 401, upstream untouched")
    finally:
        p.terminate()
        p.wait(timeout=5)
        srv.shutdown()
        srv.server_close()


def test_lowercase_headers():
    status, _ = run({}, {
        "authorization": "Bearer case-test",
        "mcp-session-id": "sess-1",
        "mcp-protocol-version": "2025-06-18",
        "content-type": "application/json",
        "accept": "application/json",
    })
    h = Upstream.records[-1]
    assert status == 200, status
    assert h.get("authorization") == "Bearer case-test"
    assert h.get("mcp-session-id") == "sess-1"
    assert h.get("mcp-protocol-version") == "2025-06-18"
    print("PASS lowercase headers forwarded")


def test_mixed_case_not_overridden():
    status, _ = run({"MCP_API_KEY": "fallback"}, {
        "AuThOrIzAtIoN": "Bearer client-key",
        "Content-Type": "application/json",
    })
    h = Upstream.records[-1]
    assert status == 200, status
    assert h.get("authorization") == "Bearer client-key", h.get("authorization")
    print("PASS mixed-case auth not overridden by fallback")


def test_fallback_key_used():
    status, _ = run({"MCP_API_KEY": "fallback"}, {"Content-Type": "application/json"})
    h = Upstream.records[-1]
    assert status == 200, status
    assert h.get("authorization") == "Bearer fallback", h.get("authorization")
    print("PASS fallback key used when client sends none")


def test_no_replay_enforce():
    Upstream.records.clear()
    Upstream.nonce_mode = "changing"
    srv = serve()
    p = start_proxy({"MCP_NONCE_ENFORCE": "1"})
    try:
        s1, _ = post({"Authorization": "Bearer k"})
        s2, _ = post({"Authorization": "Bearer k"})
        assert s1 == 200, s1
        assert s2 == 502, s2
        assert len(Upstream.records) == 2, f"upstream saw {len(Upstream.records)} calls"
        print("PASS ENFORCE mismatch: no replay (2 upstream calls)")
    finally:
        p.terminate()
        p.wait(timeout=5)
        srv.shutdown()
        srv.server_close()


def test_no_replay_pin():
    status, _ = run({"MCP_NONCE_PIN": "A"}, {"Authorization": "Bearer k"},
                    nonce_mode="always_b")
    assert status == 502, status
    assert len(Upstream.records) == 1, f"upstream saw {len(Upstream.records)} calls"
    print("PASS PIN mismatch: no replay (1 upstream call)")


if __name__ == "__main__":
    test_health_and_missing_auth()
    test_lowercase_headers()
    test_mixed_case_not_overridden()
    test_fallback_key_used()
    test_no_replay_enforce()
    test_no_replay_pin()
    print("ALL PASS")
