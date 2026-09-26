"""Declarative plugin settings (xui ``ui.settings``) — cross-renderer.

Model implemented by ``os.inled.es/sayri_xui_spec §6``: a plugin ``manifest.json``
can declare a settings form with xui nodes plus a ``key`` that maps to the plugin's
``config.json``. This module reads/writes that file and builds the settings flow
(xui host) rendered by the terminal (TUI/piped) and the Settings window (GTK)
without duplicating logic.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from . import paths, xui

# Editable widgets rendered as a form (entry, select, check).
EDITABLE = ("entry", "select", "check")


def _service_block(manifest: dict) -> Optional[dict]:
    svc = manifest.get("service")
    if isinstance(svc, dict) and (svc.get("start") or svc.get("status")):
        return svc
    return None


def _enabled_key(svc: dict) -> str:
    return str(svc.get("enabled_key") or "enabled")


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in ("1", "true", "yes", "on", "si")


def settings_schema(manifest: dict) -> Optional[dict]:
    """Return the manifest ``ui`` block if it declares an editable form.

    A form exists when ``ui.settings`` contains at least one editable widget
    (``entry``/``select``/``check``). Returns ``None`` when the plugin has no
    settings to expose.
    """
    ui = manifest.get("ui")
    if not isinstance(ui, dict):
        return None
    nodes = ui.get("settings")
    if not isinstance(nodes, list):
        return None
    if not any(isinstance(n, dict) and n.get("t") in EDITABLE and n.get("key") for n in nodes):
        return None
    return ui


def editable_fields(ui: Optional[dict]) -> list:
    """Editable form widgets (those with ``id`` and ``key``)."""
    if not isinstance(ui, dict):
        return []
    return [n for n in ui.get("settings", [])
            if isinstance(n, dict) and n.get("t") in EDITABLE and n.get("id") and n.get("key")]


def settings_file_path(manifest: dict) -> Path:
    """Plugin config path: ``ui.settings_file`` or ``<id>.json``.

    Resolved under the Sayri config directory (respects ``SAYRI_CONFIG_DIR``).
    Absolute paths are used as-is.
    """
    pid = manifest.get("id") or "plugin"
    ui = manifest.get("ui") or {}
    name = ui.get("settings_file") or f"{pid}.json"
    p = Path(name).expanduser()
    if not p.is_absolute():
        p = Path(paths.config_dir()) / p
    return p


def read_values(manifest: dict) -> dict:
    """Current values stored in the plugin config.json."""
    fp = settings_file_path(manifest)
    try:
        if fp.is_file():
            data = json.loads(fp.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - a broken config must not break the UI
        pass
    return {}


def write_setting(manifest: dict, key: str, value: Any) -> bool:
    """Persist ``key=value`` in the plugin config (merge, never deletes other keys)."""
    fp = settings_file_path(manifest)
    data = read_values(manifest)
    data[key] = value
    try:
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                      encoding="utf-8")
        return True
    except OSError:
        return False


def _widget_display(n: dict, values: dict) -> dict:
    """Convert a manifest node into an xui node ready to render."""
    t = n.get("t")
    if t == "text":
        return xui.text(n.get("text", ""), accent=bool(n.get("accent")), dim=bool(n.get("dim")))
    if t == "sub":
        return xui.sub(n.get("text", ""))
    if t == "note":
        return xui.note(n.get("text", ""), n.get("level", "info"))
    if t == "spacer":
        return xui.spacer()
    if t == "entry":
        key = n.get("key") or n.get("id")
        cur = values.get(key, n.get("default", ""))
        if cur in (None, True, False):
            cur = "" if n.get("t") == "entry" else cur
        return xui.entry(n.get("id"), label=n.get("label", ""),
                         default=str(cur),
                         placeholder=n.get("placeholder", ""),
                         secret=bool(n.get("secret")), hint=n.get("hint", ""))
    if t == "select":
        opts = [{k: o.get(k) for k in ("value", "label", "desc") if k in o}
                for o in n.get("options", []) if isinstance(o, dict)]
        cur = values.get(n.get("key"), n.get("default", ""))
        if not any(o.get("value") == cur for o in opts):
            cur = opts[0]["value"] if opts else ""
        return xui.select(n.get("id"), n.get("label", ""), opts, default=cur)
    if t == "check":
        key = n.get("key") or n.get("id")
        return xui.check(n.get("id"), n.get("label", ""),
                         default=bool(values.get(key, n.get("default", False))))
    return xui.text("", dim=True)


class SettingsApp:
    """xui host for a plugin's declarative settings form.

    Renders a single form screen and saves to ``config.json`` on
    ``submit``/``action:save``. Works the same on the TUI, piped mode and (via
    the host) the WebKit/HTTP window.
    """

    def __init__(self, manifest: dict, title: str = "") -> None:
        self.manifest = manifest
        self.ui = manifest.get("ui") or {}
        self.title = title or manifest.get("name") or manifest.get("id") or "Plugin"
        self.values = read_values(manifest)
        self._done = False
        self._last_error: Optional[str] = None
        self._last_service_note: Optional[str] = None
        self._last_service_ok = True

    # ------------------------------------------------------------- screens
    def render(self) -> Optional[dict]:
        if self._done:
            body = [xui.text("Settings saved ✓", accent=True),
                    xui.note("Restart the gateway/plugin if it needs to re-read the config.",
                             "info")]
            if self._last_service_note:
                body.append(xui.note(self._last_service_note, "ok" if self._last_service_ok else "warn"))
            footer = [xui.button("quit", "Close", kind="primary")]
            return xui.screen(f"{self.title} — settings", body, id="done",
                              done=True, footer=footer)
        body: list = [xui.sub(f"{self.title} — plugin settings")]
        sync = self.ui.get("sync_instructions")
        if self.ui.get("settings_file"):
            body.append(xui.text(f"Config file: {settings_file_path(self.manifest)}", dim=True))
        if sync:
            body.append(xui.note(sync, "info"))
        svc = _service_block(self.manifest)
        if svc:
            key = _enabled_key(svc)
            body.append(xui.check(
                key, svc.get("enabled_label") or "Run this service when Sayri starts",
                default=_as_bool(self.values.get(
                    key,
                    svc.get("enabled", svc.get("auto_start", True))))))
            body.append(xui.text("Saving toggles the service on/off right away.", dim=True))
        for n in self.ui.get("settings", []):
            body.append(_widget_display(n, self.values))
        footer = [xui.button("save", "Save", kind="primary"),
                  xui.button("cancel", "Cancel")]
        return xui.screen(f"{self.title} — settings", body, id="settings", footer=footer)

    def dispatch(self, event: dict) -> Optional[dict]:
        t = event.get("type")
        if t == "action":
            wid = event.get("widget", "")
            if wid in ("save", "guardar", "ok"):
                self._save(event.get("value", {}))
                return self.render()
            if wid in ("quit", "close", "cancel", "cancelar"):
                return None
        if t == "submit":
            self._save(event.get("value", {}))
            return self.render()
        return self.render()

    # ------------------------------------------------------------ persistence
    def _save(self, value: dict) -> None:
        written: list[str] = []
        for n in self.ui.get("settings", []):
            if not isinstance(n, dict) or n.get("t") not in EDITABLE:
                continue
            key = n.get("key")
            if not key or not n.get("id"):
                continue
            if n.get("id") not in value:
                continue
            v = value[n["id"]]
            if n.get("t") == "check":
                v = bool(v)
            else:
                v = str(v)
            if write_setting(self.manifest, key, v):
                written.append(key)

        svc = _service_block(self.manifest)
        if svc:
            skey = _enabled_key(svc)
            before = _as_bool(self.values.get(
                skey, svc.get("enabled", svc.get("auto_start", True))))
            if skey in value and _as_bool(value[skey]) != before:
                write_setting(self.manifest, skey, bool(value[skey]))
                self.values = read_values(self.manifest)
                after = _as_bool(self.values.get(skey, False))
                if after:
                    from . import plugin_service as _psvc
                    ok, msg = _psvc.start_service(self.manifest)
                    self._last_service_ok = ok
                    self._last_service_note = f"Service started: {msg}" if ok else f"Service start failed: {msg}"
                else:
                    from . import plugin_service as _psvc
                    ok, msg = _psvc.stop_service(self.manifest)
                    self._last_service_ok = ok
                    self._last_service_note = f"Service stopped: {msg}" if ok else f"Service stop failed: {msg}"
            elif skey in value:
                self.values = read_values(self.manifest)
        if written:
            self.values = read_values(self.manifest)
            self._done = True


def run_settings_tui(manifest: dict, title: str = "") -> int:
    """Terminal surface: declarative settings flow (TUI or piped)."""
    from . import xui as _xui
    return _xui.run_cli(SettingsApp(manifest, title=title))