"""Sayri configuration (key file stored at ~/.config/sayri/sayri.conf).

Uses GLib.KeyFile when PyGObject is available (Linux desktop), otherwise falls
back to a dependency-free stdlib backend implementing the same read/write
syntax (``[group]`` + ``key=value``, booleans as ``true``/``false``), so the
headless core imports cleanly on macOS and Windows.
"""

from __future__ import annotations

import configparser
import os

from . import paths

try:
    from gi.repository import GLib
    _GLIB_AVAILABLE = True
except Exception:  # noqa: BLE001 - PyGObject absent (macOS/Windows headless)
    _GLIB_AVAILABLE = False

# Escape hatch so the portable backend can be exercised/shadowed on systems
# where GLib is installed (e.g. packagers or CI validating the macOS/Windows path).
if os.environ.get("SAYRI_NO_GLIB", "").strip().lower() in ("1", "true", "yes", "on"):
    _GLIB_AVAILABLE = False


class _MissingKeyError(Exception):
    """Raised by a key file backend when a key is absent from the file."""


class _GLibKeyFile:
    """Thin wrapper around GLib.KeyFile that re-raises missing keys uniformly."""

    def __init__(self) -> None:
        self._kf = GLib.KeyFile.new()

    def load(self, path: str) -> None:
        self._kf.load_from_file(path, GLib.KeyFileFlags.NONE)

    def save(self, path: str) -> bool:
        return self._kf.save_to_file(path)

    def set_string(self, group: str, key: str, value: str) -> None:
        self._kf.set_string(group, key, str(value))

    def set_integer(self, group: str, key: str, value: int) -> None:
        self._kf.set_integer(group, key, int(value))

    def set_double(self, group: str, key: str, value: float) -> None:
        self._kf.set_double(group, key, float(value))

    def set_boolean(self, group: str, key: str, value: bool) -> None:
        self._kf.set_boolean(group, key, bool(value))

    def _get(self, group: str, key: str, getter: str):
        try:
            return getattr(self._kf, getter)(group, key)
        except GLib.Error as exc:
            raise _MissingKeyError(f"{group}.{key}") from exc

    def get_string(self, group: str, key: str) -> str:
        return self._get(group, key, "get_string")

    def get_integer(self, group: str, key: str) -> int:
        return self._get(group, key, "get_integer")

    def get_double(self, group: str, key: str) -> float:
        return self._get(group, key, "get_double")

    def get_boolean(self, group: str, key: str) -> bool:
        return self._get(group, key, "get_boolean")


class _PortableKeyFile:
    """GLib.KeyFile-compatible backend built on configparser (no C deps)."""

    def __init__(self) -> None:
        self._cp = configparser.ConfigParser(
            interpolation=None,
            delimiters=("=",),
            comment_prefixes=("#",),
            inline_comment_prefixes=None,
            strict=False,
            empty_lines_in_values=False,
        )

    def load(self, path: str) -> None:
        self._cp.read(path, encoding="utf-8")

    def save(self, path: str) -> bool:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            self._cp.write(f)
        return True

    def _ensure_section(self, group: str) -> None:
        if not self._cp.has_section(group):
            self._cp.add_section(group)

    def set_string(self, group: str, key: str, value: str) -> None:
        self._ensure_section(group)
        self._cp[group][key] = str(value)

    def set_integer(self, group: str, key: str, value: int) -> None:
        self.set_string(group, key, str(int(value)))

    def set_double(self, group: str, key: str, value: float) -> None:
        self.set_string(group, key, repr(float(value)))

    def set_boolean(self, group: str, key: str, value: bool) -> None:
        self.set_string(group, key, "true" if bool(value) else "false")

    def _get(self, group: str, key: str, cast):
        if not self._cp.has_option(group, key):
            raise _MissingKeyError(f"{group}.{key}")
        return cast(self._cp.get(group, key))

    def get_string(self, group: str, key: str) -> str:
        return self._get(group, key, str)

    def get_integer(self, group: str, key: str) -> int:
        return self._get(group, key, int)

    def get_double(self, group: str, key: str) -> float:
        return self._get(group, key, float)

    def get_boolean(self, group: str, key: str) -> bool:
        return self._get(group, key, lambda s: s.strip().lower() == "true")


DEFAULTS: dict[str, dict[str, object]] = {
    "provider": {
        "base_url": "http://127.0.0.1:11434/v1",
        "api_key": "",
        "model": "llama3.2",
        "system_prompt": (
            "You are Sayri, a concise and natural voice assistant. "
            "Answer in short, spoken-style sentences."
        ),
        "agent_mode": True,
        "temperature": 0.7,
        "max_tokens": 512,
        "stream": True,
        "timeout": 120,
        "strip_patterns": " thinking.*? response, <thought>.*?</thought>",
    },
    "stt": {
        "mode": "wakeword",  # always | wakeword | manual
        "wake_word": "hey sayri",
        "model_size": "base",
        "language": "es",
        "mic_device": "",
        "silence_ms": 500,
        "live_transcript": True,
    },
    "tts": {
        "enabled": True,
        "language": "es_ES",
        "voice": "sharvard",
        "quality": "medium",
        "speed": 1.0,
    },
    "ui": {
        "orb_size": 140,
        "orb_position": "top-right",  # pinned to top-right
        "autostart": True,
        "always_on_top": True,
        "bubble_visible": True,
        "default_ui": "desktop",  # UI the launcher opens: "desktop", "orb" or a plugin id
        "autostart_mode": "ui",  # "ui" = UI + daemon at login | "daemon" = daemon only
        "setup_complete": False,  # set by the welcome wizard on first run
    },
}

_TYPES: dict[str, dict[str, str]] = {
    "provider": {
        "base_url": "string",
        "api_key": "string",
        "model": "string",
        "system_prompt": "string",
        "agent_mode": "bool",
        "temperature": "double",
        "max_tokens": "int",
        "stream": "bool",
        "timeout": "int",
        "strip_patterns": "string",
    },
    "stt": {
        "mode": "string",
        "wake_word": "string",
        "model_size": "string",
        "language": "string",
        "mic_device": "string",
        "silence_ms": "int",
        "live_transcript": "bool",
    },
    "tts": {
        "enabled": "bool",
        "language": "string",
        "voice": "string",
        "quality": "string",
        "speed": "double",
    },
    "ui": {
        "orb_size": "int",
        "orb_position": "string",
        "autostart": "bool",
        "always_on_top": "bool",
        "bubble_visible": "bool",
        "default_ui": "string",
        "autostart_mode": "string",
        "setup_complete": "bool",
    },
}


class Config:
    """Typed accessors over a key file with per-group defaults."""

    def __init__(self) -> None:
        self._kf: object = (_GLibKeyFile if _GLIB_AVAILABLE else _PortableKeyFile)()
        self._listeners: list[callable] = []

        # Apply defaults so getters never fail, then load real values on top.
        for group, keys in DEFAULTS.items():
            for key, value in keys.items():
                self._set(group, key, value)
        self.load()

    # ------------------------------------------------------------------ I/O
    def load(self) -> None:
        import os
        cfg_path = paths.config_file()
        self._is_first_run = not os.path.exists(cfg_path)
        try:
            self._kf.load(cfg_path)
        except Exception:
            pass  # first run: defaults only (missing file is tolerated)

    def save(self) -> None:
        paths.ensure_dirs()
        ok = self._kf.save(paths.config_file())
        if not ok:
            print(f"[sayri] warning: could not save config to {paths.config_file()}")

    def _set(self, group: str, key: str, value: object) -> None:
        kind = _TYPES[group][key]
        if kind == "string":
            self._kf.set_string(group, key, str(value))
        elif kind == "int":
            self._kf.set_integer(group, key, int(value))
        elif kind == "double":
            self._kf.set_double(group, key, float(value))
        elif kind == "bool":
            self._kf.set_boolean(group, key, bool(value))

    # ------------------------------------------------------------- getters
    def get(self, group: str, key: str):
        kind = _TYPES[group][key]
        try:
            if kind == "string":
                return self._kf.get_string(group, key)
            if kind == "int":
                return self._kf.get_integer(group, key)
            if kind == "double":
                return self._kf.get_double(group, key)
            if kind == "bool":
                return self._kf.get_boolean(group, key)
        except _MissingKeyError:
            # Missing key after a config edit: fall back to default.
            return DEFAULTS[group][key]
        return DEFAULTS[group][key]

    def get_string(self, group: str, key: str) -> str:
        return str(self.get(group, key))

    def get_int(self, group: str, key: str) -> int:
        return int(self.get(group, key))

    def get_float(self, group: str, key: str) -> float:
        return float(self.get(group, key))

    def get_bool(self, group: str, key: str) -> bool:
        return bool(self.get(group, key))

    def set(self, group: str, key: str, value: object, persist: bool = True) -> None:
        self._set(group, key, value)
        if persist:
            self.save()
            for cb in list(self._listeners):
                try:
                    cb(group, key, value)
                except Exception as exc:  # noqa: BLE001 - UI callbacks must not kill us
                    print(f"[sayri] config listener error: {exc}")

    def set_string(self, group: str, key: str, value: str, persist: bool = True) -> None:
        self.set(group, key, str(value), persist=persist)

    def set_int(self, group: str, key: str, value: int, persist: bool = True) -> None:
        self.set(group, key, int(value), persist=persist)

    def set_float(self, group: str, key: str, value: float, persist: bool = True) -> None:
        self.set(group, key, float(value), persist=persist)

    def set_bool(self, group: str, key: str, value: bool, persist: bool = True) -> None:
        self.set(group, key, bool(value), persist=persist)

    def on_change(self, callback: callable) -> None:
        self._listeners.append(callback)


# Singleton used across the app.
config = Config()