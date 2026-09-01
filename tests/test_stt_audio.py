"""Tests for sayri.audio and sayri.stt helpers."""

import os
import struct
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="sayri-test-")
os.environ["SAYRI_STATE_DIR"] = os.path.join(_TMP, "state")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "share", "sayri", "lib"))

from sayri import audio  # noqa: E402


def _chunk(*amplitudes):
    """Build a raw S16LE chunk from amplitudes in -1..1."""
    data = b"".join(struct.pack("<h", int(a * 32767)) for a in amplitudes)
    return data


def test_rms_silence():
    assert audio.rms_level(b"\x00\x00" * 100) == 0.0


def test_rms_full_scale():
    level = audio.rms_level(_chunk(1.0, -1.0, 1.0, -1.0))
    assert 0.99 < level <= 1.0


def test_rms_half_scale():
    level = audio.rms_level(_chunk(0.5, -0.5, 0.5))
    assert 0.45 < level < 0.55


def test_rms_mixed():
    level = audio.rms_level(_chunk(0.0, 0.0, 0.5, 0.5))
    assert 0.3 < level < 0.4


def test_mic_command_selection():
    cmd = audio.mic_command()
    # Either pipewire/pulseaudio is present and gives a command, or we accept None.
    if cmd is not None:
        assert cmd[0] in ("pw-record", "parec")
        assert "--raw" in cmd or "--raw" in " ".join(cmd)


def test_missing_whisper_binary():
    from sayri.stt import STTEngine

    from sayri.config import Config

    cfg = Config()
    engine = STTEngine(cfg)
    # In this environment whisper-cli is not installed: transcribe must raise.
    try:
        engine.transcribe("/nonexistent.wav")
        raised = False
    except RuntimeError:
        raised = True
    assert raised, "transcribe debe fallar sin whisper-cli"


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
