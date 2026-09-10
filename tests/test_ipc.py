"""Tests for sayri.ipc (JSON socket server/client + broadcast)."""

import os
import sys
import tempfile
import threading
import time

_TMP = tempfile.mkdtemp(prefix="sayri-ipc-test-")
os.environ["SAYRI_STATE_DIR"] = os.path.join(_TMP, "state")
_SOCK = os.path.join(_TMP, "test.sock")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "share", "sayri", "lib"))

from sayri.ipc import (  # noqa: E402
    SayriClient,
    SayriError,
    SayriServer,
)


def _wait(predicate, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def test_request_response():
    server = SayriServer(_SOCK)
    server.register("ping", lambda p, cid: "pong")
    server.register("echo", lambda p, cid: p)
    assert server.start()

    client = SayriClient(_SOCK)
    try:
        assert client.connect(), "client should connect"
        assert client.request("ping") == "pong"
        assert client.request("echo", {"a": 1}) == {"a": 1}
    finally:
        client.close()
        server.stop()


def test_client_id_in_handler():
    server = SayriServer(_SOCK)
    ids = []

    def _whoami(params, cid):
        ids.append(cid)
        return cid

    server.register("whoami", _whoami)
    assert server.start()

    client = SayriClient(_SOCK)
    try:
        assert client.connect()
        got = client.request("whoami")
        assert got == 1, "first client should get client_id 1"
    finally:
        client.close()
        server.stop()


def test_unknown_command_raises():
    server = SayriServer(_SOCK)
    assert server.start()

    client = SayriClient(_SOCK)
    try:
        assert client.connect()
        try:
            client.request("nope")
            raised = False
        except SayriError:
            raised = True
        assert raised, "unknown command must raise SayriError"
    finally:
        client.close()
        server.stop()


def test_handler_error_replies_ok_false():
    server = SayriServer(_SOCK)

    def _boom(params, cid):
        raise ValueError("kaboom")

    server.register("boom", _boom)
    assert server.start()

    client = SayriClient(_SOCK)
    try:
        assert client.connect()
        try:
            client.request("boom")
            raised = False
        except SayriError as exc:
            raised = True
            assert "kaboom" in str(exc)
        assert raised
    finally:
        client.close()
        server.stop()


def test_broadcast_reaches_clients():
    server = SayriServer(_SOCK)
    assert server.start()

    c1, c2 = SayriClient(_SOCK), SayriClient(_SOCK)
    evs1, evs2 = [], []
    try:
        assert c1.connect() and c2.connect()
        c1.listen_events(evs1.append)
        c2.listen_events(evs2.append)
        time.sleep(0.2)
        server.broadcast("state", state="listening")
        assert _wait(lambda: len(evs1) >= 1 and len(evs2) >= 1), "both clients must receive broadcast"
        assert evs1[0]["event"] == "state" and evs1[0]["state"] == "listening"
        assert evs2[0]["event"] == "state"
    finally:
        c1.close()
        c2.close()
        server.stop()


def test_on_connect_on_disconnect_client_count():
    server = SayriServer(_SOCK)
    counts = []

    def _on_connect(cid):
        counts.append(("connect", server.client_count()))

    def _on_disconnect(cid):
        counts.append(("disconnect", server.client_count()))

    server = SayriServer(_SOCK, on_connect=_on_connect, on_disconnect=_on_disconnect)
    assert server.start()

    c = SayriClient(_SOCK)
    assert c.connect()
    assert _wait(lambda: ("connect", 1) in counts)
    c.close()
    assert _wait(lambda: ("disconnect", 0) in counts)
    server.stop()


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