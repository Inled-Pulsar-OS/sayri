"""Sayri application (GTK4): wires the transparent overlay (orb + cajita),
whisper.cpp STT, OpenAI-compatible LLM and Piper TTS together.

States: idle -> listening -> (activated) -> thinking -> speaking -> listening

A single layer-shell window contains both the Siri orb and the Apple-
intelligence cajita side by side, pinned to the top-right of the monitor.
Clicking the orb toggles the microphone; the cajita handles text input,
reply display and settings/quit buttons.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gio, GLib, Gtk  # noqa: E402

from . import (  # noqa: E402
    blur_exclusion,
    config,
    llm,
    overlay as overlay_mod,
    paths,
    settings_window,
    sound,
    stt as stt_mod,
    tts as tts_mod,
)
from sayri.domain.models import AgentProfile, SandboxLevel
from sayri.domain.agent_engine import AgentEngine
from sayri.domain.agent_creator import AgentCreator
from sayri.domain.triggers import TriggerEngine
from sayri.adapters.sandbox.executor import SandboxExecutor
from sayri.adapters.storage.sqlite_sessions import SQLiteSessionRepository

APP_ID = "es.inled.sayri"
HISTORY_MAX = 10
AUTOSTART_SRC = "/etc/xdg/autostart/sayri.desktop"


def _detect_distro() -> str:
    if os.path.exists("/etc/arch-release"):
        return "Arch Linux"
    elif os.path.exists("/etc/debian_version"):
        return "Debian"
    try:
        with open("/etc/os-release") as f:
            content = f.read().lower()
            if "arch" in content:
                return "Arch Linux"
            elif "debian" in content or "ubuntu" in content:
                return "Debian"
    except Exception:
        pass
    return "Linux"


def markdown_to_plain_speech(text: str) -> str:
    """Clean markdown syntax into natural, clean spoken text for Piper TTS."""
    if not text:
        return ""
    import re
    # Remove code blocks completely so TTS doesn't dictate raw scripts
    cleaned = re.sub(r"```(?:[a-zA-Z0-9_\-]+)?\n?(.*?)\n?```", "", text, flags=re.DOTALL)
    # Convert inline code `foo` -> foo
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    # Convert bold / italic
    cleaned = re.sub(r"\*\*([^\*]+)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"__([^_]+)__", r"\1", cleaned)
    cleaned = re.sub(r"(?<!\*)\*(?!\*)([^\*\n]+?)(?<!\*)\*(?!\*)", r"\1", cleaned)
    cleaned = re.sub(r"(?<!\w)_([^\_\n]+?)_(?!\w)", r"\1", cleaned)
    # Convert headers # Header -> Header
    cleaned = re.sub(r"^(?:#{1,6})\s+(.+)$", r"\1", cleaned, flags=re.MULTILINE)
    # Convert list bullets - / *
    cleaned = re.sub(r"^[\*\-]\s+", "", cleaned, flags=re.MULTILINE)
    # Links [text](url) -> text
    cleaned = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", cleaned)
    # Strip URLs
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    # Remove leftover markdown symbols
    cleaned = re.sub(r"[\*\_~`#><]", "", cleaned)
    # Strip emojis and pictographs so TTS speaks pure words without reciting emoji names
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"  # emoticons
        "\U0001F300-\U0001F5FF"  # symbols & pictographs
        "\U0001F680-\U0001F6FF"  # transport & map symbols
        "\U0001F1E0-\U0001F1FF"  # flags
        "\U0001F900-\U0001F9FF"  # supplemental symbols & pictographs
        "\U0001FA00-\U0001FAFF"  # chess, symbols extended
        "\U00002700-\U000027BF"  # dingbats
        "\U00002600-\U000026FF"  # misc symbols
        "\U00002B50"              # star
        "\U0000200D"              # zero-width joiner
        "\U0000FE0F"              # variation selector-16
        "]+",
        flags=re.UNICODE,
    )
    cleaned = emoji_pattern.sub("", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{2,}", "\n", cleaned).strip()
    return cleaned


def _get_effective_system_prompt(cfg) -> str:
    agent_mode = cfg.get_bool("provider", "agent_mode")
    base_prompt = cfg.get_string("provider", "system_prompt")
    if not agent_mode:
        return base_prompt
    distro = _detect_distro()
    import getpass
    username = getpass.getuser()
    mem_file = paths.memory_file()
    user_f = paths.user_file()
    skills_d = paths.skills_dir()
    return (
        f"You are Sayri, the intelligent voice assistant and autonomous agent integrated into Pulsar OS (based on {distro}).\n"
        f"The current system user is '{username}'. Their profile and data are in `{user_f}`.\n"
        f"Your long-term memory of memories and preferences is in `{mem_file}` (you can read it or add notes with bash).\n"
        f"Your installed ClawHub/OpenClaw skills are in `{skills_d}`. You can list your skills with `ls {skills_d}` and read their guides with `cat {skills_d}/<skill>/SKILL.md`.\n"
        "You can search for or download new skills from ClawHub (https://clawhub.ai) using the `sayri-skills install <skill-name>` or `sayri-skills search <query>` command.\n"
        "To capture the screen, you can use the `sayri screenshot [destination_path]` command or GNOME portals.\n"
        "For tasks requiring administrator/root privileges (installing or updating system packages with pacman, modifying /etc, system systemctl), always use `pkexec <command>`.\n"
        "The system will intercept elevation and request graphical confirmation from the user via Polkit before proceeding.\n\n"
        "AUTONOMOUS AGENTIC FLOW:\n"
        "When the user asks you a task, to open applications, change settings or query data, you MUST use EXACTLY this format to issue commands:\n\n"
        "```bash\n"
        "<bash command>\n"
        "```\n\n"
        "CRITICAL RULES (do not break them):\n"
        "1. ALWAYS use markdown code blocks with 'bash' after the triple backticks: \"```bash\".\n"
        "2. NEVER use XML tags such as <bash>, <sh>, <tool>, etc.\n"
        "3. A bash block contains ONLY ONE command. Do not put multiple commands separated by semicolons.\n"
        "4. First reply with a short sentence describing what you are going to do, THEN the bash block.\n"
        "5. If you need to run several steps, do it in separate turns: one block per turn.\n\n"
        "You will run commands interactively. If a command fails or you need more steps, you will receive the exit code and error in the next turn and you can issue new bash commands to correct the error until you achieve the goal.\n"
        "Always respond naturally, concisely and pleasantly (1 to 3 spoken sentences for voice)."
    )


class SayriApp(Gtk.Application):
    def __init__(self, is_autostart: bool = False) -> None:
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.cfg = config.config
        self.is_autostart = is_autostart

        self.stt = stt_mod.STTEngine(self.cfg)
        self.tts = tts_mod.TTSEngine(self.cfg)

        self.overlay: overlay_mod.SayriOverlay | None = None
        self.settings_win = None

        self.state = "idle"
        self.armed = False
        self._busy = False
        self._mic_on = False
        self._setup_needed = False
        self.session = None
        self._assistant_text = ""
        self._current_query_id = 0
        self.history: list[tuple[str, str]] = []

        self.cfg.on_change(self._on_config_change)
        self.connect("activate", self._on_activate)
        self.connect("shutdown", self._on_shutdown)

        # Hexagonal Domain & Adapters
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

        self.hold()

    def new_conversation(self) -> None:
        """Starts a fresh conversation session."""
        self.tts.cancel()
        sound.stop_all()
        self._current_query_id += 1
        self.active_session_id = self.storage.create_session(agent_id=self.active_agent.id).id
        self._set_busy(False)
        self._assistant_text = ""
        self.history = []
        if self.overlay:
            self.overlay.cajita.set_speaking(False)
            self.overlay.clear()
            self.overlay.cajita.clear()
            self.overlay.cajita.entry.set_text("")
            self.overlay.cajita.card_overlay.set_visible(False)
            self.overlay.cajita.update_agent_badge(self.active_agent.name, self.active_agent.sandbox.level.value)
        self._msg("hint", "✨ New conversation started.")
        sound.play("activate")
        print(f"[Sayri] ✨ Started new conversation session: {self.active_session_id}")

    def switch_session(self, session_id: str) -> None:
        """Loads and switches to an existing conversation session."""
        self.tts.cancel()
        sound.stop_all()
        self._current_query_id += 1
        sess = self.storage.get_session(session_id)
        if not sess:
            return
        self.active_session_id = sess.id
        self.active_agent = AgentCreator.get_agent(sess.agent_id) or self.active_agent
        self.history = []
        for m in sess.messages:
            self.history.append((m.role, m.content))
        self._set_busy(False)
        self._assistant_text = ""
        if self.overlay:
            self.overlay.cajita.set_speaking(False)
            self.overlay.clear()
            self.overlay.cajita.update_agent_badge(self.active_agent.name, self.active_agent.sandbox.level.value)
            self.overlay.cajita.render_session_history(sess.title or "Conversation", sess.messages)
        self._msg("hint", f"Conversation: {sess.title[:24]}…")
        sound.play("activate")
        print(f"[Sayri] Switched to session: {sess.id} ({sess.title})")

    # ── public state helpers
    @property
    def busy(self) -> bool:
        return self._busy

    def listening_now(self) -> bool:
        return bool(self._mic_on)

    # ── startup
    def _on_activate(self, _app) -> None:
        paths.ensure_dirs()
        blur_exclusion.apply_blur_exclusion()
        self.apply_autostart()
        if self.overlay is None:
            self._build_ui()

        # Check if first run or no AI provider configured
        has_api_key = bool(self.cfg.get_string("provider", "api_key").strip())
        is_first_run = getattr(self.cfg, "_is_first_run", False) or not has_api_key
        self._setup_needed = is_first_run or not has_api_key

        if is_first_run or not has_api_key:
            self._show_setup_prompt()
            self.overlay.show()
        else:
            if not self.is_autostart:
                self.overlay.show()

        mode = self.cfg.get_string("stt", "mode")
        if mode in ("always", "wakeword"):
            self._start_session()
        self.refresh_status()
        self._start_ipc_server()
        self._launch_indicator()

        # Auto-start installed gateways with configured secrets
        try:
            from sayri.gateway_supervisor import gateway_supervisor
            gateway_supervisor.auto_start_all()
        except Exception as e:
            print(f"[Sayri] Gateway supervisor auto-start notice: {e}")

    def _launch_indicator(self) -> None:
        if hasattr(self, "_indicator_proc") and self._indicator_proc and self._indicator_proc.poll() is None:
            return
        try:
            env = dict(os.environ)
            lib_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            cur = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = f"{lib_dir}:{cur}".rstrip(":")
            env.pop("LD_PRELOAD", None)
            self._indicator_proc = subprocess.Popen([sys.executable, "-m", "sayri.indicator"], env=env)
            print(f"[Sayri] 🚀 Launched tray indicator (PID: {self._indicator_proc.pid})")
        except Exception as exc:
            print(f"[Sayri] Indicator launch error: {exc}")

    def _start_ipc_server(self) -> None:
        """Lightweight UNIX domain socket listener for CLI triggers and remote Gateways."""
        sock_path = os.path.join(paths.state_dir(), "sayri.sock")
        if os.path.exists(sock_path):
            try:
                os.unlink(sock_path)
            except OSError:
                pass

        def _worker():
            try:
                import socket
                import struct
                self._ipc_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self._ipc_sock.bind(sock_path)
                try:
                    os.chmod(sock_path, 0o600)
                except OSError:
                    pass
                self._ipc_sock.listen(5)
                while getattr(self, "_ipc_running", False):
                    try:
                        conn, _ = self._ipc_sock.accept()
                        conn.settimeout(40.0)

                        # Enforce peer UID validation on Linux to prevent cross-user socket hijacking
                        try:
                            cred = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
                            peer_pid, peer_uid, peer_gid = struct.unpack("3i", cred)
                            if peer_uid != os.getuid() and peer_uid != 0:
                                print(f"[Sayri IPC Security] 🚨 Rejected socket connection from untrusted UID {peer_uid}")
                                conn.close()
                                continue
                        except Exception:
                            pass

                        raw_data = conn.recv(8192).decode("utf-8").strip()
                        if raw_data.startswith("{"):
                            try:
                                msg = json.loads(raw_data)
                                if msg.get("type") in ("INCOMING_MSG", "remote_message"):
                                    prompt_text = msg.get("text", "")
                                    author = msg.get("author", "User")
                                    target_agent_id = msg.get("target_agent", "default")
                                    sandbox_level = msg.get("sandbox_level")
                                    instance_id = msg.get("instance_id", "default")
                                    custom_session_id = msg.get("session_id")
                                    reply = self.process_remote_message(
                                        text=prompt_text,
                                        author=author,
                                        target_agent_id=target_agent_id,
                                        sandbox_level=sandbox_level,
                                        instance_id=instance_id,
                                        session_id=custom_session_id,
                                    )
                                    conn.sendall(reply.encode("utf-8") + b"\n")
                                    conn.close()
                                    continue
                            except Exception as e:
                                print(f"[Sayri IPC] Message processing error: {e}")
                        elif raw_data == "toggle":
                            GLib.idle_add(self.toggle_visible)
                        elif raw_data == "show":
                            GLib.idle_add(lambda: self.overlay.show() if self.overlay else None)
                        elif raw_data == "hide":
                            GLib.idle_add(lambda: self.overlay.hide() if self.overlay else None)
                        elif raw_data == "listen":
                            GLib.idle_add(self.start_listening)
                        elif raw_data == "settings":
                            GLib.idle_add(self.open_settings)
                        elif raw_data == "quit":
                            GLib.idle_add(self.quit_app)
                        conn.sendall(b"OK\n")
                        conn.close()
                    except Exception:
                        break
            except Exception as exc:
                print(f"[Sayri] IPC server: {exc}")

        self._ipc_running = True
        threading.Thread(target=_worker, daemon=True).start()

    def process_remote_message(
        self,
        text: str,
        author: str,
        target_agent_id: Optional[str] = None,
        sandbox_level: Optional[str] = None,
        instance_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> str:
        """Processes an incoming message from a channel gateway (Telegram/Discord) via AgentEngine."""
        import copy
        done_event = threading.Event()
        result_holder = {"text": "", "error": None}

        def _on_delta(d: str):
            result_holder["text"] += d

        def _on_done(full: str):
            if full:
                result_holder["text"] = full
            done_event.set()

        def _on_error(exc: Exception):
            result_holder["error"] = str(exc)
            done_event.set()

        # 1. Resolve target agent profile
        agent_profile = None
        if target_agent_id:
            agent_profile = AgentCreator.get_agent(target_agent_id)
        if not agent_profile:
            agent_profile = self.active_agent or AgentCreator.get_agent("default")

        # 2. Apply custom Sandbox Level override if specified
        if sandbox_level:
            try:
                agent_profile = copy.deepcopy(agent_profile)
                lvl_enum = getattr(SandboxLevel, sandbox_level, None) or SandboxLevel(sandbox_level)
                agent_profile.sandbox.level = lvl_enum
            except Exception as exc:
                print(f"[Sayri Remote] Could not set custom sandbox level {sandbox_level}: {exc}")

        if not session_id:
            inst_tag = f"-{instance_id}" if instance_id else ""
            session_id = f"remote{inst_tag}-{agent_profile.id}-{author.replace('@', '')}"

        # Ensure session exists in SQLite repository with proper gateway metadata and title
        existing_session = self.storage.get_session(session_id)
        if not existing_session:
            gw_label = instance_id.replace("sayri-gateway-", "").capitalize() if instance_id else "Gateway"
            initial_title = f"{author} ({gw_label})"
            self.storage.create_session(
                agent_id=agent_profile.id,
                title=initial_title
            )

        try:
            self.engine.process_query(
                session_id=session_id,
                user_text=text,
                profile=agent_profile,
                cfg=self.cfg,
                on_delta=_on_delta,
                on_done=_on_done,
                on_tool_start=lambda _: None,
                on_tool_finish=lambda _t, _o, _c: None,
                on_error=_on_error,
            )
            # Wait up to 35 seconds for the LLM response
            done_event.wait(timeout=35.0)

            # Sync session changes with active desktop UI
            self._notify_sessions_updated()

            if result_holder["text"]:
                return result_holder["text"].strip()
            if result_holder["error"]:
                return f"⚠️ Sayri Error: {result_holder['error']}"
        except Exception as exc:
            print(f"[Sayri Remote] Engine error: {exc}")
            return f"⚠️ Error processing message: {exc}"

        return result_holder["text"].strip() or f"Hi {author}, I received your message: '{text}'."

    def _notify_sessions_updated(self) -> None:
        """Notifies cajita chat history UI in real-time when new remote messages arrive."""
        try:
            if self.overlay and hasattr(self.overlay, "cajita") and self.overlay.cajita:
                GLib.idle_add(self.overlay.cajita._populate_sessions)
        except Exception:
            pass

    def toggle_visible(self) -> None:
        if self.overlay:
            self.overlay.toggle()

    def on_shown(self) -> None:
        """Called when Sayri is shown: clean UI, play activation sound, and start listening."""
        print("[Sayri] Overlay shown: cleaning UI and playing activation sound.")
        if self._setup_needed:
            # Setup not finished yet: keep the setup message visible and do not
            # wipe it or start listening until an AI provider is configured.
            # Re-show the welcome so reopening from the appindicator keeps it.
            self._show_setup_prompt()
            if self.overlay:
                self.overlay.cajita.card_overlay.set_visible(True)
            return
        self._current_query_id += 1
        self.tts.cancel()
        sound.stop_all()
        self._set_busy(False)
        self._assistant_text = ""
        self._last_assistant_reply = ""
        self._on_level(0.0)
        if self.overlay:
            self.overlay.clear()
            self.overlay.cajita.clear()
            self.overlay.cajita.entry.set_text("")
            self.overlay.cajita.card_overlay.set_visible(False)
        sound.play("activate")
        self.start_listening()

    def _build_ui(self) -> None:
        self.overlay = overlay_mod.SayriOverlay(self)

    # ── state
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

        if self.overlay:
            self.overlay.set_state_sync(state)

    def on_hidden(self) -> None:
        """Called when Sayri is hidden: stop all speech, sounds, active execution, and clear history display."""
        print("[Sayri] 🙈 Overlay hidden: halting all speech, sounds, and active execution.")
        self._current_query_id += 1
        self.tts.cancel()
        sound.stop_all()
        self._set_busy(False)
        self._assistant_text = ""
        self._last_assistant_reply = ""
        self._on_level(0.0)
        if self.overlay:
            self.overlay.cajita.set_speaking(False)
            self.overlay.clear()
            self.overlay.cajita.clear()
            self.overlay.cajita.entry.set_text("")
            self.overlay.cajita.card_overlay.set_visible(False)
        self.stop_listening()
        mode = self.cfg.get_string("stt", "mode")
        if mode in ("always", "wakeword") and self.stt.ready:
            self._start_session()
            self.set_state("idle")
        else:
            self.set_state("idle")
        import gc
        gc.collect()

    # ── orb events
    def on_orb_click(self) -> None:
        """The orb halts any active generation/speech/action and listens immediately for new instructions."""
        if self._busy or self.tts.is_speaking or self.state in ("speaking", "thinking"):
            print("[Sayri] ⏹️ Orb clicked during speech/activity: halting all operations and listening...")
            self.tts.cancel()
            sound.stop_all()
            self._set_busy(False)
            self._assistant_text = ""
            self._on_level(0.0)
            if self.overlay:
                self.overlay.cajita.set_speaking(False)
            self.start_listening()
        else:
            self.toggle_listening()

    # ── helpers
    def _msg(self, kind: str, text: str) -> None:
        if self.overlay:
            self.overlay.set_content(kind, text)

    def _set_assistant(self, text: str) -> None:
        if self.overlay:
            self.overlay.set_content("assistant", text)

    def _show_setup_prompt(self) -> None:
        """Show the setup / welcome message when no AI provider is configured.

        Called both on first activation and every time the overlay is shown
        again (e.g. reopened from the appindicator) so that the setup reminder
        stays visible until an API key is configured.
        """
        welcome_msg = (
            "👋 **Welcome to Sayri!**\n\n"
            "To get started, please configure an **AI Provider**:\n"
            "• Click the **Sayri logo** on the left of the input bar (or ⚙) to open **Settings**.\n"
            "• Select a provider and add your API key (OpenAI, Anthropic, Groq, OpenRouter, or Ollama).\n"
            "• In Settings, you can also download local **Speech-to-Text (STT)** and **Text-to-Speech (TTS)** models for voice interaction."
        )
        self._set_assistant(welcome_msg)
        self._msg("hint", "Click the Sayri logo on the left to set up AI Provider & Voice")

    def _set_partial(self, text: str) -> None:
        if self.overlay:
            self.overlay.set_content("partial", text)

    def _set_mic(self, active: bool) -> None:
        self._mic_on = active
        if self.overlay:
            self.overlay.set_mic(active)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        if self.overlay:
            self.overlay.set_busy(busy)

    # ── STT
    def _start_session(self) -> None:
        if self.session and self.session.is_running():
            return
        if not self.stt.ready:
            return
        self.session = self.stt.create_session(
            on_partial=self._on_partial,
            on_utterance=self._on_utterance,
            on_level=self._on_level,
            on_speech_start=lambda: GLib.idle_add(self._on_speech_start),
            on_transcribe_start=lambda: GLib.idle_add(self._on_transcribe_start),
        )
        if self.session.start():
            self.set_state("listening")
            self._set_mic(True)
        else:
            self.session = None
            self._msg("error", "Could not initialize microphone.")

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
        self._msg("hint", "Listening…")

    def stop_listening(self) -> None:
        self.armed = False
        self._stop_session()
        self.tts.cancel()
        sound.stop_all()
        self._set_busy(False)
        self._assistant_text = ""
        self._on_level(0.0)
        if self.overlay:
            self.overlay.cajita.set_speaking(False)
        self.set_state("idle")
        self._set_mic(False)

    def toggle_listening(self) -> None:
        """Toggle microphone listening state on/off."""
        # 1. If currently busy, speaking, or thinking: halt everything immediately!
        if self._busy or self.tts.is_speaking or self.state in ("speaking", "thinking"):
            self.stop_listening()
            self._msg("hint", "Microphone off. Click to talk.")
            return

        # 2. If actively listening / armed: stop listening!
        if self.armed or self.state in ("listening", "activated") or (self.session and self.session.is_running()):
            self.stop_listening()
            self._msg("hint", "Microphone off. Click to talk.")
        else:
            # 3. If idle: start listening immediately!
            self.start_listening()

    def _apply_mode(self) -> None:
        self._stop_session()
        mode = self.cfg.get_string("stt", "mode")
        if mode in ("always", "wakeword"):
            self._start_session()
        else:
            self.set_state("idle")

    # ── STT callbacks (threads)
    def _on_speech_start(self) -> None:
        if self._busy or not self.armed:
            return
        self.set_state("listening")
        self._msg("hint", "Listening…")

    def _on_transcribe_start(self) -> None:
        if self._busy or not self.armed:
            return
        self.set_state("thinking")
        self._msg("hint", "Transcribing…")

    def _on_partial(self, text: str) -> None:
        GLib.idle_add(self._handle_partial, text)

    def _on_utterance(self, text: str) -> None:
        GLib.idle_add(self._handle_utterance, text)

    def _on_level(self, level: float) -> None:
        if self.overlay:
            self.overlay.set_audio_level(level)

    def _handle_partial(self, text: str) -> None:
        if self._busy or not self.armed:
            return
        self._set_partial(f"“{text}…”")

    def _handle_utterance(self, text: str) -> None:
        if not text.strip():
            self._set_partial("")
            mode = self.cfg.get_string("stt", "mode")
            if mode == "manual" or not self.armed:
                self.set_state("idle")
                self._msg("hint", "Ask me anything…")
            return
        if self._busy:
            self._msg("hint", "Please wait for the response to finish.")
            return

        # Check if text is self-echo from the last assistant response
        if getattr(self, "_last_assistant_reply", None):
            prev_clean = re.sub(r"[^\w\s]", "", self._last_assistant_reply.lower())
            curr_clean = re.sub(r"[^\w\s]", "", text.lower())
            if len(curr_clean) > 3 and (curr_clean in prev_clean or (len(prev_clean) > 3 and prev_clean in curr_clean)):
                print(f"[Sayri] ℹ️ Ignored TTS self-echo: \"{text}\"")
                return

        mode = self.cfg.get_string("stt", "mode")
        matched, remainder = self._match_and_extract_wake_word(text)
        ui_open = bool(self.overlay and self.overlay.is_visible)

        # In wakeword mode while running in background (UI closed and not armed), require wake word
        if mode == "wakeword" and not self.armed and not ui_open:
            if matched:
                if self.overlay and not self.overlay.is_visible:
                    self.overlay.show()
                if remainder and len(remainder) > 1:
                    # User asked the question in the same sentence as the wake word
                    self.armed = False
                    self.send_text(remainder)
                else:
                    # User only said the wake word
                    self.armed = True
                    self.start_listening()
                    self.set_state("activated")
                    self._msg("hint", "Listening…")
                    print(f"[Sayri] 🎯 Wake word activated from background: \"{text}\", listening for prompt...")
                return
            else:
                print(f"[Sayri] ℹ️ Wake word not found in \"{text}\" (mode=wakeword, UI hidden)")
                return

        # If user spoke ONLY the wake word without a question, arm and wait
        if matched and (not remainder or len(remainder) <= 1):
            if self.overlay and not self.overlay.is_visible:
                self.overlay.show()
            self.armed = True
            self.start_listening()
            self.set_state("activated")
            self._msg("hint", "Listening…")
            print("[Sayri] ℹ️ Wake word detected without question, waiting for prompt...")
            return

        if self.overlay and not self.overlay.is_visible:
            self.overlay.show()

        # If matched wake word with a query, send the query without the wake word; otherwise send full text
        query = remainder if (matched and remainder and len(remainder) > 1) else text
        self.armed = False
        self.send_text(query)
        if mode == "manual":
            self._stop_session()
            self.set_state("idle")

    def _match_and_extract_wake_word(self, text: str) -> tuple[bool, str]:
        raw = text.strip()
        import re

        cfg_ww = self.cfg.get_string("stt", "wake_word").strip().lower()
        candidates = set()
        if cfg_ww:
            for item in cfg_ww.split(","):
                clean = item.strip().lower()
                if clean:
                    candidates.add(clean)

        # Extended phonetic Spanish & English wake word variants
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

        # Convert spaces to flexible \s+
        regex_parts = []
        for w in sorted(candidates, key=len, reverse=True):
            parts = [re.escape(p) for p in w.split()]
            regex_parts.append(r"\s+".join(parts))

        full_regex = re.compile(r"\b(?:" + "|".join(regex_parts) + r")\b", re.IGNORECASE)

        # Replace punctuation with spaces for matching
        cleaned = re.sub(r"[,;:\.¿\?¡!\-_]", " ", raw)
        m = full_regex.search(cleaned)
        if m:
            end_pos = m.end()
            remainder = raw[end_pos:].strip(" \t\n\r,:;¿?¡!.")
            matched_word = m.group(0).strip()
            print(f"[Sayri] 🎯 Wake word '{matched_word}' matched in \"{raw}\" -> command: \"{remainder}\"")
            return True, remainder

        return False, ""

    # ── LLM & Agent Engine
    def send_text(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        self._current_query_id += 1
        query_id = self._current_query_id
        self._stop_session()
        self.tts.cancel()
        self.armed = False
        if self.overlay:
            self.overlay.clear()
            self.overlay.cajita.show_chat_view()
        self._assistant_text = ""
        self._set_busy(True)
        self.set_state("thinking")
        self._msg("user", text)
        print(f"[Sayri] 🤖 Querying AI ({self.active_agent.name}): \"{text}\"")

        self.engine.process_query(
            session_id=self.active_session_id,
            user_text=text,
            profile=self.active_agent,
            cfg=self.cfg,
            on_delta=lambda d: GLib.idle_add(self._on_delta, d, query_id),
            on_done=lambda full: GLib.idle_add(self._finish_engine_reply, full, query_id),
            on_tool_start=lambda cmd: GLib.idle_add(self._msg, "hint", f"⚙️ Running: {cmd[:36]}…"),
            on_tool_finish=lambda cmd, out, code: GLib.idle_add(
                lambda: self.overlay and self.overlay.cajita.set_tool_output(cmd, out)
            ),
            on_error=lambda e: GLib.idle_add(self._on_error, e, query_id),
        )

    def _finish_engine_reply(self, full: str, query_id: int = 0) -> None:
        if query_id != self._current_query_id:
            return
        if full:
            self._assistant_text = full
            self._set_assistant(full)
            self.history.append(("assistant", full))
            self.history = self.history[-HISTORY_MAX * 2:]
        self._finish_reply(full, query_id)

    def _llm_worker(self, messages: list[dict], depth: int = 1, query_id: int = 0) -> None:
        if query_id != self._current_query_id:
            return
        llm.stream_chat(
            self.cfg.get_string("provider", "base_url"),
            self.cfg.get_string("provider", "api_key"),
            self.cfg.get_string("provider", "model"),
            messages,
            temperature=self.cfg.get_float("provider", "temperature"),
            max_tokens=self.cfg.get_int("provider", "max_tokens") or None,
            stream=self.cfg.get_bool("provider", "stream"),
            timeout=self.cfg.get_int("provider", "timeout"),
            on_delta=lambda d: GLib.idle_add(self._on_delta, d, query_id),
            on_done=lambda full: GLib.idle_add(self._on_done, full, messages, depth, query_id),
            on_error=lambda e: GLib.idle_add(self._on_error, e, query_id),
        )

    def _on_delta(self, delta: str, query_id: int = 0) -> None:
        if query_id != self._current_query_id:
            return
        if isinstance(delta, bytes):
            delta = delta.decode("utf-8", errors="replace")
        elif not isinstance(delta, str):
            delta = str(delta) if delta is not None else ""
        self._assistant_text += delta
        self._set_assistant(self._assistant_text)

    def _on_done(self, full: str, messages: list[dict] | None = None, depth: int = 1, query_id: int = 0) -> None:
        if query_id != self._current_query_id:
            return
        import re
        if full and self.cfg.get_bool("provider", "agent_mode") and messages and depth < 6:
            # Support markdown code blocks AND XML-style tags
            m = re.search(r"```(?:bash|sh)?\s*\n(.*?)\n```", full, re.DOTALL)
            if not m:
                m = re.search(r"<(?:bash|sh|tool)>(.*?)</(?:bash|sh|tool)>", full, re.DOTALL)
            if m:
                cmd = m.group(1).strip()
                if cmd:
                    print(f"[Sayri] ⚙️ Agent step {depth} executing: `{cmd}`")
                    self._msg("hint", f"⚙️ Running ({depth}): {cmd[:36]}…")
                    threading.Thread(target=self._execute_tool_and_followup,
                                     args=(messages, full, cmd, depth, query_id), daemon=True).start()
                    return

        if full:
            self._assistant_text = full
            self._set_assistant(full)
            self.history.append(("assistant", full))
            self.history = self.history[-HISTORY_MAX * 2:]
        self._finish_reply(full, query_id)

    def _execute_tool_and_followup(self, messages: list[dict], full_reply: str, cmd: str, depth: int = 1, query_id: int = 0) -> None:
        if query_id != self._current_query_id:
            return
        retcode = 0
        raw_cmd = cmd.strip()
        is_elevated = "pkexec" in raw_cmd or "sudo" in raw_cmd
        if is_elevated:
            if raw_cmd.startswith("sudo "):
                cmd = "pkexec " + raw_cmd[5:]
            self._msg("hint", f"🔒 Requesting authorization: {cmd[:30]}…")

        try:
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=4)
            output = (res.stdout + "\n" + res.stderr).strip()
            retcode = res.returncode
            if retcode in (126, 127) and is_elevated:
                output = "The user cancelled or denied the administrator authorization (Polkit)."
            elif not output:
                output = f"(Command exited with code {res.returncode})"
        except subprocess.TimeoutExpired as exc:
            # If a command takes more than 4s (e.g. xdg-open, launching GUI apps, background tasks),
            # mark it as successfully launched in background and let the model continue immediately!
            partial_out = ""
            if exc.stdout:
                partial_out += exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else str(exc.stdout)
            if exc.stderr:
                partial_out += "\n" + (exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else str(exc.stderr))
            partial_out = partial_out.strip()
            output = partial_out or "(Command launched successfully and running in the background)"
            retcode = 0
            print(f"[Sayri] ⏱️ Tool step {depth} exceeded 4s (GUI app / xdg-open); continuing model immediately.")
        except Exception as exc:
            output = f"Command error: {exc}"
            retcode = 1

        if query_id != self._current_query_id:
            return

        print(f"[Sayri] ✓ Tool step {depth} exit {retcode} ({len(output)} bytes): \"{output[:100]}...\"")
        followup_messages = list(messages)
        followup_messages.append({"role": "assistant", "content": full_reply})

        if retcode != 0:
            prompt_content = (
                f"[Tool Error - Exit code {retcode}]:\nCommand: `{cmd}`\nOutput:\n{output}\n\n"
                "The command failed. Analyze what went wrong, fix the command and emit a new ```bash ... ``` block to retry, or explain the error."
            )
        else:
            prompt_content = (
                f"[Tool Output - Exit code 0]:\nCommand: `{cmd}`\nOutput:\n{output}\n\n"
                "If you need another command, emit a ```bash ... ``` block. Otherwise, summarize the final result naturally for voice in 1-2 sentences."
            )

        followup_messages.append({
            "role": "user",
            "content": prompt_content,
        })

        GLib.idle_add(lambda: self.overlay and self.overlay.cajita.set_tool_output(cmd, output))
        self._assistant_text = ""
        self._llm_worker(followup_messages, depth + 1, query_id)

    def _on_error(self, exc: Exception, query_id: int = 0) -> None:
        if query_id != self._current_query_id:
            return
        self._msg("error", f"Provider error: {exc}")
        self._set_busy(False)
        if self.overlay:
            self.overlay.cajita.set_speaking(False)
        self._after_reply()

    def _finish_reply(self, full: str, query_id: int = 0) -> None:
        if query_id != self._current_query_id:
            return
        self._last_assistant_reply = full
        spoken = markdown_to_plain_speech(full)
        if self.cfg.get_bool("tts", "enabled") and spoken and self.tts.ready:
            # Stop microphone during TTS speech to prevent feedback loop!
            self._stop_session()
            self.set_state("speaking")
            print(f"[Sayri] 🔊 Speaking response with Piper TTS: \"{spoken[:60]}...\"")
            self.tts.speak_async(
                spoken,
                on_level=lambda lvl: GLib.idle_add(self._on_level, lvl),
                on_end=lambda: GLib.idle_add(self._after_reply),
                on_error=lambda e: GLib.idle_add(self._on_error, e),
            )
        else:
            self._after_reply()

    def _after_reply(self) -> None:
        self._set_busy(False)
        self._on_level(0.0)
        self._assistant_text = ""
        if self.overlay:
            self.overlay.cajita.set_speaking(False)
        mode = self.cfg.get_string("stt", "mode")
        if mode in ("always", "wakeword"):
            self._start_session()
            if mode == "wakeword":
                self.set_state("idle")
            else:
                self.set_state("listening")
        else:
            self.set_state("idle")

    # ── UI glue
    def toggle_visible(self) -> None:
        if self.overlay:
            self.overlay.toggle()

    def open_settings(self) -> None:
        if self.overlay:
            self.overlay.show()
            self.overlay.cajita.switch_tab("settings")
            return
        import subprocess
        import sys
        try:
            if hasattr(self, "_settings_proc") and self._settings_proc and self._settings_proc.poll() is None:
                return
            env = dict(os.environ)
            lib_path = os.path.dirname(os.path.dirname(__file__))
            env["PYTHONPATH"] = lib_path + (":" + env["PYTHONPATH"] if "PYTHONPATH" in env else "")
            env.pop("LD_PRELOAD", None)
            self._settings_proc = subprocess.Popen([sys.executable, "-m", "sayri.settings_gtk3"], env=env)
        except Exception:
            if self.settings_win is None:
                self.settings_win = settings_window.SettingsWindow(self)
            self.settings_win.show()

    def refresh_status(self) -> None:
        if self.settings_win is not None:
            self.settings_win.refresh_status()

    def apply_ui_config(self) -> None:
        if self.overlay:
            self.overlay.apply_config()

    def apply_autostart(self) -> None:
        autostart_dir = os.path.expanduser("~/.config/autostart")
        dest = os.path.join(autostart_dir, "sayri.desktop")
        if self.cfg.get_bool("ui", "autostart"):
            try:
                os.makedirs(autostart_dir, exist_ok=True)
                content = (
                    "[Desktop Entry]\n"
                    "Type=Application\n"
                    "Name=Sayri\n"
                    "Comment=Sayri voice assistant orb\n"
                    "Exec=/usr/bin/sayri --autostart\n"
                    "Terminal=false\n"
                    "X-GNOME-Autostart-enabled=true\n"
                )
                with open(dest, "w", encoding="utf-8") as f:
                    f.write(content)
                print(f"[sayri] Ensured autostart desktop file at {dest}")
            except OSError as exc:
                print(f"[sayri] autostart error: {exc}")
        else:
            try:
                if os.path.exists(dest):
                    os.remove(dest)
                    print(f"[sayri] Removed autostart desktop file from {dest}")
            except OSError:
                pass

    def _on_config_change(self, group: str, key: str, _value) -> None:
        if group == "ui":
            if key == "autostart":
                self.apply_autostart()
            else:
                self.apply_ui_config()
        elif group == "stt" and key == "mode":
            self._apply_mode()
        elif group == "provider" and key == "api_key":
            # Setup (first-run welcome) is done once an AI provider key is set.
            has_key = bool(self.cfg.get_string("provider", "api_key").strip())
            if self._setup_needed and has_key:
                self._setup_needed = False
                self._set_busy(False)
                self._after_reply()

    # ── shutdown
    def quit_app(self) -> None:
        self._ipc_running = False
        if hasattr(self, "_indicator_proc") and self._indicator_proc and self._indicator_proc.poll() is None:
            try:
                self._indicator_proc.terminate()
            except Exception:
                pass
        sock_path = os.path.join(paths.state_dir(), "sayri.sock")
        if os.path.exists(sock_path):
            try:
                os.remove(sock_path)
            except OSError:
                pass
        self._stop_session()
        self.tts.cancel()
        sound.stop_all()
        self.quit()

    def _on_shutdown(self, _app) -> None:
        self._stop_session()
        self.tts.cancel()
        sound.stop_all()


def main() -> int:
    args = list(sys.argv[1:])
    is_autostart = "--autostart" in args
    is_toggle = "--toggle" in args or "-t" in args
    is_show = "--show" in args
    is_hide = "--hide" in args
    is_settings = "--settings" in args or "-s" in args
    is_quit = "--quit" in args or "-q" in args

    sock_path = os.path.join(paths.state_dir(), "sayri.sock")
    if os.path.exists(sock_path):
        try:
            import socket
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(0.5)
            s.connect(sock_path)
            if is_show:
                cmd = b"show\n"
            elif is_hide:
                cmd = b"hide\n"
            elif is_settings:
                cmd = b"settings\n"
            elif is_quit:
                cmd = b"quit\n"
            elif is_autostart:
                # Already running instance, autostart finishes silently
                s.close()
                return 0
            else:
                cmd = b"toggle\n"
            s.sendall(cmd)
            s.recv(1024)
            s.close()
            print(f"[Sayri] Forwarded command {cmd.decode().strip()} to running instance via IPC.")
            return 0
        except Exception:
            try:
                os.remove(sock_path)
            except OSError:
                pass

    if is_quit:
        return 0

    # Filter out custom CLI arguments so Gtk.Application argument parser does not fail with "No such option"
    filtered_argv = [sys.argv[0]]
    for a in sys.argv[1:]:
        if a not in ("--autostart", "--toggle", "-t", "--show", "--hide", "--settings", "-s", "--quit", "-q"):
            filtered_argv.append(a)

    app = SayriApp(is_autostart=is_autostart)
    app.hold()
    if os.environ.get("SAYRI_AUTOQUIT_MS"):
        try:
            GLib.timeout_add(int(os.environ["SAYRI_AUTOQUIT_MS"]), app.quit)
        except ValueError:
            pass
    code = app.run(filtered_argv)
    print(f"[sayri] run() finished with code {code}")
    return code
