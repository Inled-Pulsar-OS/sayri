"""Autostart management for Sayri (Linux .desktop, macOS LaunchAgent,
Windows Run key).

What starts depends on ``ui.autostart_mode``:

* ``ui``     -> starts the configured default UI, and the launcher also makes
                sure the headless daemon is running.
* ``daemon`` -> only the headless daemon (no UI on screen).
"""

from __future__ import annotations

import os
import sys

from . import paths, sysinfo


def _exec_cmd(mode: str) -> tuple[str, list[str]]:
    """Return (binary, args) that start Sayri in the requested mode."""
    sayri = sysinfo.find_sayri_command()
    if sayri:
        binary = sayri
        args = ["daemon"] if mode == "daemon" else ["--launch-ui", "--autostart"]
    else:
        binary = sys.executable
        args = ["-m", "sayri", "daemon"] if mode == "daemon" else ["-m", "sayri", "--launch-ui", "--autostart"]
    return binary, args


def _resolve_mode(cfg) -> str:
    try:
        mode = (cfg.get_string("ui", "autostart_mode") or "ui").strip().lower()
    except Exception:  # noqa: BLE001
        mode = "ui"
    return "daemon" if mode == "daemon" else "ui"


def _enabled(cfg) -> bool:
    try:
        return bool(cfg.get_bool("ui", "autostart"))
    except Exception:  # noqa: BLE001
        return True


def apply_autostart(cfg) -> str | None:
    """Create/refresh/remove the autostart entry; returns its path or None."""
    enabled = _enabled(cfg)
    if sysinfo.is_windows():
        return _apply_windows(enabled, cfg)
    if sysinfo.is_macos():
        return _apply_macos(enabled, cfg)
    return _apply_linux(enabled, cfg)


# ---------------------------------------------------------------- Linux
def _apply_linux(enabled: bool, cfg) -> str | None:
    autostart_dir = os.path.join(os.path.expanduser("~"), ".config", "autostart")
    autostart_file = os.path.join(autostart_dir, "sayri.desktop")
    if not enabled:
        try:
            if os.path.exists(autostart_file):
                os.remove(autostart_file)
        except OSError:
            pass
        return None

    binary, args = _exec_cmd(_resolve_mode(cfg))
    exec_line = f'"{binary}" {" ".join(args)}'
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Sayri\n"
        "Comment=Sayri voice assistant\n"
        f"Exec={exec_line}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )
    os.makedirs(autostart_dir, exist_ok=True)
    with open(autostart_file, "w", encoding="utf-8") as f:
        f.write(content)
    return autostart_file


# ---------------------------------------------------------------- macOS
def _apply_macos(enabled: bool, cfg) -> str | None:
    agents_dir = os.path.join(os.path.expanduser("~"), "Library", "LaunchAgents")
    plist_file = os.path.join(agents_dir, "com.sayri.agent.plist")
    if not enabled:
        try:
            if os.path.exists(plist_file):
                os.remove(plist_file)
        except OSError:
            pass
        return None

    binary, args = _exec_cmd(_resolve_mode(cfg))
    path_items = "\n".join(f"      <string>{_plist_escape(a)}</string>" for a in [binary] + args)
    log_dir = os.path.join(paths.state_dir(), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "autostart.log")

    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        '<dict>\n'
        "  <key>Label</key>\n"
        "  <string>com.sayri.agent</string>\n"
        "  <key>ProgramArguments</key>\n"
        "  <array>\n"
        f"{path_items}\n"
        "  </array>\n"
        "  <key>RunAtLoad</key>\n"
        "  <true/>\n"
        "  <key>KeepAlive</key>\n"
        "  <false/>\n"
        "  <key>ProcessType</key>\n"
        "  <string>Background</string>\n"
        "  <key>StandardOutPath</key>\n"
        f"  <string>{_plist_escape(log_path)}</string>\n"
        "  <key>StandardErrorPath</key>\n"
        f"  <string>{_plist_escape(log_path)}</string>\n"
        "</dict>\n"
        "</plist>\n"
    )
    os.makedirs(agents_dir, exist_ok=True)
    with open(plist_file, "w", encoding="utf-8") as f:
        f.write(content)
    return plist_file


def _plist_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


# -------------------------------------------------------------- Windows
def _apply_windows(enabled: bool, cfg) -> str | None:
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        import winreg
    except Exception:  # noqa: BLE001
        return None
    try:
        key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
        try:
            if not enabled:
                try:
                    winreg.DeleteValue(key, "Sayri")
                except OSError:
                    pass
                return None
            binary, args = _exec_cmd(_resolve_mode(cfg))
            winreg.SetValueEx(key, "Sayri", 0, winreg.REG_SZ, f'"{binary}" {" ".join(args)}')
            return rf"HKCU\{key_path}\Sayri"
        finally:
            winreg.CloseKey(key)
    except OSError:
        return None


if __name__ == "__main__":
    from sayri import config
    print(apply_autostart(config.config))