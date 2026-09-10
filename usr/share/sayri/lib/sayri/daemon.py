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
from .domain.cron_scheduler import cron_scheduler
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
        s.register("gateway_list", lambda p, c: gateway_supervisor.list_instances())
        s.register("gateway_start", lambda p, c: self._cmd_gateway_start(p))
        s.register("gateway_stop", lambda p, c: self._cmd_gateway_stop(p))
        s.register("gateway_delete", lambda p, c: self._cmd_gateway_delete(p))
        s.register("gateway_save", lambda p, c: self._cmd_gateway_save(p))
        s.register("agents_list", lambda p, c: [_agent_to_dict(a) for a in AgentCreator.list_agents()])
        s.register("agent_switch", lambda p, c: self._cmd_agent_switch(p))
        s.register("routines_run", lambda p, c: self._cmd_routines_run(p))
        s.register("sessions_list", lambda p, c: self._cmd_sessions_list(p))
        s.register("routines_list", lambda p, c: [r.to_dict() for r in cron_scheduler.list_routines()])
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
        return [
            {"group": g, "key": k, "value": config.config.get(g, k)}
            for g in config.DEFAULTS
            for k in config.DEFAULTS[g]
        ]

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
        rows = []
        for p in gateway_supervisor.list_installed_plugins():
            rows.append({
                "id": p.get("id"),
                "name": p.get("name"),
                "description": p.get("description", ""),
                "version": p.get("version"),
                "auth_mode": p.get("auth_mode"),
                "required_secrets": p.get("required_secrets", []),
                "path": str(p["path"]) if p.get("path") else "",
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
                reply = json.dumps({"ok": True, "text": result})
                conn.sendall((reply + "\n").encode("utf-8"))
                return
            if mtype in ("ATTACH_IMAGE", "attach"):
                img_path = msg.get("image_path") or msg.get("path")
                if img_path:
                    self.server.broadcast("attach_image", path=str(img_path))
                conn.sendall(b"OK\n")
                return
        elif raw == "toggle":
            self.core.toggle_listening()
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