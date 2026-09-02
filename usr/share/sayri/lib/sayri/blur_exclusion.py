"""Keep Sayri's windows out of the compositor blur.

Pulsar OS uses the GNOME Shell extension "blur-my-shell", which blurs
application windows. Overlay windows such as the orb / cajita should stay
crisp, so we:

  1. Set a distinctive WM_CLASS / prgname ("sayri") so compositors can match it.
  2. Add "sayri", "Sayri", "es.inled.sayri" to blur-my-shell's application
     blacklist in org.gnome.shell.extensions.blur-my-shell.applications.
  3. When available, gtk4-layer-shell makes the surfaces override-redirect /
     layer-shell surfaces, which compositors do not window-manage or blur.

All of this is best-effort and never fails the app startup.
"""

from __future__ import annotations

import os

from gi.repository import Gio, GLib

BLS_APPS_SCHEMA = "org.gnome.shell.extensions.blur-my-shell.applications"
ENTRIES_TO_BLACKLIST = [
    "sayri", "Sayri", "es.inled.sayri", "sayri-overlay",
    "sayri-indicator", "sayri-tray", "*sayri*", "*Sayri*"
]

__all__ = ["apply_blur_exclusion"]


def _set_wm_class(sayri: str = "sayri") -> None:
    """Make compositors / WM match our windows as 'sayri'."""
    try:
        GLib.set_prgname(sayri)
    except Exception:  # noqa: BLE001
        pass


def _add_blacklist(entries: list[str] | None = None) -> bool:
    """Add entries to blur-my-shell applications blacklist via Gio.Settings.

    Returns True if the setting was updated, False if the schema is missing
    (blur-my-shell not installed / not enabled).
    """
    entries = entries or ENTRIES_TO_BLACKLIST
    try:
        source = Gio.SettingsSchemaSource.get_default()
        if source is None or not source.lookup(BLS_APPS_SCHEMA, True):
            return False
        settings = Gio.Settings.new(BLS_APPS_SCHEMA)
        blacklist = list(settings.get_strv("blacklist") or [])
        changed = False
        for entry in entries:
            if entry not in blacklist:
                blacklist.append(entry)
                changed = True
        if changed:
            settings.set_strv("blacklist", blacklist)
            settings.sync()
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[sayri] warning: could not update blur blacklist: {exc}")
        return False


def apply_blur_exclusion() -> None:
    """Best-effort exclusion of Sayri from compositor blur."""
    _set_wm_class()
    if os.environ.get("SAYRI_SKIP_BLUR_LIST") == "1":
        return
    try:
        added = _add_blacklist()
        print("[sayri] blur-my-shell blacklist updated"
              if added else
              "[sayri] blur-my-shell not present; layer-shells are excluded automatically")
    except Exception as exc:  # noqa: BLE001
        print(f"[sayri] warning: blur exclusion not available: {exc}")