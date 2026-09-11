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
    """System-wide Sayri root (/usr/share/sayri on Linux); on macOS/Windows it
    resolves relative to the installed package so plugins/sounds are found in
    the app bundle."""
    if is_linux():
        return "/usr/share/sayri"
    lib = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # …/lib
    return os.path.dirname(lib)


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


def process_alive(pid: int) -> bool:
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