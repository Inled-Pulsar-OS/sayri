"""Bridge to the local Sayri daemon (UNIX socket, one JSON object per line).

Only that local socket is used: no network port, no daemon secrets read. The
sandbox level is forwarded so the daemon enforces the instance's isolation.
"""
import json
import os
import socket
from typing import Optional

MAX_REPLY_BYTES = 1024 * 1024


def daemon_socket_path(state_dir: str) -> str:
    return os.path.join(state_dir, "sayri-daemon.sock")


def ask(sock_path: str, text: str, author: str, target_agent: str,
        sandbox_level: str, session_id: str, timeout: float = 180.0) -> Optional[str]:
    """Send one message to the daemon; None when unreachable or unparseable."""
    if not os.path.exists(sock_path):
        return None

    message = {"type": "INCOMING_MSG", "text": text, "author": author,
               "target_agent": target_agent, "sandbox_level": sandbox_level,
               "session_id": session_id}

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    chunks: list = []
    try:
        sock.settimeout(timeout)
        sock.connect(sock_path)
        sock.sendall((json.dumps(message) + "\n").encode("utf-8"))
        received = 0
        while True:
            chunk = sock.recv(8192)
            if not chunk:
                break
            chunks.append(chunk)
            received += len(chunk)
            if received > MAX_REPLY_BYTES or chunk.endswith(b"\n"):
                break
    except OSError:
        return None
    finally:
        sock.close()

    try:
        reply = json.loads(b"".join(chunks).decode("utf-8", "replace"))
    except ValueError:
        return None
    text_out = reply.get("text") if isinstance(reply, dict) else None
    return str(text_out) if text_out else None