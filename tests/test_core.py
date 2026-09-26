"""Tests for sayri.core: headless SayriCore (no GTK / display required)."""

import os
import sys
import tempfile
import threading
import time

_TMP = tempfile.mkdtemp(prefix="sayri-core-test-")
os.environ["SAYRI_STATE_DIR"] = os.path.join(_TMP, "state")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "share", "sayri", "lib"))

import sayri.sound  # noqa: E402


# Keep tests silent and hermetic: never spawn real audio players.
sayri.sound.play = lambda name: None
sayri.sound.start_loop = lambda name: None
sayri.sound.stop_loop = lambda: None
sayri.sound.stop_all = lambda: None


class RecordingUI:
    def __init__(self):
        self.events = {}
        self.user = []

    def _rec(self, name):
        def _f(*args, **kwargs):
            self.events.setdefault(name, []).append((args, kwargs))
        return _f

    def __getattr__(self, name):
        if name.startswith("on_"):
            return self._rec(name)
        raise AttributeError(name)


class FakeEngine:
    """Replaces AgentEngine.process_query with a scripted background reply."""

    def __init__(self, reply="hola, esto es una prueba"):
        self.reply = reply
        self.calls = []

    def process_query(self, session_id, user_text, profile, cfg,
                      on_delta, on_done, on_tool_start, on_tool_finish, on_error):
        self.calls.append((session_id, user_text))

        def _run():
            try:
                time.sleep(0.05)
                on_delta(self.reply)
                on_done(self.reply)
            except Exception as exc:
                on_error(exc)

        threading.Thread(target=_run, daemon=True).start()
        return 0


def _make_core(reply="hola, esto es una prueba"):
    from sayri.core import SayriCore

    ui = RecordingUI()
    core = SayriCore(ui=ui)
    # Fake the audio backends so tests never touch a mic / Piper / speakers.
    from types import SimpleNamespace

    core.tts = SimpleNamespace(ready=False, missing=lambda: [], cancel=lambda: None, is_speaking=False)
    core.stt = SimpleNamespace(ready=False, missing=lambda: [])
    core.engine = FakeEngine(reply=reply)
    return core, ui


def test_wake_word_extraction():
    core, _ = _make_core()
    matched, remainder = core._match_and_extract_wake_word("oye sayri, dime que hora es")
    assert matched
    assert remainder == "dime que hora es"

    matched, remainder = core._match_and_extract_wake_word("hola, ¿cómo estás?")
    assert not matched

    matched, remainder = core._match_and_extract_wake_word("hey siri apaga la luz")
    assert matched
    assert remainder == "apaga la luz"


def test_status_info_keys():
    core, _ = _make_core()
    info = core.status_info()
    for key in ("version", "state", "stt_ready", "tts_ready", "session_id", "agent", "provider"):
        assert key in info, f"status_info must include '{key}'"


def test_ask_returns_text_and_events():
    core, ui = _make_core(reply="respuesta de prueba")
    result = core.ask("pregunta")
    assert result["text"] == "respuesta de prueba", result
    assert result["error"] is None
    assert "on_user" in ui.events and ui.events["on_user"][0][0] == ("pregunta",)
    assert "on_assistant_delta" in ui.events
    assert "assistant_done" not in ui.events


def test_send_text_changes_state():
    core, ui = _make_core()
    core.send_text("hola")
    deadline = time.time() + 3.0
    while ("thinking" not in [e[0][0] for e in ui.events.get("on_state", [])]) and time.time() < deadline:
        time.sleep(0.05)
    states = [e[0][0] for e in ui.events.get("on_state", [])]
    assert "thinking" in states, states
    assert ui.events.get("on_busy"), "busy event expected"


def test_new_conversation_new_session():
    core, ui = _make_core()
    first = core.active_session_id
    core.new_conversation()
    assert core.active_session_id != first
    assert "on_conversation_started" in ui.events


def test_stop_listening_idle():
    core, _ = _make_core()
    core.start_listening()
    core.stop_listening()
    assert core.state == "idle"
    assert not core._mic_on


def test_interrupt():
    core, _ = _make_core()
    core.send_text("hola")
    core.interrupt()
    assert not core._busy


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
            except Exception as exc:
                failures += 1
                print(f"  FAIL {name} (exception): {exc}")
    sys.exit(1 if failures else 0)