# Architecture

Sayri is a Python package with a hexagonal core. The headless brain knows nothing about windows; every interface attaches to it through one event sink.

## The core

`sayri/core.py` defines `SayriCore`, the headless engine. It owns the voice loop (STT session), TTS, the ReAct agent engine, sandbox, storage, wake word handling, sessions and remote/gateway message processing. It emits state changes through a `CoreUI` sink — an object with no-op callbacks:

```
on_ready, on_state, on_audio_level, on_mic, on_busy, on_partial,
on_user, on_assistant_delta, on_assistant_done, on_tool_start,
on_tool_finish, on_hint, on_error, on_speaking, on_show, on_hide,
on_conversation_started, on_shutdown
```

Any UI — the GTK orb, a Clippy widget, the CLI, a web bubble — subclasses or wraps this sink. Nothing else is required to build a new front-end.

## The domain layer

`sayri/domain/` holds the pieces that make decisions:

| Module | Responsibility |
| ------ | -------------- |
| `models.py` | Dataclasses: `AgentProfile`, `SandboxConfig`, `SandboxLevel`, `Session`, `PluginManifest`, `SecurityAuditReport` |
| `agent_engine.py` | ReAct loop: builds the system prompt, runs tool calls, feeds observations back, persists messages |
| `agent_creator.py` | Creates sub-agents from natural language, with privilege containment |
| `permissions.py` | Rule engine: `{action, resource, effect}` with whole-value wildcards, last match wins |
| `permission_broker.py` | The ask/checkpoint flow: a card with Allow / Allow always / Deny |
| `skills_scanner.py` | Static pre-install audit of skills and plugins, with a risk score |
| `secrets_manager.py` | Zero-plaintext vault: secrets never reach the LLM prompt or chat history |
| `cron_scheduler.py` | Routines: `on_login`, `daily_at`, `hourly` triggers that run prompts |
| `triggers.py` | System event triggers stored as JSON files |

## The adapters

`sayri/adapters/` holds the boundaries:

- `sandbox/executor.py` — `SandboxExecutor`, the only code that runs commands. Enforces the level, the permission rules and the policies before anything executes.
- `storage/sqlite_sessions.py` — sessions, messages, learned preferences and security authorizations in one SQLite file.

## Process model

There are three ways Sayri runs, and they can coexist:

1. **GUI** (`python3 -m sayri` or `sayri`): GTK4 overlay with the orb and the cajita. Owns the legacy socket `sayri.sock`.
2. **Daemon** (`sayri.daemon`): headless. Owns `SayriCore` and exposes it on `sayri-daemon.sock`. It also keeps a legacy listener on `sayri.sock` speaking the old gateway wire protocol so channel gateways keep working without changes.
3. **CLI** (`sayri.cli`): one-shot commands over the same core.

Every core event is broadcast to all connected IPC clients, so a Clippy widget and the GTK orb see exactly the same stream.

## IPC protocol

JSON over a local socket, one message per line (NDJSON), defined in `sayri/ipc.py`.

Requests: `{"cmd": "talk", "params": {"text": "..."}, "id": 1}`

Responses: `{"ok": true, "result": {...}, "id": 1}`

Events broadcast to every client: `ready, state, audio_level, mic, busy, partial, user, assistant_delta, assistant_done, tool_start, tool_finish, hint, error, speaking, shutdown`.

Commands: `ping, status, talk, ask, listen, stop_listening, toggle_listening, interrupt, new_conversation, switch_session, config_get, config_set, config_list, skills_list, skills_install, skills_uninstall, skills_search, plugins_list, gateway_list, gateway_start, gateway_stop, gateway_delete, gateway_save, agents_list, version, quit` — plus the permission, settings, routines, vault and UI commands the cajita panel uses.

The socket is `chmod 600` and rejects connections from other users (peer UID validation).

## The agent loop

`AgentEngine.process_query` runs in a background thread:

1. Detects sub-agent or skill-install intent in natural language and refuses it from restricted levels (levels 0–2 cannot create sub-agents or install anything).
2. Builds the system prompt: profile, sandbox level in plain language, installed skills, tool restrictions, and the investigation-loop protocol if enabled.
3. Compacts history: the last 4 messages verbatim, older ones as a summary, `search_history` as an escape hatch.
4. Streams from the OpenAI-compatible endpoint. When the reply contains a ` ```bash ` block, extracts it, checks permissions, executes through the sandbox, sanitizes the output for secrets, and feeds it back as an observation — up to 6 steps (10 with the investigation loop).
5. Anything longer than 4 seconds is treated as launched in the background (for GUI apps and `xdg-open`).
6. Root operations go through `pkexec`, which shows a graphical Polkit dialog the user must approve.
7. With `reinforcement_learning` on, substantive commands are recorded as learned preferences queryable with `sayri-pref`.

## Multi-agent

Agents are JSON profiles in `~/.config/sayri/agents/`. Each profile carries its own model config, sandbox config (level, rules, blocklists, ask switch), allowed skills, allowed plugins, allowed tools, custom instructions, and the investigation/learning switches. The engine enforces non-escalation: an agent at level 0–2 cannot create sub-agents, and a sub-agent can never get a level higher than its creator.

Gateway instances bind a channel to an agent: one process per instance, managed by the `GatewaySupervisor`. Several gateways, agents and sandbox levels run in parallel.
