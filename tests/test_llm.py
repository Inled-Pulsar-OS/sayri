"""Tests for sayri.llm. The SSE integration test is skipped if httpx is absent."""

import importlib.util
import json
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "share", "sayri", "lib"))

from sayri.llm import build_payload  # noqa: E402

HAS_HTTPX = importlib.util.find_spec("httpx") is not None


def test_build_payload():
    p = build_payload("llama3.2", [{"role": "user", "content": "hola"}],
                      stream=True, temperature=0.5, max_tokens=100)
    assert p["model"] == "llama3.2"
    assert p["stream"] is True
    assert p["temperature"] == 0.5
    assert p["max_tokens"] == 100
    assert p["messages"][0]["role"] == "user"

    p2 = build_payload("m", [], stream=False, max_tokens=0)
    assert p2["stream"] is False
    assert "max_tokens" not in p2


def test_sanitize_messages():
    from sayri.llm import sanitize_messages

    # Test merging of multiple system messages
    raw = [
        {"role": "system", "content": "You are Sayri."},
        {"role": "user", "content": "Hello"},
        {"role": "system", "content": "Context Note: User likes Python."},
        {"role": "assistant", "content": "Hi!"},
        {"role": "user", "content": "How are you?"},
    ]
    cleaned = sanitize_messages(raw)
    assert len(cleaned) == 4
    assert cleaned[0]["role"] == "system"
    assert "You are Sayri." in cleaned[0]["content"]
    assert "Context Note: User likes Python." in cleaned[0]["content"]
    assert cleaned[1] == {"role": "user", "content": "Hello"}
    assert cleaned[2] == {"role": "assistant", "content": "Hi!"}
    assert cleaned[3] == {"role": "user", "content": "How are you?"}


def test_stream_chat_sse():
    if not HAS_HTTPX:
        print("  SKIP test_stream_chat_sse (httpx no disponible)")
        return

    from http.server import BaseHTTPRequestHandler, HTTPServer

    from sayri.llm import stream_chat

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            events = [
                'data: {"choices":[{"delta":{"content":"hola"}}]}\n\n',
                'data: {"choices":[{"delta":{"content":" mundo"}}]}\n\n',
                "data: [DONE]\n\n",
            ]
            for e in events:
                self.wfile.write(e.encode())
                self.wfile.flush()

        def log_message(self, *args):  # noqa: D102
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    deltas = []
    done = []

    stream_chat(
        f"http://127.0.0.1:{port}/v1",
        "test-key",
        "model-x",
        [{"role": "user", "content": "hi"}],
        on_delta=deltas.append,
        on_done=done.append,
        on_error=lambda e: done.append(f"ERR:{e}"),
    )
    server.shutdown()
    assert "".join(deltas) == "hola mundo"
    assert done and done[0] == "hola mundo"


def test_stream_chat_error_status():
    if not HAS_HTTPX:
        return
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from sayri.llm import stream_chat

    class ErrHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b"unauthorized")

        def log_message(self, *args):  # noqa: D102
            pass

    server = HTTPServer(("127.0.0.1", 0), ErrHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    errors = []
    stream_chat(
        f"http://127.0.0.1:{port}/v1", "", "m", [],
        on_delta=lambda _d: None,
        on_done=lambda _f: None,
        on_error=errors.append,
    )
    server.shutdown()
    assert errors and "401" in str(errors[0])


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"  FAIL {name}: {exc}")
    sys.exit(1 if failures else 0)
