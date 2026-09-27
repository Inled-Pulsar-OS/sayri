"""Sayri headless daemon: wires SayriCore to the IPC socket for external UIs.

The daemon owns the singleton SayriCore instance and exposes it to the world
over ``SAYRI_STATE_DIR/sayri-daemon.sock`` (see :mod:`sayri.ipc`).  Every core
event (state, partial, assistant_delta, tool events, ...) is re-broadcast to
all connected clients, so any UI — the GTK orb, a Clippy/Bonzi widget, the
CLI — behaves identically.

It also keeps a *legacy* listener on ``sayri.sock`` speaking the old gateway
wire protocol (``{"type": "INCOMING_MSG", ...}``) so existing channel gateway
plugins (Telegram/Discord/...) keep working without changes. If the GUI already
owns that socket, the daemon simply skips it.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from typing import Any, Optional

from . import __version__, config, paths
from .core import CoreUI, SayriCore
from .domain.agent_creator import AgentCreator
from .domain.cron_scheduler import Routine, cron_scheduler
from .domain.models import AgentProfile
from .domain.secrets_manager import secrets_manager
from .gateway_supervisor import gateway_supervisor
from .ipc import SayriServer, SOCK_NAME, socket_path
from . import skills


class BridgeUI(CoreUI):
    """Forwards SayriCore events onto the IPC broadcast."""

    def __init__(self, server: SayriServer) -> None:
        self._server = server

    def on_ready(self, info: dict) -> None:
        self._server.broadcast("ready", info=info)

    def on_state(self, state: str) -> None:
        self._server.broadcast("state", state=state)

    def on_audio_level(self, level: float) -> None:
        self._server.broadcast("audio_level", level=level)

    def on_mic(self, active: bool) -> None:
        self._server.broadcast("mic", active=active)

    def on_busy(self, active: bool) -> None:
        self._server.broadcast("busy", active=active)

    def on_partial(self, text: str) -> None:
        self._server.broadcast("partial", text=text)

    def on_user(self, text: str) -> None:
        self._server.broadcast("user", text=text)

    def on_assistant_delta(self, delta: str) -> None:
        self._server.broadcast("assistant_delta", delta=delta)

    def on_assistant_done(self, text: str) -> None:
        self._server.broadcast("assistant_done", text=text)

    def on_tool_start(self, command: str) -> None:
        self._server.broadcast("tool_start", command=command)

    def on_tool_finish(self, command: str, output: str, exit_code: int) -> None:
        self._server.broadcast("tool_finish", command=command, output=output, exit_code=exit_code)

    def on_hint(self, text: str) -> None:
        self._server.broadcast("hint", text=text)

    def on_error(self, message: str) -> None:
        self._server.broadcast("error", error=message)

    def on_speaking(self, active: bool) -> None:
        self._server.broadcast("speaking", active=active)

    def on_show(self) -> None:
        self._server.broadcast("show")

    def on_hide(self) -> None:
        self._server.broadcast("hide")

    def on_conversation_started(self, session_id: str) -> None:
        self._server.broadcast("conversation_started", session_id=session_id)

    def on_shutdown(self) -> None:
        self._server.broadcast("shutdown")


def _agent_to_dict(a: Any) -> dict:
    model = getattr(a, "model", None)
    sandbox = getattr(a, "sandbox", None)
    return {
        "id": a.id,
        "name": a.name,
        "description": a.description,
        "system_prompt": a.system_prompt,
        "is_builtin": bool(getattr(a, "is_builtin", False)),
        "model": {
            "provider": getattr(model, "provider", None),
            "model_name": getattr(model, "model_name", None),
            "temperature": getattr(model, "temperature", None),
        },
        "sandbox": {
            "level": getattr(sandbox.level, "value", str(getattr(sandbox, "level", ""))),
            "timeout_seconds": getattr(sandbox, "timeout_seconds", None),
            "allow_network": getattr(sandbox, "allow_network", None),
        },
        "allowed_skills": list(getattr(a, "allowed_skills", []) or []),
        "allowed_tools": list(getattr(a, "allowed_tools", []) or []),
        "custom_instructions": getattr(a, "custom_instructions", ""),
    }


_SECRET_KEY_HINTS = ("api_key", "apikey", "token", "secret", "password", "passwd", "credential")


def _is_secret_key(key: str) -> bool:
    """True when a config key looks like it holds a credential."""
    lowered = str(key).lower()
    return any(hint in lowered for hint in _SECRET_KEY_HINTS)


def _mask_secret_value(key: str, value: Any) -> Any:
    """Hide the middle of a credential, leaving just enough to recognise it."""
    if not _is_secret_key(key) or not isinstance(value, str) or not value:
        return value
    if len(value) <= 8:
        return "***"
    return f"{value[:3]}...{value[-3:]}"


def _find_manifest(plugin_id: str) -> Optional[dict]:
    """Locate an installed plugin's manifest.json by id, user scope first."""
    from . import paths as _paths
    from pathlib import Path

    for root in (_paths.plugins_dir(), _paths.shared_plugins_dir()):
        candidate = Path(root) / plugin_id / "manifest.json"
        if not candidate.is_file():
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("id", plugin_id) == plugin_id:
            return data
    return None


def _declared_type(dotted_key: str) -> str:
    """The type config.py declares for ``group.key`` ("int", "bool", ...)."""
    group, _, name = dotted_key.partition(".")
    return config._TYPES.get(group, {}).get(name, "string")


class SayriDaemon:
    def __init__(self) -> None:
        self.server = SayriServer(
            socket_path(),
            on_connect=self._on_connect,
            on_disconnect=self._on_disconnect,
        )
        self.core = SayriCore(ui=BridgeUI(self.server))
        self._gateways_started = False
        self._legacy_sock: Optional[socket.socket] = None
        self._legacy_running = False
        # In-flight asset downloads, keyed by a token so a repeated request for
        # the same file joins the running one instead of racing it.
        self._downloads: dict[str, bool] = {}
        self._download_lock = threading.Lock()
        self._register_handlers()

    # ----------------------------------------------------------- plumbing
    def _on_connect(self, client_id: int) -> None:
        self.core.ui_visible = True

    def _on_disconnect(self, client_id: int) -> None:
        if self.server.client_count() == 0:
            self.core.ui_visible = False

    def _register_handlers(self) -> None:
        s = self.server
        s.register("ping", lambda p, c: "pong")
        s.register("version", lambda p, c: __version__)
        s.register("status", lambda p, c: self.core.status_info())
        s.register("talk", lambda p, c: self._cmd_talk(p))
        s.register("ask", lambda p, c: self._cmd_ask(p))
        s.register("listen", lambda p, c: self._cmd_listen(p))
        s.register("stop_listening", lambda p, c: self._cmd_stop(p))
        s.register("toggle_listening", lambda p, c: self._cmd_toggle(p))
        s.register("interrupt", lambda p, c: self._cmd_interrupt(p))
        s.register("new_conversation", lambda p, c: self._cmd_new_conversation(p))
        s.register("switch_session", lambda p, c: self._cmd_switch_session(p))
        s.register("config_get", lambda p, c: self._cmd_config_get(p))
        s.register("config_set", lambda p, c: self._cmd_config_set(p))
        s.register("config_list", lambda p, c: self._cmd_config_list())
        s.register("skills_list", lambda p, c: skills.list_skills())
        s.register("skills_install", lambda p, c: skills.install_skill(str(p.get("slug", ""))))
        s.register("skills_uninstall", lambda p, c: skills.uninstall_skill(str(p.get("slug", ""))))
        s.register("skills_search", lambda p, c: skills.search_skills(str(p.get("query", ""))))
        s.register("plugins_list", lambda p, c: self._cmd_plugins_list())
        s.register("plugin_set_enabled", lambda p, c: self._cmd_plugin_set_enabled(p))
        s.register("gateway_list", lambda p, c: gateway_supervisor.list_instances())
        s.register("gateway_start", lambda p, c: self._cmd_gateway_start(p))
        s.register("gateway_stop", lambda p, c: self._cmd_gateway_stop(p))
        s.register("gateway_delete", lambda p, c: self._cmd_gateway_delete(p))
        s.register("gateway_save", lambda p, c: self._cmd_gateway_save(p))
        s.register("agents_list", lambda p, c: [_agent_to_dict(a) for a in AgentCreator.list_agents()])
        s.register("agent_switch", lambda p, c: self._cmd_agent_switch(p))
        s.register("routines_run", lambda p, c: self._cmd_routines_run(p))
        s.register("sessions_list", lambda p, c: self._cmd_sessions_list(p))
        s.register("session_get", lambda p, c: self._cmd_session_get(p))
        s.register("session_rename", lambda p, c: self._cmd_session_rename(p))
        s.register("session_delete", lambda p, c: self._cmd_session_delete(p))
        s.register("routines_list", lambda p, c: [r.to_dict() for r in cron_scheduler.list_routines()])
        s.register("routine_save", lambda p, c: self._cmd_routine_save(p))
        s.register("routine_delete", lambda p, c: self._cmd_routine_delete(p))
        s.register("routine_set_enabled", lambda p, c: self._cmd_routine_set_enabled(p))
        s.register("vault_list", lambda p, c: secrets_manager.list_secrets())
        s.register("vault_set", lambda p, c: self._cmd_vault_set(p))
        s.register("vault_delete", lambda p, c: self._cmd_vault_delete(p))
        s.register("agent_save", lambda p, c: self._cmd_agent_save(p))
        s.register("agent_delete", lambda p, c: self._cmd_agent_delete(p))
        s.register("ui_list", lambda p, c: self._cmd_ui_list())
        s.register("ui_start", lambda p, c: self._cmd_ui_start(p))
        s.register("ui_stop", lambda p, c: self._cmd_ui_stop(p))
        s.register("ui_status", lambda p, c: self._cmd_ui_status(p))
        s.register("clipboard_copy", lambda p, c: self._cmd_clipboard_copy(p))
        s.register("settings_schema", lambda p, c: self._cmd_settings_schema())
        s.register("settings_save", lambda p, c: self._cmd_settings_save(p))
        s.register("plugin_settings_set", lambda p, c: self._cmd_plugin_settings_set(p))
        s.register("asset_download", lambda p, c: self._cmd_asset_download(p))
        s.register("quit", lambda p, c: self._cmd_quit(p))

    # ------------------------------------------------------------ helpers
    def _cmd_talk(self, params: dict) -> dict:
        text = str(params.get("text", "")).strip()
        if not text:
            raise ValueError("missing 'text'")
        self.core.send_text(text)
        return {"sent": True, "text": text, "session_id": self.core.active_session_id}

    def _cmd_ask(self, params: dict) -> dict:
        text = str(params.get("text", "")).strip()
        if not text:
            raise ValueError("missing 'text'")
        timeout = float(params.get("timeout", 120.0))
        result = self.core.ask(text, timeout=timeout)
        return result

    def _cmd_listen(self, params: dict) -> dict:
        timeout = float(params.get("timeout", 60.0))
        text = self.core.listen_once(timeout=timeout)
        return {"text": text}

    def _cmd_stop(self, params: dict) -> dict:
        self.core.stop_listening()
        return {"stopped": True}

    def _cmd_toggle(self, params: dict) -> dict:
        self.core.toggle_listening()
        return {"state": self.core.state}

    def _cmd_interrupt(self, params: dict) -> dict:
        self.core.interrupt()
        return {"interrupted": True}

    def _cmd_new_conversation(self, params: dict) -> dict:
        self.core.new_conversation()
        return {"session_id": self.core.active_session_id}

    def _cmd_switch_session(self, params: dict) -> dict:
        sid = str(params.get("session_id", "")).strip()
        if not sid:
            raise ValueError("missing 'session_id'")
        self.core.switch_session(sid)
        return {"session_id": self.core.active_session_id}

    def _cmd_config_get(self, params: dict) -> dict:
        path = str(params.get("key", "")).strip()
        if "." in path:
            group, key = path.split(".", 1)
        else:
            group = str(params.get("group", "")).strip()
            key = path
        if group not in config.DEFAULTS or key not in config.DEFAULTS[group]:
            raise ValueError(f"unknown setting: {group}.{key}")
        return {"group": group, "key": key, "value": config.config.get(group, key)}

    def _cmd_config_set(self, params: dict) -> dict:
        path = str(params.get("key", "")).strip()
        if "." in path:
            group, key = path.split(".", 1)
        else:
            group = str(params.get("group", "")).strip()
            key = path
        if group not in config.DEFAULTS or key not in config.DEFAULTS[group]:
            raise ValueError(f"unknown setting: {group}.{key}")
        value = params.get("value")
        gtype = config._TYPES[group].get(key, "string")
        try:
            if gtype == "bool":
                config.config.set_bool(group, key, bool(value))
            elif gtype in ("int", "double"):
                num = float(value)
                if gtype == "int":
                    num = int(num)
                config.config.set(group, key, num)
            else:
                config.config.set_string(group, key, str(value))
        except Exception as exc:
            raise ValueError(f"could not set {group}.{key}={value!r}: {exc}") from exc
        if group == "ui" and key in ("autostart", "autostart_mode"):
            from .autostart import apply_autostart
            try:
                apply_autostart(config.config)
            except Exception as exc:  # noqa: BLE001
                print(f"[sayri-daemon] autostart update error: {exc}")
        return {"group": group, "key": key, "value": config.config.get(group, key)}

    def _cmd_config_list(self) -> list:
        # Credentials live in the same config file as everything else, so a
        # plain dump hands the provider API key to any connected client (and
        # straight into a desktop UI's settings list). Mask the obvious secret
        # fields; a UI that genuinely needs one value can still ask for that
        # single key with config_get.
        return [
            {
                "group": g,
                "key": k,
                "value": _mask_secret_value(k, config.config.get(g, k)),
                "is_secret": _is_secret_key(k),
            }
            for g in config.DEFAULTS
            for k in config.DEFAULTS[g]
        ]

    def _cmd_plugin_set_enabled(self, params: dict) -> dict:
        """Turn a plugin's background service on or off.

        ``enabled`` persists the choice (so it survives a reboot) and starts or
        stops the running process to match.
        """
        from . import plugin_service

        plugin_id = str(params.get("plugin_id", "")).strip()
        if not plugin_id:
            raise ValueError("missing 'plugin_id'")
        manifest = _find_manifest(plugin_id)
        if manifest is None:
            raise ValueError(f"plugin not found: {plugin_id}")
        if plugin_service.service_block(manifest) is None:
            raise ValueError(f"{plugin_id} has no background service")

        enabled = bool(params.get("enabled", True))
        plugin_service.set_service_enabled(manifest, enabled)
        if enabled:
            ok, msg = plugin_service.start_service(manifest)
        else:
            ok, msg = plugin_service.stop_service(manifest)
        return {"plugin_id": plugin_id, "enabled": enabled, "ok": bool(ok), "message": msg}

    def _cmd_gateway_start(self, params: dict) -> dict:
        inst_id = str(params.get("instance_id", "")).strip()
        ok, msg = gateway_supervisor.start_instance(inst_id)
        if not ok:
            raise ValueError(msg)
        return {"started": True, "message": msg}

    def _cmd_gateway_stop(self, params: dict) -> dict:
        inst_id = str(params.get("instance_id", "")).strip()
        gateway_supervisor.stop_instance(inst_id)
        return {"stopped": True, "instance_id": inst_id}

    def _cmd_gateway_delete(self, params: dict) -> dict:
        inst_id = str(params.get("instance_id", "")).strip()
        gateway_supervisor.delete_instance(inst_id)
        return {"deleted": True, "instance_id": inst_id}

    def _cmd_gateway_save(self, params: dict) -> dict:
        data = params.get("instance", params)
        inst_id = str(data.get("id", "")).strip()
        if not inst_id:
            raise ValueError("missing instance 'id'")
        gateway_supervisor.save_instance(dict(data))
        return {"saved": True, "instance_id": inst_id}

    def _cmd_plugins_list(self) -> list:
        from . import plugin_service

        rows = []
        for p in gateway_supervisor.list_installed_plugins():
            manifest = _find_manifest(str(p.get("id") or ""))
            has_service = bool(manifest and plugin_service.service_block(manifest))
            rows.append({
                "id": p.get("id"),
                "name": p.get("name"),
                "description": p.get("description", ""),
                "version": p.get("version"),
                "auth_mode": p.get("auth_mode"),
                "required_secrets": p.get("required_secrets", []),
                "path": str(p["path"]) if p.get("path") else "",
                # A plugin can be an autostart service, a gateway, a UI, or a
                # plain bundle of skills. The UI only offers the switch when
                # there is genuinely a service to start.
                "has_service": has_service,
                "service_enabled": plugin_service.service_enabled(manifest) if has_service else None,
                "service_running": plugin_service.service_running(manifest) if has_service else None,
            })
        return rows

    def _cmd_sessions_list(self, params: dict) -> list:
        limit = int(params.get("limit", 20))
        rows = []
        for s in self.core.storage.list_sessions(limit=limit, include_empty=False):
            rows.append({
                "id": s.id,
                "title": s.title,
                "agent_id": s.agent_id,
                "messages": len(s.messages),
                "updated_at": s.updated_at,
            })
        return rows

    def _cmd_quit(self, params: dict) -> dict:
        """Stop shortly after replying so the IPC response is flushed first."""
        threading.Timer(0.3, self.stop).start()
        return {"ok": True}

    # ------------------------------------------------------------ sessions
    def _cmd_session_get(self, params: dict) -> dict:
        """Full transcript of one session, so a UI can re-render its history."""
        sid = str(params.get("session_id", "")).strip()
        if not sid:
            raise ValueError("missing 'session_id'")
        session = self.core.storage.get_session(sid)
        if session is None:
            raise ValueError(f"session not found: {sid}")
        return {
            "id": session.id,
            "title": session.title,
            "agent_id": session.agent_id,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "messages": [
                {
                    "role": getattr(m, "role", ""),
                    "content": getattr(m, "content", ""),
                    "timestamp": getattr(m, "timestamp", 0.0),
                }
                for m in (session.messages or [])
            ],
        }

    def _cmd_session_rename(self, params: dict) -> dict:
        sid = str(params.get("session_id", "")).strip()
        title = str(params.get("title", "")).strip()
        if not sid:
            raise ValueError("missing 'session_id'")
        if not title:
            raise ValueError("missing 'title'")
        self.core.storage.update_session_title(sid, title)
        return {"session_id": sid, "title": title}

    def _cmd_session_delete(self, params: dict) -> dict:
        sid = str(params.get("session_id", "")).strip()
        if not sid:
            raise ValueError("missing 'session_id'")
        self.core.storage.delete_session(sid)
        return {"session_id": sid, "deleted": True}

    # ------------------------------------------------------------ routines
    def _cmd_routine_save(self, params: dict) -> dict:
        """Create or update a routine. Only ``id`` decides which."""
        rid = str(params.get("id", "")).strip()
        trigger = str(params.get("trigger", "daily_at")).strip()
        if trigger not in ("on_login", "daily_at", "hourly", "cron"):
            raise ValueError(f"unknown trigger: {trigger}")
        existing = next((r for r in cron_scheduler.list_routines() if r.id == rid), None)
        routine = Routine(
            id=existing.id if existing else (rid or f"r{int(time.time() * 1000)}"),
            name=str(params.get("name", "")).strip() or "Untitled routine",
            description=str(params.get("description", "")).strip(),
            trigger=trigger,
            time_spec=str(params.get("time_spec", "09:00")).strip() or "09:00",
            prompt=str(params.get("prompt", "")),
            agent_id=str(params.get("agent_id", "default")).strip() or "default",
            speak_tts=bool(params.get("speak_tts", True)),
            notify_desktop=bool(params.get("notify_desktop", True)),
            enabled=bool(params.get("enabled", True)),
            last_run=existing.last_run if existing else 0.0,
            created_at=existing.created_at if existing else time.time(),
        )
        cron_scheduler.save_routine(routine)
        return routine.to_dict()

    def _cmd_routine_delete(self, params: dict) -> dict:
        rid = str(params.get("routine_id", "")).strip()
        if not rid:
            raise ValueError("missing 'routine_id'")
        cron_scheduler.delete_routine(rid)
        return {"routine_id": rid, "deleted": True}

    def _cmd_routine_set_enabled(self, params: dict) -> dict:
        rid = str(params.get("routine_id", "")).strip()
        if not rid:
            raise ValueError("missing 'routine_id'")
        cron_scheduler.toggle_routine(rid, bool(params.get("enabled", True)))
        return {"routine_id": rid, "enabled": bool(params.get("enabled", True))}

    # --------------------------------------------------------------- vault
    def _cmd_vault_set(self, params: dict) -> dict:
        key = str(params.get("key", "")).strip()
        if not key:
            raise ValueError("missing 'key'")
        secrets_manager.set_secret(
            key,
            str(params.get("value", "")),
            str(params.get("description", "")),
        )
        return {"key": key, "saved": True}

    def _cmd_vault_delete(self, params: dict) -> dict:
        key = str(params.get("key", "")).strip()
        if not key:
            raise ValueError("missing 'key'")
        return {"key": key, "deleted": bool(secrets_manager.delete_secret(key))}

    # -------------------------------------------------------------- agents
    def _cmd_agent_save(self, params: dict) -> dict:
        """Create or update an agent profile from a flat parameter dict."""
        agent_id = str(params.get("id", "")).strip()
        existing = AgentCreator.get_agent(agent_id) if agent_id else None
        if existing is not None and existing.is_builtin and agent_id != str(params.get("id", "")):
            raise ValueError("built-in agents cannot be replaced")

        def _str_list(value: Any) -> list:
            if isinstance(value, list):
                return [str(v) for v in value if str(v).strip()]
            if value is None:
                return []
            return [part.strip() for part in str(value).split(",") if part.strip()]

        profile = AgentProfile(
            id=agent_id or str(params.get("name", "")).strip().lower().replace(" ", "-"),
            name=str(params.get("name", "")).strip() or "New agent",
            description=str(params.get("description", "")).strip(),
            system_prompt=str(params.get("system_prompt", "")).strip(),
            custom_instructions=str(params.get("custom_instructions", "")),
            allowed_skills=_str_list(params.get("allowed_skills")),
            allowed_plugins=_str_list(params.get("allowed_plugins")),
            allowed_tools=_str_list(params.get("allowed_tools")) or ["bash", "read_skill", "search_history"],
            investigation_loop=bool(params.get("investigation_loop", True)),
            reinforcement_learning=bool(params.get("reinforcement_learning", True)),
        )
        if existing is not None:
            profile.id = existing.id
            profile.created_at = existing.created_at
            profile.is_builtin = existing.is_builtin
            profile.model = existing.model
            profile.sandbox = existing.sandbox
        saved_id = AgentCreator.save_agent(profile)
        return {"id": saved_id, "name": profile.name}

    def _cmd_agent_delete(self, params: dict) -> dict:
        agent_id = str(params.get("agent_id", "")).strip()
        if not agent_id:
            raise ValueError("missing 'agent_id'")
        agent = AgentCreator.get_agent(agent_id)
        if agent is not None and agent.is_builtin:
            raise ValueError("built-in agents cannot be deleted")
        return {"agent_id": agent_id, "deleted": AgentCreator.delete_agent(agent_id)}

    # ------------------------------------------------------------------ ui
    def _cmd_ui_list(self) -> list:
        """Installed UI plugins with their live status, for the Plugins tab.

        Delegates to the CLI scanner so there is exactly one definition of
        "installed UI plugin" between the terminal and the desktop UIs.
        """
        from .cli import _list_ui_plugins

        rows = []
        for pl in _list_ui_plugins():
            rows.append({
                "id": pl.get("id", ""),
                "name": pl.get("name", ""),
                "version": pl.get("version", ""),
                "description": pl.get("description", ""),
                "kind": pl.get("kind", ""),
                "running": bool(pl.get("running")),
                "pid": pl.get("pid"),
                "is_default": bool(pl.get("is_default")),
            })
        return rows

    @staticmethod
    def _ui_running(ui_id: str) -> bool:
        from . import sysinfo

        pid_file = os.path.join(paths.state_dir(), f"{ui_id}.pid")
        try:
            pid = int(open(pid_file, encoding="utf-8").read().strip())
        except (OSError, ValueError):
            return False
        return sysinfo.is_pid_alive(pid)

    def _cmd_ui_start(self, params: dict) -> dict:
        from . import sysinfo

        ui_id = str(params.get("ui_id", "")).strip()
        if not ui_id:
            raise ValueError("missing 'ui_id'")
        if self._ui_running(ui_id):
            return {"ui_id": ui_id, "running": True, "already_running": True}
        # Reuse the CLI so plugin launching stays in one place: the orb's
        # gtk4-layer-shell preload has to be stripped or GTK3 UIs die on start.
        from .cli import cmd_ui
        import io
        import contextlib

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cmd_ui({"ui": ui_id}, [])
        return {"ui_id": ui_id, "running": code == 0, "output": buf.getvalue().strip()}

    def _cmd_ui_stop(self, params: dict) -> dict:
        from . import sysinfo

        ui_id = str(params.get("ui_id", "")).strip()
        if not ui_id:
            raise ValueError("missing 'ui_id'")
        return {"ui_id": ui_id, "stopped": ui_id in sysinfo.stop_ui_plugins({ui_id})}

    def _cmd_ui_status(self, params: dict) -> dict:
        ui_id = str(params.get("ui_id", "")).strip()
        if not ui_id:
            raise ValueError("missing 'ui_id'")
        return {"ui_id": ui_id, "running": self._ui_running(ui_id)}

    def _cmd_clipboard_copy(self, params: dict) -> dict:
        """Hand text to the desktop clipboard.

        UIs without a clipboard of their own (the WebKit companion balloon)
        route this through the daemon so GTK stays the only clipboard owner.
        """
        text = str(params.get("text", ""))
        try:
            import shutil
            import subprocess

            for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "-b"]):
                if not shutil.which(cmd[0]):
                    continue
                proc = subprocess.run(cmd, input=text.encode("utf-8"), timeout=5, check=False)
                if proc.returncode == 0:
                    return {"copied": True, "via": cmd[0]}
            return {"copied": False, "reason": "no clipboard tool available (install wl-clipboard or xclip)"}
        except Exception as exc:  # noqa: BLE001
            return {"copied": False, "reason": str(exc)}

    # -------------------------------------------------------------- settings
    def _cmd_settings_schema(self) -> dict:
        """Curated settings description: core sections plus per-plugin sections.

        A UI that hardcodes its own settings form drifts from the config within
        one release, and the same key ends up labelled three different ways.
        Returning the description from the daemon keeps one wording everywhere.
        """
        from . import plugin_settings as _ps
        from . import settings_schema as _ss

        sections = _ss.schema()
        plugins = []
        for p in gateway_supervisor.list_installed_plugins():
            plugin_id = str(p.get("id") or "")
            manifest = _find_manifest(plugin_id)
            if not manifest or not _ps.settings_schema(manifest):
                continue
            plugins.append({
                "id": plugin_id,
                "name": p.get("name") or plugin_id,
                "description": p.get("description", ""),
                "type": manifest.get("type", ""),
                "manifest": manifest,
            })
        return {
            "sections": sections,
            "plugin_sections": _ss.plugin_sections(plugins),
        }

    @staticmethod
    def _settings_fields() -> dict:
        """Flat ``{field_key: field}`` index over the core settings schema."""
        from . import settings_schema as _ss

        index: dict[str, dict] = {}
        for section in _ss.schema():
            for field in section["fields"]:
                index[field["key"]] = field
        return index

    @staticmethod
    def _coerce_setting(field: dict, value: Any) -> list[tuple[str, str, Any]]:
        """Turn one submitted value into the ``(group, key, value)`` writes it means.

        Fields are allowed to be virtual: ``tts.voice_pick`` is really three
        config keys glued into one friendly choice, and a saved value has to be
        split back apart. Anything unrecognised is passed through as a single
        key so a hand-written client still works.
        """
        kind = field.get("kind", "text")
        writes = field.get("writes") or [field["key"]]

        if kind == "choice":
            allowed = [o.get("value") for o in field.get("options") or []]
            coerced = value if isinstance(value, str) else str(value)
            if allowed and coerced not in allowed:
                raise ValueError(
                    f"{field['key']}: {coerced!r} is not one of the offered options")
        elif kind == "toggle":
            if isinstance(value, bool):
                coerced: Any = value
            else:
                coerced = str(value).strip().lower() in ("1", "true", "yes", "on")
        elif kind == "number":
            try:
                coerced = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{field['key']}: {value!r} is not a number") from exc
            for bound, tighter in (("min", max), ("max", min)):
                limit = field.get(bound)
                if isinstance(limit, (int, float)):
                    coerced = tighter(coerced, limit)
            # An integer setting must stay integral, or config.json slowly fills
            # with "140.0" and the read side has to cope.
            if _declared_type(field["key"]) == "int":
                coerced = int(coerced)
        else:
            coerced = "" if value is None else str(value)

        if len(writes) == 1:
            return [(writes[0].partition(".")[0], writes[0].partition(".")[2], coerced)]

        parts = str(value).split("|")
        if len(parts) != len(writes):
            raise ValueError(f"{field['key']}: expected {'|'.join(writes)}")
        return [
            (w.partition(".")[0], w.partition(".")[2], p)
            for w, p in zip(writes, parts)
        ]

    def _cmd_settings_save(self, params: dict) -> dict:
        """Apply a batch of settings values.

        Typed values arrive together with the section's Save button, so one call
        persists a whole section (and writes the file once) instead of one call
        per field. Failures are reported per key rather than aborting the batch,
        so a bad number in one box does not lose the other edits in the section.
        """
        values = params.get("values")
        if not isinstance(values, dict) or not values:
            raise ValueError("missing 'values'")

        index = self._settings_fields()
        saved: list[str] = []
        errors: dict[str, str] = {}
        applied: dict[str, Any] = {}
        touched_ui_startup = False

        for key, value in values.items():
            field = index.get(str(key))
            try:
                if field is None:
                    raise ValueError("unknown setting")
                if field.get("secret") and str(value).strip() == "":
                    # An empty password box means "keep what is stored", not
                    # "erase the key" -- otherwise a careless click locks the
                    # user out of their provider.
                    continue
                writes = self._coerce_setting(field, value)
            except ValueError as exc:
                errors[str(key)] = str(exc)
                continue
            for group, name, coerced in writes:
                try:
                    config.config.set(group, name, coerced)
                except Exception as exc:  # noqa: BLE001
                    errors[f"{group}.{name}"] = str(exc)
                    continue
                applied[f"{group}.{name}"] = config.config.get(group, name)
                if group == "ui" and name in ("autostart", "autostart_mode"):
                    touched_ui_startup = True
            saved.append(str(key))

        if touched_ui_startup:
            try:
                from .autostart import apply_autostart
                apply_autostart(config.config)
            except Exception as exc:  # noqa: BLE001
                print(f"[sayri-daemon] autostart update error: {exc}")

        if saved:
            # Other UIs (orb, standalone settings window) keep their own copy of
            # these values, so tell them what moved.
            self.server.broadcast("settings_changed", keys=saved, values=applied)

        return {
            "ok": not errors,
            "saved": saved,
            # The coerced values, so the caller can resync its inputs instead of
            # trusting what it sent (a clamped orb size, a "true" that became
            # True, a voice split back into its three keys).
            "values": applied,
            "errors": errors,
            "restart_required": [
                k for k in saved if index.get(k, {}).get("restart")
            ],
        }

    def _cmd_plugin_settings_set(self, params: dict) -> dict:
        """Write one plugin setting into the plugin's own config file.

        Plugins already own their settings (declared in ``manifest.json`` and
        stored next to it), so this writes that same file the plugin host reads
        -- the panel never invents a second source of truth. The change is
        broadcast so a live UI can react without a restart.
        """
        from . import plugin_settings as _ps

        plugin_id = str(params.get("plugin_id", "")).strip()
        key = str(params.get("key", "")).strip()
        if not plugin_id or not key:
            raise ValueError("missing 'plugin_id' or 'key'")
        manifest = _find_manifest(plugin_id)
        if manifest is None:
            raise ValueError(f"plugin not found: {plugin_id}")
        if not _ps.settings_schema(manifest):
            raise ValueError(f"{plugin_id} declares no settings")

        value = params.get("value")
        field = next(
            (f for f in _ps.editable_fields(manifest.get("ui") or {}) if f.get("key") == key),
            None,
        )
        if field is not None:
            if field.get("t") == "check":
                value = bool(value) if isinstance(value, bool) else \
                    str(value).strip().lower() in ("1", "true", "yes", "on")
            elif field.get("t") == "select":
                options = [o.get("value") for o in field.get("options") or []]
                if options and str(value) not in options:
                    raise ValueError(f"{key}: {value!r} is not one of the offered options")
            else:
                value = str(value)

        if not _ps.write_setting(manifest, key, value):
            raise RuntimeError(f"could not write {plugin_id} setting {key}")
        self.server.broadcast("plugin_settings_changed", plugin_id=plugin_id, key=key, value=value)
        return {"ok": True, "plugin_id": plugin_id, "key": key, "value": value}

    def _cmd_asset_download(self, params: dict) -> dict:
        """Fetch a Whisper model or Piper voice in the background.

        These files are hundreds of megabytes, so the request returns
        immediately and progress arrives as ``asset_progress`` broadcasts. A
        second request for the same file is refused rather than started twice.
        """
        kind = str(params.get("kind", "")).strip()
        asset_params = params.get("params")
        if not isinstance(asset_params, dict):
            raise ValueError("missing 'params'")
        from . import downloads

        if kind == "whisper_model":
            size = str(asset_params.get("model_size", ""))
            language = str(asset_params.get("language") or "en")
            if size not in downloads.WHISPER_MODELS:
                raise ValueError(f"unknown whisper model: {size}")
            token = f"whisper_model:{size}:{language}"
            runner = lambda report: downloads.download_whisper_model(size, language, progress=report)
        elif kind == "piper_voice":
            language = str(asset_params.get("language", ""))
            voice = str(asset_params.get("voice", ""))
            quality = str(asset_params.get("quality") or "medium")
            if language not in downloads.PIPER_VOICES:
                raise ValueError(f"unknown voice language: {language}")
            token = f"piper_voice:{language}:{voice}:{quality}"
            runner = lambda report: downloads.download_piper_voice(
                language, voice, quality, progress=report)
        else:
            raise ValueError(f"unknown asset kind: {kind}")

        with self._download_lock:
            if token in self._downloads:
                return {"started": False, "already_running": True, "token": token}
            self._downloads[token] = True

        last = {"percent": -1}

        def progress(fraction: float) -> None:
            # The downloader reports on every chunk, which for a large model is
            # hundreds of messages on a socket every client is reading. Whole
            # percent is plenty for a progress bar.
            percent = int(float(fraction) * 100)
            if percent == last["percent"]:
                return
            last["percent"] = percent
            self.server.broadcast("asset_progress", token=token, kind=kind, percent=percent)

        def worker() -> None:
            try:
                path = runner(progress)
                last["percent"] = 100
                self.server.broadcast("asset_progress", token=token, kind=kind,
                                      percent=100, done=True, path=str(path))
            except Exception as exc:  # noqa: BLE001
                self.server.broadcast("asset_progress", token=token, kind=kind,
                                      done=False, error=str(exc))
            finally:
                with self._download_lock:
                    self._downloads.pop(token, None)

        threading.Thread(target=worker, name="sayri-asset-download", daemon=True).start()
        return {"started": True, "token": token}

    def _cmd_agent_switch(self, params: dict) -> dict:
        from sayri.domain.agent_creator import AgentCreator

        agent_id = str(params.get("agent_id", "")).strip()
        if not agent_id:
            raise ValueError("missing 'agent_id'")
        agent = self.core.set_active_agent(agent_id)
        if agent is None:
            raise ValueError(f"agent not found: {agent_id}")
        return {"agent_id": agent.id, "name": agent.name}

    def _cmd_routines_run(self, params: dict) -> dict:
        from sayri.domain.cron_scheduler import cron_scheduler

        routine_id = str(params.get("routine_id", "")).strip()
        if not routine_id:
            raise ValueError("missing 'routine_id'")
        cron_scheduler.app = self.core
        routine = next(
            (r for r in cron_scheduler.list_routines() if r.id == routine_id), None
        )
        if routine is None:
            raise ValueError(f"routine not found: {routine_id}")
        cron_scheduler._execute_routine(routine)
        return {"scheduled": True, "routine_id": routine_id, "name": routine.name}

    # ------------------------------------------------------- legacy wire
    def _start_legacy_socket(self) -> None:
        """Bind sayri.sock speaking the old gateway protocol, if free."""
        legacy_path = os.path.join(paths.state_dir(), "sayri.sock")
        if os.path.exists(legacy_path):
            try:
                os.unlink(legacy_path)
            except OSError:
                pass
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.bind(legacy_path)
        except OSError as exc:
            print(f"[sayri-daemon] legacy {legacy_path} busy ({exc}); GUI-compat socket skipped")
            sock.close()
            return
        try:
            os.chmod(legacy_path, 0o600)
        except OSError:
            pass
        sock.listen(5)
        sock.settimeout(1.0)
        self._legacy_sock = sock
        self._legacy_running = True
        threading.Thread(target=self._legacy_loop, args=(sock,), daemon=True).start()
        threading.Thread(target=self._legacy_watchdog, args=(legacy_path,), daemon=True).start()
        print(f"[sayri-daemon] legacy gateway socket on {legacy_path}")

    def _legacy_watchdog(self, path: str) -> None:
        """Re-take sayri.sock if a GUI instance released it or died without cleanup."""
        while self._legacy_running:
            time.sleep(2.0)
            if os.path.exists(path):
                alive = False
                try:
                    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    probe.settimeout(0.5)
                    probe.connect(path)
                    probe.sendall(b"sayri-gui-ping\n")
                    try:
                        reply = probe.recv(64)
                        alive = reply.strip().upper().startswith(b"GUI")
                    except (socket.timeout, OSError):
                        alive = False
                    probe.close()
                except OSError:
                    alive = False
                if alive:
                    continue
                try:
                    os.remove(path)
                except OSError:
                    pass
            if self._legacy_sock is not None:
                try:
                    self._legacy_sock.close()
                except OSError:
                    pass
                self._legacy_sock = None
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.bind(path)
                os.chmod(path, 0o600)
                sock.listen(5)
                sock.settimeout(1.0)
                self._legacy_sock = sock
                threading.Thread(target=self._legacy_loop, args=(sock,), daemon=True).start()
                print(f"[sayri-daemon] legacy gateway socket rebound on {path}")
            except OSError:
                try:
                    sock.close()
                except OSError:
                    pass

    def _legacy_loop(self, sock: socket.socket) -> None:
        while self._legacy_running:
            try:
                conn, _ = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                conn.settimeout(120.0)
                raw = conn.recv(8192).decode("utf-8", errors="replace").strip()
                self._handle_legacy_message(conn, raw)
            except Exception as exc:  # noqa: BLE001
                print(f"[sayri-daemon] legacy connection error: {exc}")
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def _handle_legacy_message(self, conn: socket.socket, raw: str) -> None:
        if raw.startswith("{"):
            msg = json.loads(raw)
            mtype = msg.get("type")
            if mtype in ("INCOMING_MSG", "remote_message"):
                result = self.core.process_remote_message(
                    text=str(msg.get("text", "")),
                    author=str(msg.get("author", "User")),
                    target_agent_id=str(msg.get("target_agent", "default")),
                    sandbox_level=msg.get("sandbox_level"),
                    instance_id=str(msg.get("instance_id", "default")),
                    session_id=msg.get("session_id"),
                )
                reply = json.dumps({"ok": True, "event": "done", "text": result})
                conn.sendall((reply + "\n").encode("utf-8"))
                return
            if mtype in ("ATTACH_IMAGE", "attach"):
                img_path = msg.get("image_path") or msg.get("path")
                if img_path:
                    self.server.broadcast("attach_image", path=str(img_path))
                conn.sendall(b"OK\n")
                return
        elif raw == "sayri-gui-ping":
            conn.sendall(b"DAEMON\n")
            return
        elif raw == "toggle":
            if self.server.client_count() > 0:
                self.server.broadcast("toggle")
            else:
                import subprocess
                import sys
                env = dict(os.environ)
                lib_path = os.path.dirname(os.path.dirname(__file__))
                env["PYTHONPATH"] = lib_path + (":" + env["PYTHONPATH"] if "PYTHONPATH" in env else "")
                subprocess.Popen([sys.executable, "-m", "sayri", "--toggle"], env=env)
        elif raw == "show":
            self.server.broadcast("show")
        elif raw == "hide":
            self.server.broadcast("hide")
        elif raw == "listen":
            self.core.start_listening()
        elif raw == "settings":
            self.server.broadcast("settings_requested")
        elif raw == "quit":
            threading.Thread(target=self.stop, daemon=True).start()
        conn.sendall(b"OK\n")

    # ------------------------------------------------------------- auto
    def _start_services(self) -> None:
        self._start_legacy_socket()

        if not getattr(self, "_gateways_started", False):
            self._gateways_started = True
            try:
                gateway_supervisor.auto_start_all()
            except Exception as exc:  # noqa: BLE001
                print(f"[sayri-daemon] gateway auto-start notice: {exc}")
            try:
                from sayri import plugin_service
                plugin_service.auto_start_services()
            except Exception as exc:  # noqa: BLE001
                print(f"[sayri-daemon] plugin service auto-start notice: {exc}")

        try:
            cron_scheduler.app = self.core
            cron_scheduler.start()
        except Exception as exc:  # noqa: BLE001
            print(f"[sayri-daemon] cron scheduler notice: {exc}")

    # ---------------------------------------------------------------- run
    def start(self, start_services: bool = True) -> bool:
        if not self.server.start():
            print("[sayri-daemon] could not start IPC socket; is another daemon running?")
            return False
        print(f"[sayri-daemon] listening on {socket_path()} (version {__version__})")
        if start_services:
            self._start_services()
        return True

    def serve_forever(self) -> None:
        """Block the calling thread while the IPC server runs."""
        try:
            while self.server._running:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self) -> None:
        self._legacy_running = False
        if self._legacy_sock is not None:
            try:
                self._legacy_sock.close()
            except OSError:
                pass
            self._legacy_sock = None
        try:
            cron_scheduler.stop()
        except Exception:  # noqa: BLE001
            pass
        self.core.shutdown()
        self.server.broadcast("shutdown")
        self.server.stop()
        legacy_path = os.path.join(paths.state_dir(), "sayri.sock")
        try:
            if os.path.exists(legacy_path):
                os.unlink(legacy_path)
        except OSError:
            pass


def main() -> int:
    daemon = SayriDaemon()
    if not daemon.start():
        return 1
    daemon.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())