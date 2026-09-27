"""Platform / OS abstraction (hexagonal adapter layer).

Central place for everything that differs between Linux, macOS and Windows so
the headless core stays OS-agnostic. Behaviour on Linux is identical to the
previously hard-coded values; other OSes get native defaults that can always
be overridden through the ``SAYRI_*`` environment variables used by
:mod:`sayri.paths`.
"""

from __future__ import annotations

import os
import platform
import shutil
import signal
import subprocess
import sys
import time
from typing import Optional

_OS = platform.system().lower()


def os_name() -> str:
    """Normalized OS name: 'linux', 'macos', 'windows' or 'other'."""
    return {
        "linux": "linux",
        "darwin": "macos",
        "windows": "windows",
    }.get(_OS, sys.platform or _OS)


def is_linux() -> bool:
    return os_name() == "linux"


def is_macos() -> bool:
    return os_name() == "macos"


def is_windows() -> bool:
    return os_name() == "windows"


def cmd_exists(name: str) -> bool:
    return shutil.which(name) is not None


def default_config_dir() -> str:
    """~/.config/sayri on Linux, ~/Library/Application Support/sayri on macOS,
    %APPDATA%/sayri on Windows."""
    if is_windows():
        base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    elif is_macos():
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.path.expanduser("~/.config")
    return os.path.join(base, "sayri")


def default_state_dir() -> str:
    """~/.local/share/sayri on Linux, ~/Library/Application Support/sayri on
    macOS, %LOCALAPPDATA%/sayri on Windows."""
    if is_windows():
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    elif is_macos():
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.path.expanduser("~/.local/share")
    return os.path.join(base, "sayri")


def shared_root_dir() -> str:
    """System-wide Sayri root (/usr/share/sayri on Linux); on macOS/Windows or
    in a source checkout it resolves relative to the package so plugins/sounds
    are found seamlessly."""
    if os.environ.get("SAYRI_SHARED_DIR"):
        return os.environ["SAYRI_SHARED_DIR"]
    lib = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # …/lib
    pkg_root = os.path.dirname(lib)  # …/usr/share/sayri
    if os.path.isdir(os.path.join(pkg_root, "plugins")) or os.path.isdir(os.path.join(pkg_root, "web")):
        return pkg_root
    if is_linux() and os.path.isdir("/usr/share/sayri"):
        return "/usr/share/sayri"
    return pkg_root


def default_data_dir() -> str:
    """Default web build root: /usr/share/sayri/web on Linux, otherwise the
    sibling 'web' directory of the installed package."""
    return os.path.join(shared_root_dir(), "web")


def find_sayri_command() -> Optional[str]:
    """Absolute path of the 'sayri' launcher, or None if not on PATH."""
    return shutil.which("sayri")


# --------------------------------------------------------------------------
# Process helpers (portable subprocess / signal handling)
# --------------------------------------------------------------------------
def spawn_flags() -> dict:
    """Keyword arguments that detach a background process from the current
    session: ``start_new_session=True`` on POSIX, a new process group on
    Windows."""
    if is_windows():
        flags = subprocess.CREATE_NEW_PROCESS_GROUP
        if hasattr(subprocess, "DETACHED_PROCESS"):
            flags |= subprocess.DETACHED_PROCESS
        return {"creationflags": flags}
    return {"start_new_session": True}


def send_signal(pid: int, sig: int) -> None:
    """Send a signal to a process. POSIX uses os.kill; Windows falls back to
    taskkill since Windows signals are console-only."""
    if is_windows():
        cmd = ["taskkill", "/PID", str(pid), "/T", "/F"]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        os.kill(pid, sig)

def terminate_pid(pid: int) -> None:
    send_signal(pid, signal.SIGTERM)


def is_pid_alive(pid: int) -> bool:
    """Cheap aliveness probe (signal 0). Windows has no POSIX signals, so a
    failed tasklist check means the process is gone."""
    if is_windows():
        return subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True,
            text=True,
            check=False,
        ).returncode == 0
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# Historical name kept so both spellings work across the tree.
process_alive = is_pid_alive


def kill_process_tree(pattern: Optional[str] = None, pid: Optional[int] = None) -> None:
    """Best-effort kill of Sayri processes.

    POSIX: ``pkill -9 -f <pattern>``, excluding the calling process itself so
    ``sayri daemon restart`` cannot commit suicide through its own command
    line. Windows: ``taskkill /IM`` for matching image names or ``/PID`` for a
    pid.
    """
    if is_windows():
        if pid is not None:
            send_signal(pid, 0)
        elif pattern:
            image = os.path.basename(pattern.split()[0].replace("/", os.sep))
            subprocess.run(["taskkill", "/IM", image, "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        return
    if pattern and pid is None:
        own = os.getpid()
        try:
            out = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True,
                                 check=False).stdout
        except OSError:
            return
        for line in out.splitlines():
            line = line.strip()
            if not line.isdigit() or int(line) == own:
                continue
            try:
                os.kill(int(line), signal.SIGKILL)
            except OSError:
                pass


def _state_dir() -> str:
    """State dir without importing sayri.paths (keeps this module dependency-free)."""
    override = os.environ.get("SAYRI_STATE_DIR")
    if override:
        return override
    try:
        from .paths import state_dir
        return str(state_dir())
    except Exception:  # noqa: BLE001
        return os.path.join(os.path.expanduser("~"), ".local", "share", "sayri")


def stop_ui_plugins(skip_ids: Optional[set[str]] = None) -> list[str]:
    """Terminate every running Sayri UI plugin and return the ids signalled.

    UI plugins are detached background apps (the GTK orb, the classic
    companions, any custom UI) that each own a ``<id>.pid`` file in the state
    dir. Nothing in the core shutdown path used to know about them, so they
    survived "Exit" in the appindicator and ``sayri killall`` and kept floating
    on the desktop. This is the single place both paths call.

    ``skip_ids`` keeps the built-in orb alive when only the third-party plugins
    should be stopped.
    """
    skip = {s for s in (skip_ids or set()) if s}
    state = _state_dir()
    stopped: list[str] = []
    try:
        entries = list(os.scandir(state))
    except OSError:
        return stopped

    for entry in entries:
        name = entry.name
        if not name.endswith(".pid"):
            continue
        ui_id = name[:-4]
        if ui_id in skip or ui_id.startswith("sayri-daemon"):
            continue
        pid = None
        try:
            pid = int(open(entry.path, encoding="utf-8").read().strip())
        except (OSError, ValueError):
            pass
        if pid and process_alive(pid) and pid != os.getpid():
            try:
                terminate_pid(pid)
            except OSError:
                pid = None  # it died between the probe and the signal
            if pid is not None:
                # Give the plugin a moment to tear its window down, then
                # insist. A UI that ignores SIGTERM would otherwise survive
                # the very command meant to close it.
                for _ in range(20):
                    if not process_alive(pid):
                        break
                    time.sleep(0.1)
                if process_alive(pid):
                    try:
                        send_signal(pid, signal.SIGKILL)
                    except OSError:
                        pass
                stopped.append(ui_id)
        # The pid file is stale either way: the process is gone or we just
        # asked it to leave, so `sayri ui start` can launch it again.
        try:
            os.unlink(entry.path)
        except OSError:
            pass
    return stopped


def get_primary_geometry_gnome() -> tuple[int, int, int, int] | None:
    """Query GNOME Mutter DBus for the configured primary monitor (x, y, width, height)."""
    try:
        from gi.repository import Gio
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        proxy = Gio.DBusProxy.new_sync(
            bus,
            Gio.DBusProxyFlags.NONE,
            None,
            "org.gnome.Mutter.DisplayConfig",
            "/org/gnome/Mutter/DisplayConfig",
            "org.gnome.Mutter.DisplayConfig",
            None,
        )
        state = proxy.GetCurrentState()
        mode_map = {}
        for out in state[1]:
            name = out[0][0]
            modes = out[1]
            for m in modes:
                if len(m) > 6 and m[6].get("is-current", False):
                    mode_map[name] = (m[1], m[2])
                    break

        for mon in state[2]:
            if mon[4]:  # primary == True
                lx, ly, lscale = int(mon[0]), int(mon[1]), float(mon[2])
                outputs = mon[5]
                w, h = 1920, 1080
                if outputs and len(outputs) > 0 and len(outputs[0]) > 0:
                    conn_name = str(outputs[0][0])
                    if conn_name in mode_map:
                        mw, mh = mode_map[conn_name]
                        w = int(mw / lscale) if lscale > 0 else mw
                        h = int(mh / lscale) if lscale > 0 else mh
                return (lx, ly, w, h)
    except Exception:
        pass
    return None


def ensure_indicator() -> None:
    """Ensure sayri-indicator is running in background for tray access."""
    try:
        out = subprocess.run(["pgrep", "-f", "sayri.indicator|sayri-indicator"], capture_output=True, text=True, check=False).stdout
        if out.strip():
            return
    except Exception:
        pass
    try:
        cmd = ["sayri-indicator"] if shutil.which("sayri-indicator") else [sys.executable, "-m", "sayri.indicator"]
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **spawn_flags())
    except Exception:
        pass