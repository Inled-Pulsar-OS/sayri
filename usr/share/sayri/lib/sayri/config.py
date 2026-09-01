"""Sayri configuration (GLib key file stored at ~/.config/sayri/sayri.conf)."""

from __future__ import annotations

from gi.repository import GLib

from . import paths

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
    },
}


class Config:
    """Typed accessors over a GLib.KeyFile with per-group defaults."""

    def __init__(self) -> None:
        self._kf = GLib.KeyFile.new()
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
            self._kf.load_from_file(cfg_path, GLib.KeyFileFlags.NONE)
        except GLib.Error:
            pass  # first run: defaults only

    def save(self) -> None:
        paths.ensure_dirs()
        ok = self._kf.save_to_file(paths.config_file())
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
        except GLib.Error:
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
