"""Tests for sayri.downloads helpers (no network access)."""

import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="sayri-test-")
os.environ["SAYRI_STATE_DIR"] = os.path.join(_TMP, "state")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "share", "sayri", "lib"))

from sayri import downloads  # noqa: E402


def test_whisper_model_filename():
    assert downloads.whisper_model_filename("base", "es") == "ggml-base.bin"
    assert downloads.whisper_model_filename("base", "EN") == "ggml-base.en.bin"
    assert downloads.whisper_model_filename("large-v3", "en") == "ggml-large-v3.bin"
    assert downloads.whisper_model_filename("small", "auto") == "ggml-small.bin"


def test_whisper_url():
    url = downloads.whisper_model_url("base", "es")
    assert "huggingface.co/ggerganov/whisper.cpp" in url
    assert url.endswith("ggml-base.bin")


def test_piper_entry_and_path():
    entry = downloads._piper_entry("es_ES", "sharvard", "medium")
    assert entry["path"] == "es/es_ES/sharvard/medium/es_ES-sharvard-medium.onnx"

    # unknown voice falls back to the first voice of the language
    fallback = downloads._piper_entry("es_ES", "no-such-voice", "x")
    assert fallback["voice"] == "sharvard"

    path = downloads.piper_voice_path("en_US", "amy", "medium")
    assert path.endswith("en_US-amy-medium.onnx")
    assert downloads.PIPER_VOICES["de_DE"], "de_DE debe tener voces"


def test_known_languages():
    for lang in ("en_US", "es_ES", "fr_FR", "de_DE", "it_IT", "pt_BR", "ru_RU"):
        assert lang in downloads.PIPER_VOICES, f"falta {lang}"
        assert downloads.PIPER_VOICES[lang]


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
