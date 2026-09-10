"""Sayri daemon <-> clients IPC (JSON over a local socket).

Transport
---------
Linux / macOS use a Unix domain socket (`SAYRI_STATE_DIR/sayri-daemon.sock`).
Windows will use a named pipe in the same location (future work); the
`SayriServer`/`SayriClient` classes here are the only transport-aware place.

Protocol
--------
Messages are JSON objects, one per line (NDJSON). Three kinds exist:

1. Request  (client -> daemon)::

       {"cmd": "talk", "params": {"text": "..."}, "id": 1}

2. Response (daemon -> client)::

       {"ok": true,  "result": {...}, "id": 1}
       {"ok": false, "error": "msg", "id": 1}

3. Event (daemon -> *all* clients, broadcast)::

       {"event": "state", "state": "listening", ...payload}

Wire format is line-delimited JSON: ``sock.sendall(json.dumps(msg) + "\\n")``.

Commands (cmd)
--------------
  ping, status, talk, ask, listen, stop_listening, toggle_listening,
  interrupt, new_conversation, switch_session, config_get, config_set,
  config_list, skills_list, skills_install, skills_uninstall, skills_search,
  plugins_list, gateway_list, gateway_start, gateway_stop, gateway_delete,
  gateway_save, agents_list, version, quit

Events (event)
--------------
  ready, state, audio_level, mic, busy, partial, utterance, user,
  assistant_delta, assistant_done, tool_start, tool_finish, hint, error,
  speaking, shutdown

Any process can consume this protocol: the GTK orb, a Clippy or Bonzi Buddy
widget, the `sayri` CLI, shell scripts, etc.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from typing import Any, Callable, Optional

from . import paths

# Always use a separate socket from the GUI's legacy one (sayri.sock) so the
# daemon and the GTK overlay can coexist on the same machine.
SOCK_NAME = "sayri-daemon.sock"


class SayriError(Exception):
    """Raised by SayriClient when the daemon answers with an error."""

    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


def socket_path(state_dir: Optional[str] = None) -> str:
    """Absolute path of the daemon control socket."""
    base = state_dir or paths.state_dir()
    return os.path.join(base, SOCK_NAME)


class SayriServer:
    """A tiny JSON/socket command server with event broadcast.

    Usage::

        server = SayriServer(on_connect, on_disconnect)
        server.register("ping", lambda p, cid: "pong")
        server.start()              # background accept loop
        ...
        server.broadcast("state", state="listening")
        ...
        server.stop()
    """

    def __init__(
        self,
        sock_path: Optional[str] = None,
        *,
        on_connect: Optional[Callable[[int], None]] = None,
        on_disconnect: Optional[Callable[[int], None]] = None,
    ) -> None:
        self.sock_path = sock_path or socket_path()
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect

        self._handlers: dict[str, Callable[[dict, int], Any]] = {}
        self._clients: dict[int, "_ClientConn"] = {}
        self._accept_thread: Optional[threading.Thread] = None
        self._listener: Optional[socket.socket] = None
        self._running = False
        self._next_client_id = 1
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ api
    def register(self, command: str, handler: Callable[[dict, int], Any]) -> None:
        """Register a command handler.

        ``handler(params, client_id)`` returns a JSON-serialisable result.
        """
        self._handlers[command] = handler

    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)

    def broadcast(self, event: str, **payload: Any) -> None:
        """Send ``{"event": event, **payload}`` to every connected client."""
        msg = {"event": event, **payload}
        line = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        with self._lock:
            conns = list(self._clients.values())
        for conn in conns:
            conn.send_raw(line)

    # ---------------------------------------------------------------- life
    def start(self) -> bool:
        if self._running:
            return True
        os.makedirs(os.path.dirname(self.sock_path), exist_ok=True)
        if os.path.exists(self.sock_path):
            try:
                os.unlink(self.sock_path)
            except OSError:
                pass

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.bind(self.sock_path)
        except OSError as exc:
            print(f"[sayri-ipc] could not bind {self.sock_path}: {exc}")
            return False
        try:
            os.chmod(self.sock_path, 0o600)
        except OSError:
            pass
        sock.listen(16)
        sock.settimeout(1.0)
        self._listener = sock
        self._running = True
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()
        return True

    def stop(self) -> None:
        self._running = False
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
            self._listener = None
        with self._lock:
            conns = list(self._clients.values())
            self._clients.clear()
        for conn in conns:
            conn.close()
        try:
            if os.path.exists(self.sock_path):
                os.unlink(self.sock_path)
        except OSError:
            pass

    # -------------------------------------------------------------- accept
    def _accept_loop(self) -> None:
        while self._running and self._listener is not None:
            try:
                client, _addr = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with self._lock:
                cid = self._next_client_id
                self._next_client_id += 1
                conn = _ClientConn(cid, client, self)
                self._clients[cid] = conn
            print(f"[sayri-ipc] client #{cid} connected ({self.client_count()} total)")
            try:
                if self.on_connect:
                    self.on_connect(cid)
            except Exception as exc:  # noqa: BLE001
                print(f"[sayri-ipc] on_connect error: {exc}")
            conn.spawn()

    def _drop_client(self, conn: "_ClientConn") -> None:
        with self._lock:
            self._clients.pop(conn.client_id, None)
        print(f"[sayri-ipc] client #{conn.client_id} disconnected ({self.client_count()} total)")
        try:
            if self.on_disconnect:
                self.on_disconnect(conn.client_id)
        except Exception as exc:  # noqa: BLE001
            print(f"[sayri-ipc] on_disconnect error: {exc}")

    def _handle(self, conn: "_ClientConn", msg: dict) -> None:
        cmd = msg.get("cmd")
        req_id = msg.get("id")
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            params = {"value": params}
        handler = self._handlers.get(cmd)
        if handler is None:
            conn.reply({"ok": False, "error": f"unknown command: {cmd}", "id": req_id})
            return
        try:
            result = handler(params, conn.client_id)
            conn.reply({"ok": True, "result": result, "id": req_id})
        except Exception as exc:  # noqa: BLE001 - a bad handler must not kill the server
            print(f"[sayri-ipc] command {cmd} error: {exc}")
            conn.reply({"ok": False, "error": str(exc), "id": req_id})


class _ClientConn:
    """One connected client: a reader thread + a write lock."""

    def __init__(self, client_id: int, sock: socket.socket, server: SayriServer) -> None:
        self.client_id = client_id
        self.sock = sock
        self.server = server
        self._thread: Optional[threading.Thread] = None
        self._write_lock = threading.Lock()
        self._closed = False

    def spawn(self) -> None:
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def send_raw(self, data: bytes) -> None:
        if self._closed:
            return
        try:
            with self._write_lock:
                self.sock.sendall(data)
        except OSError:
            self.close()

    def reply(self, msg: dict) -> None:
        line = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        self.send_raw(line)

    def close(self) -> None:
        self._closed = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    def _read_loop(self) -> None:
        buf = b""
        try:
            while not self._closed:
                chunk = self.sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line.decode("utf-8"))
                    except Exception:  # noqa: BLE001
                        continue
                    if isinstance(msg, dict) and "cmd" in msg:
                        self.server._handle(self, msg)
        except OSError:
            pass
        finally:
            self.close()
            self.server._drop_client(self)


class SayriClient:
    """Client for talking to a running Sayri daemon.

    Lightweight: creates one socket, sends requests and optionally routes
    broadcast events to a callback. Not thread-safe by design (use one client
    per thread if needed).
    """

    def __init__(self, sock_path: Optional[str] = None) -> None:
        self.sock_path = sock_path or socket_path()
        self._sock: Optional[socket.socket] = None
        self._buf = b""
        self._next_id = 1
        self._pending: dict[int, dict] = {}
        self._lock = threading.Lock()
        self._event_cb: Optional[Callable[[dict], None]] = None
        self._listen_thread: Optional[threading.Thread] = None
        self._reader_started = False

    # --------------------------------------------------------------- conn
    def connect(self, timeout: float = 3.0) -> bool:
        if self._sock is not None:
            return True
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect(self.sock_path)
            s.settimeout(None)
            self._sock = s
            self._ensure_reader()
            return True
        except (OSError, FileNotFoundError):
            return False

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def close(self) -> None:
        if self._sock is not None:
            with self._lock:
                for fut in self._pending.values():
                    fut["done"].set()
                self._pending.clear()
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    # ----------------------------------------------------------- requests
    def _ensure_reader(self) -> None:
        """Make sure a background thread is draining socket responses/events."""
        if self._reader_started:
            return
        if self._sock is None:
            return  # not connected yet; connect() will start the reader
        self._reader_started = True
        self._listen_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._listen_thread.start()

    def request(self, cmd: str, params: Optional[dict] = None, timeout: float = 30.0):
        """Send a request and block until the matching response arrives."""
        if not self.connect():
            raise SayriError(f"Sayri daemon is not running ({self.sock_path})")
        s = self._sock
        self._ensure_reader()
        req_id = self._next_id
        self._next_id += 1

        done = threading.Event()
        result_box: list = []
        with self._lock:
            self._pending[req_id] = {"done": done, "result": result_box}

        payload = {
            "cmd": cmd,
            "params": params or {},
            "id": req_id,
        }
        try:
            line = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
            s.sendall(line)
        except OSError as exc:
            with self._lock:
                self._pending.pop(req_id, None)
            raise SayriError(f"could not send to daemon: {exc}")

        if not done.wait(timeout):
            with self._lock:
                self._pending.pop(req_id, None)
            raise SayriError(f"timed out waiting for '{cmd}' response")

        if result_box and isinstance(result_box[0], dict):
            resp = result_box[0]
            if not resp.get("ok"):
                raise SayriError(str(resp.get("error", "unknown error")))
            return resp.get("result")
        return None

    def listen_events(self, callback: Callable[[dict], None]) -> None:
        """Start delivering broadcast events to ``callback`` in background."""
        self._event_cb = callback
        self._ensure_reader()

    def _read_loop(self) -> None:
        s = self._sock
        if s is None:
            return
        try:
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                self._buf += chunk
                while b"\n" in self._buf:
                    line, self._buf = self._buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line.decode("utf-8"))
                    except Exception:  # noqa: BLE001
                        continue
                    if "event" in msg:
                        if self._event_cb:
                            try:
                                self._event_cb(msg)
                            except Exception:  # noqa: BLE001
                                pass
                    elif "id" in msg:
                        with self._lock:
                            fut = self._pending.pop(msg.get("id"), None)
                        if fut:
                            fut["result"].append(msg)
                            fut["done"].set()
        except OSError:
            pass

    # -------------------------------------------------------- convenience
    def ping(self, timeout: float = 3.0) -> bool:
        try:
            return self.request("ping", timeout=timeout) == "pong"
        except SayriError:
            return False


def daemon_is_running(sock_path: Optional[str] = None) -> bool:
    """Cheap probe: is a Sayri daemon listening on the control socket?"""
    if not os.path.exists(sock_path or socket_path()):
        return False
    c = SayriClient(sock_path)
    try:
        return c.connect(timeout=1.0) and c.ping()
    finally:
        c.close()


def wait_events(cmd: str, params: Optional[dict] = None, timeout: float = 120.0,
                listen_keys: Optional[list[str]] = None) -> dict:
    """Send a request and collect broadcast events until a condition is met.

    Returns the first event whose key is in ``listen_keys``, or the raw
    collected response for ``cmd``. Used by the CLI for ``ask``/``listen``.
    """
    c = SayriClient()
    if not c.connect():
        raise SayriError("Sayri daemon is not running")
    collected: list[dict] = []
    done = threading.Event()
    result_box: list = []

    def _on_event(ev: dict) -> None:
        collected.append(ev)
        if listen_keys and ev.get("event") in listen_keys:
            result_box.append(ev)
            done.set()

    c.listen_events(_on_event)
    # The request may answer before/after events; resolve in a thread.
    def _do_request() -> None:
        try:
            result_box.append({"__request__": True, "result": c.request(cmd, params, timeout=timeout)})
        except Exception as exc:  # noqa: BLE001
            result_box.append({"__request__": True, "error": str(exc)})
        finally:
            done.set()

    t = threading.Thread(target=_do_request, daemon=True)
    t.start()
    done.wait(timeout + 5)
    c.close()
    return (result_box[0] if result_box else {})