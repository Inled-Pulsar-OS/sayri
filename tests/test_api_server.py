"""Tests for sayri.api_server."""

import json
import os
import sys
import tempfile
import time
import urllib.request
import urllib.error

_TMP = tempfile.mkdtemp(prefix="sayri-api-test-")
os.environ["SAYRI_CONFIG_DIR"] = _TMP
os.environ["SAYRI_STATE_DIR"] = os.path.join(_TMP, "state")
os.environ["SAYRI_MEMORY_DIR"] = os.path.join(_TMP, "memory")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "share", "sayri", "lib"))

from sayri.config import Config
from sayri.api_server import SayriApiServer, SayriApiHandler


class MockCore:
    def __init__(self):
        self.received = []

    def process_remote_message(self, text, author, target_agent_id="default", sandbox_level="LEVEL_RESTRICTED_COMMANDS", instance_id="sayri-api", session_id=None, on_event=None):
        self.received.append({
            "text": text,
            "author": author,
            "target_agent": target_agent_id,
            "sandbox_level": sandbox_level,
        })
        if on_event:
            on_event({"event": "delta", "delta": "Hello "})
            on_event({"event": "delta", "delta": "from Sayri Mock!"})
            on_event({"event": "done", "text": "Hello from Sayri Mock!"})
        return "Hello from Sayri Mock!"


def _http_get(url: str, headers: dict = None) -> tuple[int, dict]:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return resp.status, data
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def _http_post(url: str, body: dict, headers: dict = None) -> tuple[int, dict]:
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return resp.status, data
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def test_api_server_endpoints():
    cfg = Config()
    cfg.set("api", "port", 18765)
    cfg.set("api", "host", "127.0.0.1")
    cfg.set("api", "api_key", "")

    mock_core = MockCore()
    server = SayriApiServer(core=mock_core, cfg=cfg)
    ok = server.start()
    assert ok is True
    time.sleep(0.1)

    base = "http://127.0.0.1:18765"

    try:
        # 1. Models
        status, data = _http_get(f"{base}/v1/models")
        assert status == 200
        assert "data" in data
        assert any(m["id"] == "default" for m in data["data"])

        # 2. Status
        status, data = _http_get(f"{base}/api/v1/status")
        assert status == 200
        assert data["status"] == "online"

        # 3. Ideas memory
        status, data = _http_post(f"{base}/api/v1/memory/ideas", {"idea": "Build Sayri Android companion"})
        assert status == 200
        assert data["ok"] is True

        status, data = _http_get(f"{base}/api/v1/memory/ideas")
        assert status == 200
        assert "Build Sayri Android companion" in data["ideas_markdown"]

        # 4. Tomorrow memory
        status, data = _http_post(f"{base}/api/v1/memory/tomorrow", {"task": "Verify Android accessibility integration"})
        assert status == 200
        assert data["ok"] is True

        status, data = _http_get(f"{base}/api/v1/memory/tomorrow")
        assert status == 200
        assert "Verify Android accessibility integration" in data["tomorrow_markdown"]

        # 5. Chat completions sync
        chat_req = {
            "model": "sayri",
            "messages": [
                {"role": "user", "content": "Hello computer"}
            ]
        }
        status, data = _http_post(f"{base}/v1/chat/completions", chat_req)
        assert status == 200
        assert data["choices"][0]["message"]["content"] == "Hello from Sayri Mock!"
        assert mock_core.received[-1]["text"] == "Hello computer"

        # 6. Chat completions stream
        chat_req_stream = {
            "model": "sayri",
            "stream": True,
            "messages": [
                {"role": "user", "content": "Stream to me"}
            ]
        }
        req = urllib.request.Request(f"{base}/v1/chat/completions", data=json.dumps(chat_req_stream).encode("utf-8"), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            lines = []
            while True:
                line = resp.readline().decode("utf-8")
                if not line:
                    break
                lines.append(line.strip())
                if line.strip() == "data: [DONE]":
                    break
            data_lines = [l for l in lines if l.startswith("data: ")]
            assert len(data_lines) >= 2
            assert data_lines[-1] == "data: [DONE]"

    finally:
        server.stop()


def test_api_server_auth():
    cfg = Config()
    cfg.set("api", "port", 18766)
    cfg.set("api", "host", "127.0.0.1")
    cfg.set("api", "api_key", "secret123")

    mock_core = MockCore()
    server = SayriApiServer(core=mock_core, cfg=cfg)
    ok = server.start()
    assert ok is True
    time.sleep(0.1)

    base = "http://127.0.0.1:18766"

    try:
        # Unauthorized without header
        status, data = _http_get(f"{base}/api/v1/status")
        assert status == 401

        # Authorized with Bearer header
        status, data = _http_get(f"{base}/api/v1/status", headers={"Authorization": "Bearer secret123"})
        assert status == 200
        assert data["status"] == "online"

        # Authorized with query parameter
        status, data = _http_get(f"{base}/api/v1/status?key=secret123")
        assert status == 200
        assert data["status"] == "online"

    finally:
        server.stop()


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS {name}")
            except Exception as e:
                failures += 1
                print(f"  FAIL {name}: {e}")
                import traceback
                traceback.print_exc()
    if failures:
        sys.exit(1)
