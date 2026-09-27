# Gateways

A gateway is a program that connects your Sayri to the outside world: Telegram, Discord, Slack, or anything that can deliver a text message. Gateways are plugins; everything in [plugins](plugins.md) applies to them.

## Instances

The `GatewaySupervisor` runs one process per instance. An instance binds:

- a **plugin** (which connector),
- an **agent profile** (who answers),
- a **sandbox level** (what that agent may execute),
- a **secret key** (credential from the vault, injected at process start, never in plaintext anywhere).

Several instances of the same platform can run in parallel, each bound to a different agent. Instances are stored in `~/.config/sayri/gateway_instances.json`, auto-start on launch (guarded by an exclusive lock so GUI and daemon never double-spawn), and log to `~/.local/share/sayri/logs/<instance>.log`.

## Environment contract

When the supervisor spawns your entrypoint it sets:

| Variable | Meaning |
| -------- | ------- |
| `SAYRI_GATEWAY_INSTANCE_ID` | Instance id. |
| `SAYRI_TARGET_AGENT` | Agent profile that must answer. |
| `SAYRI_SANDBOX_LEVEL` | Sandbox level for this channel's turns. |
| `SAYRI_ALLOW_RESUME_PREVIOUS` | `1` if the conversation may resume after inactivity. |
| `SAYRI_INACTIVITY_TIMEOUT` | Seconds of inactivity before stand-by. |
| `SAYRI_PID_FILE` | Write your PID here. |
| `SAYRI_AUTH_FILE` | Per-instance authorization store. |
| `SAYRI_PIN_FILE` | Per-instance pairing PIN (OTP pairing mode). |
| required secrets | Each name in `required_secrets`, value from the vault. |

`PYTHONPATH` already includes the Sayri lib, so `from sayri import ipc` works.

## The wire protocol

The simplest correct gateway is a loop that receives messages and sends one JSON line per turn to the daemon's legacy socket (`$SAYRI_STATE_DIR/sayri.sock`):

```python
import json, socket
from sayri import paths

def ask(text, author, session_id=None):
    msg = {
        "type": "INCOMING_MSG",
        "text": text,
        "author": author,
        "target_agent": os.environ["SAYRI_TARGET_AGENT"],
        "sandbox_level": os.environ["SAYRI_SANDBOX_LEVEL"],
        "instance_id": os.environ["SAYRI_GATEWAY_INSTANCE_ID"],
        "session_id": session_id,
    }
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(str(paths.state_dir() / "sayri-daemon.sock"))
    sock.settimeout(120)
    sock.sendall((json.dumps(msg) + "\n").encode())
    reply = b""
    while not reply.endswith(b"\n"):
        chunk = sock.recv(8192)
        if not chunk:
            break
        reply += chunk
    sock.close()
    return json.loads(reply.decode()).get("text", "")
```

The daemon answers `{"ok": true, "event": "done", "text": "..."}` with the agent's reply. Sessions are keyed by `session_id`; pass the channel conversation id to keep context, omit it for one-shot turns.

Two rules the daemon enforces for gateway turns:

- `approvable` is false: a command that would need the user's approval is refused, and the model is told to say so. Nobody is watching the desktop to click Allow.
- The sandbox level of the turn is the instance's, not the agent's default.

## Authentication

Pairing mode `pairing_otp`: on first contact the channel shows a PIN (written to `SAYRI_PIN_FILE`); the user confirms in the desktop panel and the channel id is stored in `SAYRI_AUTH_FILE`. Without a valid authorization, messages are ignored. `sayri gateway pin` shows the current PIN.

## CLI

```
sayri gateway list                  Instances and their state
sayri gateway start|stop <id>       Control one instance
sayri gateway delete <id>           Remove instance and configs
sayri gateway edit <id>             Change agent, level, timeout
sayri gateway pin <id>              Show pairing PIN
sayri plugins chat-url <id>         Open the chat_url of a gateway
```

## Writing one

1. Create the plugin directory with a `manifest.json` (`type: "gateway"`, `entrypoint`, `authorization.mode`, `required_secrets`).
2. Implement the channel side: receive messages, verify authorization, call the daemon as above, send the reply back.
3. Write your PID to `SAYRI_PID_FILE` and handle SIGTERM cleanly.
4. Store the token with `sayri vault set <KEY>` (or the panel) — never in the manifest.
5. Create an instance in the panel or `sayri gateway edit`, pick agent and level, and start it.
