"""Tests for sayri.config. Run via tests/run-tests.sh."""

import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="sayri-test-")
os.environ["SAYRI_CONFIG_DIR"] = _TMP
os.environ["SAYRI_STATE_DIR"] = os.path.join(_TMP, "state")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "share", "sayri", "lib"))

from sayri.config import Config, DEFAULTS  # noqa: E402


def test_defaults():
    cfg = Config()
    assert cfg.get_string("provider", "base_url") == "http://127.0.0.1:11434/v1"
    assert cfg.get_string("stt", "mode") == "wakeword"
    assert cfg.get_bool("provider", "stream") is True
    assert cfg.get_int("provider", "max_tokens") == 512
    assert cfg.get_float("provider", "temperature") == 0.7
    assert cfg.get_int("ui", "orb_size") == 140


def test_set_get_roundtrip():
    cfg = Config()
    cfg.set("provider", "model", "gpt-4o-mini")
    cfg.set("stt", "wake_word", "hey siri")
    cfg.set("tts", "speed", 1.4)
    cfg.set("ui", "always_on_top", False)

    reloaded = Config()
    assert reloaded.get_string("provider", "model") == "gpt-4o-mini"
    assert reloaded.get_string("stt", "wake_word") == "hey siri"
    assert abs(reloaded.get_float("tts", "speed") - 1.4) < 1e-6
    assert reloaded.get_bool("ui", "always_on_top") is False


def test_unknown_key_falls_back_to_default():
    cfg = Config()
    assert cfg.get_int("stt", "silence_ms") == 500
    # a key missing from the file should fall back, not raise
    assert cfg.get_string("provider", "model") != ""


def test_on_change_listener():
    cfg = Config()
    seen = []
    cfg.on_change(lambda g, k, v: seen.append((g, k)))
    cfg.set("ui", "orb_size", 300)
    assert ("ui", "orb_size") in seen


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
