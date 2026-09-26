"""Plugin-run background services (manifest ``service`` block).

A plugin can declare that it owns a background process (e.g. Prism ML's
``llama-server``) with explicit commands::

    "service": {
      "auto_start": true,
      "enabled": true,
      "enabled_key": "enabled",
      "start":  ["gateway.py", "start"],
      "stop":   ["gateway.py", "stop"],
      "status": ["gateway.py", "status"]
    }

The enable/disable flag is persisted in the plugin's own settings file (the
same one the declarative form writes, see ``plugin_settings.settings_file_path``)
so the TUI, the GTK settings window, the daemon boot and the headless CLI all
honour one single value. Commands are run as ``sys.executable`` with the plugin
directory as ``cwd`` (mirroring how the reste of Sayri drives plugin scripts).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from . import plugin_settings
from .gateway_supervisor import gateway_supervisor

__all__ = [
    "service_block", "enabled_key", "service_enabled", "set_service_enabled",
    "plugin_dir", "run_service_command", "service_running",
    "start_service", "stop_service", "auto_start_services",
]


# ------------------------------------------------------------- introspection
def service_block(manifest: dict) -> Optional[dict]:
    """The plugin's ``service`` block, or None when it has no managed service."""
    svc = manifest.get("service")
    if isinstance(svc, dict) and (svc.get("start") or svc.get("status")):
        return svc
    return None


def enabled_key(manifest: dict) -> str:
    """Which config key carries the enable/disable flag."""
    svc = service_block(manifest)
    if not svc:
        return ""
    return str(svc.get("enabled_key") or "enabled")


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in ("1", "true", "yes", "on", "si")


def service_enabled(manifest: dict) -> bool:
    """Read the persisted enable flag (defaults to the manifest default)."""
    svc = service_block(manifest)
    if not svc:
        return False
    val = plugin_settings.read_values(manifest).get(enabled_key(manifest))
    if val is None:
        val = svc.get("enabled", svc.get("auto_start", True))
    return _as_bool(val)


def set_service_enabled(manifest: dict, value: bool) -> bool:
    """Persist the enable flag in the plugin's config file."""
    if not service_block(manifest):
        return False
    return plugin_settings.write_setting(manifest, enabled_key(manifest), bool(value))


# ----------------------------------------------------------------- commands
def plugin_dir(manifest: dict) -> Optional[Path]:
    """Directory that contains the plugin's entrypoint scripts."""
    for pl in gateway_supervisor.list_installed_plugins():
        if pl.get("id") != manifest.get("id") or not pl.get("path"):
            continue
        p = Path(pl["path"]).expanduser()
        return p if p.is_dir() else p.parent
    return None


def _command(manifest: dict, which: str) -> Optional[list[str]]:
    svc = service_block(manifest)
    if not svc:
        return None
    cmd = svc.get(which)
    if not isinstance(cmd, (list, tuple)) or not cmd:
        return None
    return [str(c) for c in cmd]


def run_service_command(manifest: dict, which: str, timeout: int = 45) -> tuple[int, str]:
    """Run one of the service commands (start/stop/status) synchronously."""
    cmd = _command(manifest, which)
    d = plugin_dir(manifest)
    if not cmd or d is None:
        return 1, "not configured for this plugin"
    full = [sys.executable] + cmd
    try:
        res = subprocess.run(full, cwd=str(d), capture_output=True, text=True,
                             timeout=timeout)
        text = "\n".join(x.strip() for x in (res.stdout or "", res.stderr or "") if x.strip())
        return res.returncode, text
    except subprocess.TimeoutExpired:
        return 1, "timed out"
    except Exception as exc:  # noqa: BLE001
        return 1, f"failed to run: {exc}"


def service_running(manifest: dict) -> bool:
    """Best-effort running state, parsed from the ``status`` command output."""
    rc, text = run_service_command(manifest, "status", timeout=20)
    low = text.lower()
    if "stopped" in low:
        return False
    if "running" in low:
        return True
    return rc == 0


# ------------------------------------------------------------ start / stop
def start_service(manifest: dict) -> tuple[bool, str]:
    """Persist enabled=True and start the service now."""
    if not service_block(manifest):
        return False, "plugin has no managed service"
    set_service_enabled(manifest, True)
    rc, text = run_service_command(manifest, "start")
    ok = rc == 0 or service_running(manifest)
    return ok, text or ("started" if ok else "failed")


def stop_service(manifest: dict) -> tuple[bool, str]:
    """Persist enabled=False and stop the service now."""
    if not service_block(manifest):
        return False, "plugin has no managed service"
    set_service_enabled(manifest, False)
    rc, text = run_service_command(manifest, "stop")
    return rc == 0, text or ("stopped" if rc == 0 else "failed")


def restart_service(manifest: dict) -> tuple[bool, str]:
    """Stop then start the service, keeping the enable flag on."""
    svc = service_block(manifest)
    if not svc:
        return False, "plugin has no managed service"
    run_service_command(manifest, "stop")
    return start_service(manifest)


# ---------------------------------------------------------------- auto start
def auto_start_services() -> None:
    """Start every installed plugin service that is auto-start and enabled."""
    seen = 0
    for pl in gateway_supervisor.list_installed_plugins():
        path = pl.get("path")
        if not path:
            continue
        mpath = Path(path).expanduser()
        mpath = mpath if mpath.is_file() else mpath / "manifest.json"
        if not mpath.is_file():
            continue
        try:
            manifest = json.loads(mpath.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        svc = service_block(manifest)
        if not svc or not svc.get("auto_start", True):
            continue
        if not service_enabled(manifest):
            continue
        if service_running(manifest):
            continue
        seen += 1
        try:
            ok, text = start_service(manifest)
            print(f"[sayri] service {manifest.get('id', pl.get('id'))}: "
                  f"{'started' if ok else text}")
        except Exception as exc:  # noqa: BLE001
            print(f"[sayri] service start notice for {manifest.get('id', pl.get('id'))}: {exc}")
    if seen:
        print(f"[sayri] auto-started {seen} plugin service(s)")