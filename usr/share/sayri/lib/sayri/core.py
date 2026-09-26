"""SayriCore: the headless Sayri brain (no GTK / display required).

This is the "nucleus" of Sayri: it owns the voice loop (STT session), TTS,
the ReAct agent engine, sandbox, storage, wake-word handling, sessions and
remote/gateway message processing — but knows nothing about windows, orbs or
widgets.

Any UI (the GTK orb, a Clippy/Bonzi-style avatar, the `sayri` CLI, a web
bubble...) attaches through the ``ui`` sink: an object with no-op callbacks
that receives every state change / transcript / delta / tool event. The
daemon (``sayri.daemon``) wires this sink to the IPC broadcast so remote
clients consume the exact same events.
"""

from __future__ import annotations

import os
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import __version__, config, llm, paths, sound, stt as stt_mod, texts, tts as tts_mod
from sayri.domain.models import AgentProfile, SandboxLevel
from sayri.domain.agent_engine import AgentEngine
from sayri.domain.agent_creator import AgentCreator
from sayri.domain.triggers import TriggerEngine
from sayri.adapters.sandbox.executor import SandboxExecutor
from sayri.adapters.storage.sqlite_sessions import SQLiteSessionRepository

HISTORY_MAX = 10


class CoreUI:
    """Default no-op UI sink. Subclass or monkey-patch to consume events."""

    def on_ready(self, info: dict) -> None:
        pass

    def on_state(self, state: str) -> None:
        pass

    def on_audio_level(self, level: float) -> None:
        pass

    def on_mic(self, active: bool) -> None:
        pass

    def on_busy(self, active: bool) -> None:
        pass

    def on_partial(self, text: str) -> None:
        pass

    def on_user(self, text: str) -> None:
        pass

    def on_assistant_delta(self, delta: str) -> None:
        pass

    def on_assistant_done(self, text: str) -> None:
        pass

    def on_tool_start(self, command: str) -> None:
        pass

    def on_tool_finish(self, command: str, output: str, exit_code: int) -> None:
        pass

    def on_hint(self, text: str) -> None:
        pass

    def on_error(self, message: str) -> None:
        pass

    def on_speaking(self, active: bool) -> None:
        pass

    def on_show(self) -> None:
        pass

    def on_hide(self) -> None:
        pass

    def on_conversation_started(self, session_id: str) -> None:
        pass

    def on_shutdown(self) -> None:
        pass


class SayriCore:
    """Headless Sayri engine."""

    def __init__(self, ui: Optional[CoreUI] = None, cfg=None) -> None:
        self.cfg = cfg or config.config
        self.ui = ui or CoreUI()

        self.stt = stt_mod.STTEngine(self.cfg)
        self.tts = tts_mod.TTSEngine(self.cfg)

        self.state = "idle"
        self.armed = False
        self._busy = False
        self._mic_on = False
        self._setup_needed = False
        self.session = None
        self._assistant_text = ""
        self._current_query_id = 0
        self._last_assistant_reply = ""
        self.history: list[tuple[str, str]] = []

        # Whether a UI is currently "open" (visible + focused). The daemon
        # flips this as clients connect/disconnect so wake-word gating matches
        # the desktop behaviour of the GUI.
        self.ui_visible = True

        # One-shot capture used by the CLI `listen` command.
        self._capture = None  # (callback, event)

        self.storage = SQLiteSessionRepository()
        self.sandbox = SandboxExecutor()
        self.engine = AgentEngine(self.storage, self.sandbox)
        self.triggers = TriggerEngine()
        self.active_agent: AgentProfile = AgentCreator.get_agent("default") or AgentProfile(
            id="default",
            name="Main Sayri",
            description="Operating system assistant for Pulsar OS",
            system_prompt="You are Sayri, the intelligent assistant of Pulsar OS.",
        )
        self.active_session_id = self.storage.create_session(agent_id=self.active_agent.id).id

        self.cfg.on_change(self._on_config_change)
        self.ui.on_ready(self.status_info())

    # ─────────────────────────────────────────────────────────── listening

    def _start_session(self) -> None:
        if self.session and self.session.is_running():
            return
        if not self.stt.ready:
            return
        self.session = self.stt.create_session(
            on_partial=self._on_partial,
            on_utterance=self._on_utterance,
            on_level=self._on_level,
            on_speech_start=lambda: self._on_speech_start(),
            on_transcribe_start=lambda: self._on_transcribe_start(),
        )
        if self.session.start():
            self.set_state("listening")
            self._set_mic(True)
        else:
            self.session = None
            self.ui.on_error("Could not initialize microphone")

    def _stop_session(self) -> None:
        if self.session:
            self.session.stop()
            self.session = None
        self._set_mic(False)

    def start_listening(self) -> None:
        self._busy = False
        mode = self.cfg.get_string("stt", "mode")
        if mode == "disabled" or not self.stt.ready:
            self.armed = False
            self.set_state("idle")
            self._set_mic(False)
            return
        self.armed = True
        if not (self.session and self.session.is_running()):
            self._start_session()
        self.set_state("activated")
        self.ui.on_hint("Listening…")

    def stop_listening(self) -> None:
        self.armed = False
        self._stop_session()
        self.tts.cancel()
        sound.stop_all()
        self._set_busy(False)
        self._assistant_text = ""
        self._on_level(0.0)
        self.ui.on_speaking(False)
        self.set_state("idle")
        self._set_mic(False)

    def toggle_listening(self) -> None:
        """Toggle microphone listening state on/off."""
        if self._busy or self.tts.is_speaking or self.state in ("speaking", "thinking"):
            self.stop_listening()
            self.ui.on_hint("Microphone off.")
            return
        if self.armed or self.state in ("listening", "activated") or (self.session and self.session.is_running()):
            self.stop_listening()
            self.ui.on_hint("Microphone off.")
        else:
            self.start_listening()

    # ────────────────────────────────────────────────────────────── states
    def set_state(self, state: str) -> None:
        self.state = state
        if state == "activated":
            sound.play("activate")
            sound.stop_loop()
        elif state == "thinking":
            sound.start_loop("thinking")
        elif state == "speaking":
            sound.stop_loop()
        elif state in ("idle", "listening"):
            sound.stop_loop()
        self.ui.on_state(state)

    def interrupt(self) -> None:
        """Halt any active speech / generation / listening."""
        self._current_query_id += 1
        self.tts.cancel()
        sound.stop_all()
        self._set_busy(False)
        self._assistant_text = ""
        self._on_level(0.0)
        self.ui.on_speaking(False)
        self.stop_listening()
        mode = self.cfg.get_string("stt", "mode")
        if mode in ("always", "wakeword") and self.stt.ready:
            self._start_session()
            self.set_state("idle")

    def _set_mic(self, active: bool) -> None:
        self._mic_on = active
        self.ui.on_mic(active)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.ui.on_busy(busy)

    # ────────────────────────────────────────────────────── STT callbacks
    def _on_speech_start(self) -> None:
        if self._busy or not self.armed:
            return
        self.set_state("listening")
        self.ui.on_hint("Listening…")

    def _on_transcribe_start(self) -> None:
        if self._busy or not self.armed:
            return
        self.set_state("thinking")
        self.ui.on_hint("Transcribing…")

    def _on_partial(self, text: str) -> None:
        if self._busy or not self.armed:
            return
        if not self._capture:
            self.ui.on_partial(f"“{text}…”")

    def _on_level(self, level: float) -> None:
        self.ui.on_audio_level(max(0.0, min(1.0, float(level))))

    def _on_utterance(self, text: str) -> None:
        if self._capture:
            cb, evt = self._capture
            cb(text)
            evt.set()
            return

        if not text.strip():
            self.ui.on_partial("")
            mode = self.cfg.get_string("stt", "mode")
            if mode == "manual" or not self.armed:
                self.set_state("idle")
                self.ui.on_hint("Ask me anything…")
            return

        if self._busy:
            self.ui.on_hint("Please wait for the response to finish.")
            return

        # Ignore TTS self-echo from the last assistant response.
        if self._last_assistant_reply:
            prev_clean = re.sub(r"[^\w\s]", "", self._last_assistant_reply.lower())
            curr_clean = re.sub(r"[^\w\s]", "", text.lower())
            if len(curr_clean) > 3 and (curr_clean in prev_clean or (len(prev_clean) > 3 and prev_clean in curr_clean)):
                return

        mode = self.cfg.get_string("stt", "mode")
        matched, remainder = self._match_and_extract_wake_word(text)

        if mode == "wakeword" and not self.armed and not self.ui_visible:
            if matched:
                self.ui_visible = True
                self.ui.on_show()
                if remainder and len(remainder) > 1:
                    self.armed = False
                    self.send_text(remainder)
                else:
                    self.armed = True
                    self.start_listening()
                    self.set_state("activated")
                    self.ui.on_hint("Listening…")
            return

        # User spoke ONLY the wake word without a question: arm and wait.
        if matched and (not remainder or len(remainder) <= 1):
            if not self.ui_visible:
                self.ui_visible = True
                self.ui.on_show()
            self.armed = True
            self.start_listening()
            self.set_state("activated")
            self.ui.on_hint("Listening…")
            return

        if not self.ui_visible:
            self.ui_visible = True
            self.ui.on_show()

        query = remainder if (matched and remainder and len(remainder) > 1) else text
        self.armed = False
        self.send_text(query)
        if mode == "manual":
            self._stop_session()
            self.set_state("idle")

    def _match_and_extract_wake_word(self, text: str) -> tuple[bool, str]:
        raw = text.strip()

        cfg_ww = self.cfg.get_string("stt", "wake_word").strip().lower()
        candidates: set[str] = set()
        if cfg_ww:
            for item in cfg_ww.split(","):
                clean = item.strip().lower()
                if clean:
                    candidates.add(clean)

        candidates.update([
            "hey sayri", "oye sayri", "sayri", "hola sayri", "ok sayri",
            "hey sairi", "oye sairi", "sairi", "hola sairi", "ok sairi",
            "hey sari", "oye sari", "sari", "hola sari", "ok sari",
            "hey seiri", "oye seiri", "seiri", "hola seiri",
            "hey seyri", "oye seyri", "seyri", "hola seyri",
            "hey salir", "oye salir", "hola salir", "salir",
            "hey sabri", "oye sabri", "hola sabri", "sabri",
            "hey siri", "oye siri", "siri", "hola siri", "ok siri",
            "hey sara", "oye sara", "sara", "hola sara",
            "hey zairi", "oye zairi", "zairi",
            "hey saydy", "oye saydy", "saydy",
            "hey say", "oye say", "hola say",
            "hello sayri", "hi sayri", "okay sayri",
            "hello sairi", "hi sairi",
            "hello siri", "hi siri",
        ])

        regex_parts = []
        for w in sorted(candidates, key=len, reverse=True):
            parts = [re.escape(p) for p in w.split()]
            regex_parts.append(r"\s+".join(parts))

        full_regex = re.compile(r"\b(?:" + "|".join(regex_parts) + r")\b", re.IGNORECASE)
        cleaned = re.sub(r"[,;:\.¿\?¡!\-_]", " ", raw)
        m = full_regex.search(cleaned)
        if m:
            end_pos = m.end()
            remainder = raw[end_pos:].strip(" \t\n\r,:;¿?¡!.")
            matched_word = m.group(0).strip()
            return True, remainder
        return False, ""

    # ─────────────────────────────────────────────────────────────── LLM
    def send_text(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        self._current_query_id += 1
        query_id = self._current_query_id
        self._stop_session()
        self.tts.cancel()
        self.armed = False
        self._assistant_text = ""
        self._set_busy(True)
        self.set_state("thinking")
        self.ui.on_user(text)

        self.engine.process_query(
            session_id=self.active_session_id,
            user_text=text,
            profile=self.active_agent,
            cfg=self.cfg,
            on_delta=lambda d: self._on_delta(d, query_id),
            on_done=lambda full: self._finish_engine_reply(full, query_id),
            on_tool_start=lambda cmd: self.ui.on_tool_start(cmd),
            on_tool_finish=lambda cmd, out, code: self.ui.on_tool_finish(cmd, out, code),
            on_error=lambda e: self._on_error(e, query_id),
        )

    def _on_delta(self, delta: str, query_id: int = 0) -> None:
        if query_id != self._current_query_id:
            return
        if isinstance(delta, bytes):
            delta = delta.decode("utf-8", errors="replace")
        elif not isinstance(delta, str):
            delta = str(delta) if delta is not None else ""
        self._assistant_text += delta
        self.ui.on_assistant_delta(delta)

    def _finish_engine_reply(self, full: str, query_id: int = 0) -> None:
        if query_id != self._current_query_id:
            return
        if full:
            self._assistant_text = full
            self.ui.on_assistant_done(full)
            self.history.append(("assistant", full))
            self.history = self.history[-HISTORY_MAX * 2:]
        self._finish_reply(full, query_id)

    def _finish_reply(self, full: str, query_id: int = 0) -> None:
        if query_id != self._current_query_id:
            return
        self._last_assistant_reply = full
        spoken = texts.markdown_to_plain_speech(full)
        if self.cfg.get_bool("tts", "enabled") and spoken and self.tts.ready:
            self._stop_session()
            self.set_state("speaking")
            self.tts.speak_async(
                spoken,
                on_level=lambda lvl: self._on_level(lvl),
                on_end=lambda: self._after_reply(),
                on_error=lambda e: self._on_error(e, query_id),
            )
        else:
            self._after_reply()

    def _after_reply(self) -> None:
        self._set_busy(False)
        self._on_level(0.0)
        self._assistant_text = ""
        self.ui.on_speaking(False)
        mode = self.cfg.get_string("stt", "mode")
        if mode in ("always", "wakeword"):
            self._start_session()
            if mode == "wakeword":
                self.set_state("idle")
            else:
                self.set_state("listening")
        else:
            self.set_state("idle")

    def _on_error(self, exc: Exception, query_id: int = 0) -> None:
        if query_id != self._current_query_id:
            return
        self.ui.on_error(f"Provider error: {exc}")
        self._set_busy(False)
        self.ui.on_speaking(False)
        self._after_reply()

    # ───────────────────────────────────────────────────────── sessions
    def new_conversation(self) -> None:
        self.tts.cancel()
        sound.stop_all()
        self._current_query_id += 1
        self.active_session_id = self.storage.create_session(agent_id=self.active_agent.id).id
        self._set_busy(False)
        self._assistant_text = ""
        self.history = []
        self.ui.on_conversation_started(self.active_session_id)
        self.ui.on_hint("New conversation started.")
        sound.play("activate")

    def switch_session(self, session_id: str) -> None:
        self.tts.cancel()
        sound.stop_all()
        self._current_query_id += 1
        sess = self.storage.get_session(session_id)
        if not sess:
            return
        self.active_session_id = sess.id
        agent = AgentCreator.get_agent(sess.agent_id) or self.active_agent or AgentCreator.get_agent("default")
        if agent:
            self.active_agent = agent
        self.history = []
        for m in sess.messages:
            self.history.append((m.role, m.content))
        self._set_busy(False)
        self._assistant_text = ""
        sound.play("activate")
        self.ui.on_hint(f"Conversation: {sess.title[:24]}…")

    def set_active_agent(self, agent_id: str) -> Optional[Any]:
        agent = AgentCreator.get_agent(agent_id)
        if not agent:
            return None
        self.active_agent = agent
        self.ui.on_hint(f"Agent: {agent.name}")
        return agent

    # ────────────────────────────────────────────── remote / gateways
    def process_remote_message(
        self,
        text: str,
        author: str,
        target_agent_id: Optional[str] = None,
        sandbox_level: Optional[str] = None,
        instance_id: Optional[str] = None,
        session_id: Optional[str] = None,
        on_event: Optional[Callable[[dict], None]] = None,
    ) -> str:
        """Process an incoming message from a channel gateway (Telegram/Discord)
        or scheduled routine. Returns the final assistant text."""
        import copy

        done_event = threading.Event()
        result_holder: dict = {"text": "", "error": None}

        def _send(event_dict: dict) -> None:
            if on_event:
                try:
                    on_event(event_dict)
                except Exception:
                    pass

        def _on_delta(d: str):
            result_holder["text"] += d
            _send({"event": "delta", "delta": d})

        def _on_done(full: str):
            if full:
                result_holder["text"] = full
            _send({"event": "done", "text": full or result_holder["text"]})
            done_event.set()

        def _on_tool_start(cmd: str):
            _send({"event": "tool_start", "tool": "bash", "command": cmd})

        def _on_tool_finish(cmd: str, out: str, code: int):
            _send({"event": "tool_finish", "tool": "bash", "command": cmd, "output": out, "exit_code": code})

        def _on_error(exc: Exception):
            result_holder["error"] = str(exc)
            _send({"event": "error", "error": str(exc)})
            done_event.set()

        agent_profile = None
        if target_agent_id:
            agent_profile = AgentCreator.get_agent(target_agent_id)
        if not agent_profile:
            agent_profile = self.active_agent or AgentCreator.get_agent("default")

        if sandbox_level:
            try:
                agent_profile = copy.deepcopy(agent_profile)
                lvl_enum = getattr(SandboxLevel, sandbox_level, None) or SandboxLevel(sandbox_level)
                agent_profile.sandbox.level = lvl_enum
            except Exception:
                pass

        if not session_id:
            inst_tag = f"-{instance_id}" if instance_id else ""
            session_id = f"remote{inst_tag}-{agent_profile.id}-{author.replace('@', '')}"

        existing_session = self.storage.get_session(session_id)
        if not existing_session:
            gw_label = instance_id.replace("sayri-gateway-", "").capitalize() if instance_id else "Gateway"
            self.storage.create_session(
                agent_id=agent_profile.id,
                title=f"{author} ({gw_label})",
                session_id=session_id,
            )

        _send({"event": "start", "session_id": session_id})

        try:
            self.engine.process_query(
                session_id=session_id,
                user_text=text,
                profile=agent_profile,
                cfg=self.cfg,
                on_delta=_on_delta,
                on_done=_on_done,
                on_tool_start=_on_tool_start,
                on_tool_finish=_on_tool_finish,
                on_error=_on_error,
            )
            done_event.wait(timeout=45.0)

            final_reply = result_holder["text"].strip()
            if not final_reply and result_holder["error"]:
                final_reply = f"⚠️ Sayri Error: {result_holder['error']}"
            if not final_reply:
                final_reply = f"Hi {author}, I received your message: '{text}'."

            try:
                sess_check = self.storage.get_session(session_id)
                if not sess_check or not sess_check.messages or sess_check.messages[-1].role != "assistant":
                    from sayri.domain.models import Message
                    self.storage.add_message(session_id, Message(role="assistant", content=final_reply))
            except Exception:
                pass

            if not done_event.is_set():
                _send({"event": "done", "text": final_reply})
            return final_reply
        except Exception as exc:
            from sayri.domain.models import Message
            err_msg = f"⚠️ Error processing message: {exc}"
            try:
                self.storage.add_message(session_id, Message(role="assistant", content=err_msg))
            except Exception:
                pass
            _send({"event": "error", "error": str(exc)})
            return err_msg

    # ─────────────────────────────────────────────────────── one-shot
    def listen_once(self, timeout: float = 60.0) -> str:
        """One-shot listening session: returns the next transcribed sentence."""
        evt = threading.Event()
        captured: list = []

        def _cb(text: str) -> None:
            captured.append(text)
            evt.set()

        self._capture = (_cb, evt)
        try:
            self.start_listening()
            evt.wait(timeout)
            self.stop_listening()
            return captured[0] if captured else ""
        finally:
            self._capture = None

    def ask(self, text: str, timeout: float = 120.0) -> dict:
        """Blocking ask: sends text to the engine and waits for the full reply.

        Returns ``{"text": ..., "error": None|str, "session_id": ...}``.
        """
        done = threading.Event()
        out: dict = {"text": "", "error": None}

        class _Sink:
            def __init__(self, inner):
                self.inner = inner
            def __getattr__(self, name):
                return getattr(self.inner, name)
            def on_assistant_done(self, text):  # noqa: D102
                out["text"] = text
                done.set()
            def on_error(self, message):  # noqa: D102
                out["error"] = message
                done.set()

        old_ui = self.ui
        self.ui = _Sink(old_ui)
        try:
            self.send_text(text)
            done.wait(timeout)
            out["session_id"] = self.active_session_id
            return out
        finally:
            self.ui = old_ui

    # ───────────────────────────────────────────────── config handling
    def _apply_mode(self) -> None:
        self._stop_session()
        mode = self.cfg.get_string("stt", "mode")
        if mode in ("always", "wakeword"):
            self._start_session()
        else:
            self.set_state("idle")

    def _on_config_change(self, group: str, key: str, _value) -> None:
        if group == "stt" and key == "mode":
            self._apply_mode()

    # ─────────────────────────────────────────────────────────── status
    def status_info(self) -> dict:
        """Serialisable snapshot used by the IPC `status` command."""
        return {
            "version": getattr(config, "SAYRI_VERSION", None) or __version__,
            "state": self.state,
            "armed": self.armed,
            "busy": self._busy,
            "mic_on": self._mic_on,
            "ui_visible": self.ui_visible,
            "stt_ready": self.stt.ready,
            "stt_mode": self.cfg.get_string("stt", "mode"),
            "stt_missing": self.stt.missing(),
            "tts_ready": self.tts.ready,
            "tts_missing": self.tts.missing(),
            "provider": {
                "base_url": self.cfg.get_string("provider", "base_url"),
                "model": self.cfg.get_string("provider", "model"),
                "api_key_set": bool(self.cfg.get_string("provider", "api_key").strip()),
            },
            "session_id": self.active_session_id,
            "agent": getattr(self.active_agent, "id", "default"),
            "config_dir": paths.config_dir(),
            "state_dir": paths.state_dir(),
        }

    # ───────────────────────────────────────────────────────── shutdown
    def shutdown(self) -> None:
        self.tts.cancel()
        sound.stop_all()
        self._stop_session()
        self.ui.on_shutdown()