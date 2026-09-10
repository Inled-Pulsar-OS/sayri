"""Autostart desktop file management for Sayri.

The UI launcher writes ``~/.config/autostart/sayri.desktop`` so that Sayri
starts at login.  What starts depends on ``ui.autostart_mode``:

* ``ui``     -> ``sayri --launch-ui``: starts the configured default UI, and
                the launcher also makes sure the headless daemon is running.
* ``daemon`` -> ``sayri daemon``: only the headless daemon (no UI on screen).
"""

from __future__ import annotations

import os

AUTOSTART_DIR = os.path.expanduser("~/.config/autostart")
AUTOSTART_FILE = os.path.join(AUTOSTART_DIR, "sayri.desktop")


def apply_autostart(cfg) -> str | None:
    """Create/refresh/remove the autostart entry; returns its path or None."""
    try:
        enabled = cfg.get_bool("ui", "autostart")
    except Exception:  # noqa: BLE001
        enabled = True
    if not enabled:
        try:
            if os.path.exists(AUTOSTART_FILE):
                os.remove(AUTOSTART_FILE)
        except OSError:
            pass
        return None

    try:
        mode = (cfg.get_string("ui", "autostart_mode") or "ui").strip().lower()
    except Exception:  # noqa: BLE001
        mode = "ui"
    exec_line = "/usr/bin/sayri daemon" if mode == "daemon" else "/usr/bin/sayri --launch-ui --autostart"

    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Sayri\n"
        "Comment=Sayri voice assistant orb\n"
        f"Exec={exec_line}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )
    os.makedirs(AUTOSTART_DIR, exist_ok=True)
    with open(AUTOSTART_FILE, "w", encoding="utf-8") as f:
        f.write(content)
    return AUTOSTART_FILE