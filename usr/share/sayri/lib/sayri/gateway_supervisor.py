"""Sayri Gateway Supervisor (Multi-Instance Channel Architecture).

Manages multi-instance background gateway daemons (Telegram, Discord, MCP),
allowing multiple gateways of the same platform bound to different AI Agents
and fine-grained Sandbox Levels with zero-plaintext Vault credential injection.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sayri.domain.secrets_manager import secrets_manager

INSTANCES_FILE = Path.home() / ".config" / "sayri" / "gateway_instances.json"


class GatewaySupervisor:
    """Manages lifecycle of multi-instance out-of-process channel gateways."""

    _instance: Optional[GatewaySupervisor] = None
    _processes: Dict[str, subprocess.Popen] = {}

    @classmethod
    def get_instance(cls) -> GatewaySupervisor:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def _get_search_dirs() -> List[Path]:
        return [
            Path.home() / ".config" / "sayri" / "skills",
            Path.home() / ".config" / "sayri" / "plugins",
            Path("/usr/share/sayri/plugins"),
        ]

    def list_installed_plugins(self) -> List[Dict[str, Any]]:
        """Scans filesystem for installed Gateway plugins and their manifests."""
        plugins = []
        seen = set()

        for base in self._get_search_dirs():
            if not base.is_dir():
                continue
            for sub in base.iterdir():
                if sub.is_dir() and (sub / "manifest.json").is_file():
                    try:
                        m = json.loads((sub / "manifest.json").read_text(encoding="utf-8"))
                        auth = m.get("authorization") if isinstance(m.get("authorization"), dict) else {}
                        auth_mode = auth.get("mode", "none")
                        if not m.get("entrypoint") and auth_mode == "none":
                            continue
                        pid = m.get("id", sub.name)
                        if pid not in seen:
                            seen.add(pid)
                            plugins.append({
                                "id": pid,
                                "name": m.get("name", pid),
                                "description": m.get("description", ""),
                                "version": m.get("version", "1.0.0"),
                                "auth_mode": auth_mode,
                                "required_secrets": m.get("required_secrets", []),
                                "sync_instructions": m.get("ui", {}).get("sync_instructions", ""),
                                "chat_url": m.get("ui", {}).get("chat_url", ""),
                                "path": sub,
                            })
                    except Exception:
                        pass
        return plugins

    def find_gateway_plugin_dir(self, plugin_id: str) -> Optional[Path]:
        for base in self._get_search_dirs():
            target = base / plugin_id
            if target.is_dir() and (target / "manifest.json").is_file():
                return target
        return None

    def list_instances(self) -> List[Dict[str, Any]]:
        """Returns all configured gateway instances, bootstrapping defaults if none exist."""
        INSTANCES_FILE.parent.mkdir(parents=True, exist_ok=True)
        instances: List[Dict[str, Any]] = []

        if INSTANCES_FILE.is_file():
            try:
                data = json.loads(INSTANCES_FILE.read_text(encoding="utf-8"))
                instances = data.get("instances", [])
            except Exception as e:
                print(f"[Supervisor] Error loading instances: {e}", file=sys.stderr)

        # Bootstrap initial instance if file is empty
        if not instances:
            installed = self.list_installed_plugins()
            for p in installed:
                default_sec = p["required_secrets"][0] if p["required_secrets"] else ""
                inst = {
                    "id": p["id"],
                    "name": p["name"],
                    "plugin_id": p["id"],
                    "agent_id": "default",
                    "sandbox_level": "LEVEL_1_READONLY",
                    "secret_key": default_sec,
                    "auth_mode": p.get("auth_mode", "pairing_otp"),
                    "enabled": True,
                    "created_at": time.time(),
                }
                instances.append(inst)
            self._save_instances_to_disk(instances)

        return instances

    def _save_instances_to_disk(self, instances: List[Dict[str, Any]]) -> None:
        try:
            INSTANCES_FILE.parent.mkdir(parents=True, exist_ok=True)
            payload = {"version": 1, "instances": instances}
            INSTANCES_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[Supervisor] Error saving instances to disk: {e}", file=sys.stderr)

    def get_instance_config(self, instance_id: str) -> Optional[Dict[str, Any]]:
        for inst in self.list_instances():
            if inst["id"] == instance_id:
                return inst
        return None

    def save_instance(self, instance_data: Dict[str, Any]) -> None:
        """Creates or updates a gateway instance."""
        instances = self.list_instances()
        idx = next((i for i, item in enumerate(instances) if item["id"] == instance_data["id"]), -1)
        if idx >= 0:
            instances[idx] = instance_data
        else:
            instances.append(instance_data)
        self._save_instances_to_disk(instances)

    def delete_instance(self, instance_id: str) -> None:
        """Stops and deletes a gateway instance."""
        self.stop_instance(instance_id)
        instances = [i for i in self.list_instances() if i["id"] != instance_id]
        self._save_instances_to_disk(instances)

    def is_instance_running(self, instance_id: str) -> bool:
        proc = self._processes.get(instance_id)
        if proc:
            if proc.poll() is None:
                return True
            else:
                self._processes.pop(instance_id, None)
        return False

    def is_running(self, gw_id: str) -> bool:
        """Backward compatibility helper."""
        return self.is_instance_running(gw_id)

    def start_instance(self, instance_id: str) -> Tuple[bool, str]:
        """Starts a specific gateway instance with custom bound agent and sandbox level."""
        inst = self.get_instance_config(instance_id)
        if not inst:
            return False, f"Gateway instance '{instance_id}' not found."

        plugin_dir = self.find_gateway_plugin_dir(inst.get("plugin_id", instance_id))
        if not plugin_dir:
            return False, f"Plugin '{inst.get('plugin_id')}' not found on system."

        manifest_file = plugin_dir / "manifest.json"
        try:
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        except Exception as e:
            return False, f"Failed to read manifest.json: {e}"

        entrypoint = plugin_dir / manifest.get("entrypoint", "gateway.py")
        if not entrypoint.is_file():
            return False, f"Entrypoint '{entrypoint.name}' not found."

        # Stop existing instance process if running
        self.stop_instance(instance_id)

        # Prepare instance environment with Vault secrets dynamically from manifest
        env = os.environ.copy()
        sec_key = inst.get("secret_key")
        required_secrets = manifest.get("required_secrets", [])

        if sec_key:
            secret_val = secrets_manager.get_secret(sec_key)
            if secret_val:
                env[sec_key] = secret_val
                # Inject dynamically for any required secrets declared in the plugin manifest
                for req_sec in required_secrets:
                    env[req_sec] = secret_val
            elif sec_key not in env:
                # Fallback check directly in vault for declared secrets
                found = False
                for req_sec in required_secrets:
                    val = secrets_manager.get_secret(req_sec) or env.get(req_sec)
                    if val:
                        env[req_sec] = val
                        found = True
                if not found and required_secrets:
                    return False, f"Required secret for '{manifest.get('name', instance_id)}' is missing in Zero-Plaintext Vault."
        elif required_secrets:
            for req_sec in required_secrets:
                val = secrets_manager.get_secret(req_sec) or env.get(req_sec)
                if val:
                    env[req_sec] = val

        # Bind instance configuration
        env["SAYRI_GATEWAY_INSTANCE_ID"] = instance_id
        env["SAYRI_TARGET_AGENT"] = inst.get("agent_id", "default")
        env["SAYRI_SANDBOX_LEVEL"] = inst.get("sandbox_level", "LEVEL_1_READONLY")
        env["SAYRI_PID_FILE"] = str(Path.home() / ".config" / "sayri" / f"gateway_{instance_id}.pid")
        env["SAYRI_AUTH_FILE"] = str(Path.home() / ".config" / "sayri" / f"authorizations_{instance_id}.json")
        env["SAYRI_PIN_FILE"] = str(Path.home() / ".config" / "sayri" / f"pairing_pin_{instance_id}.json")

        sayri_lib = Path(__file__).resolve().parent
        env["PYTHONPATH"] = f"{sayri_lib.parent}:{env.get('PYTHONPATH', '')}"
        env["PYTHONUNBUFFERED"] = "1"

        try:
            log_dir = Path.home() / ".local" / "share" / "sayri" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / f"{instance_id}.log"
            log_handle = open(log_file, "a", encoding="utf-8")

            proc = subprocess.Popen(
                [sys.executable, "-u", str(entrypoint)],
                cwd=str(plugin_dir),
                env=env,
                stdout=log_handle,
                stderr=log_handle,
                start_new_session=True,
            )
            self._processes[instance_id] = proc
            print(f"[Supervisor] 🚀 Started gateway instance '{inst.get('name')}' ({instance_id}, PID: {proc.pid}) -> Agent: {inst.get('agent_id')}, Sandbox: {inst.get('sandbox_level')}")
            return True, f"Started gateway instance '{inst.get('name')}' (PID: {proc.pid})"
        except Exception as e:
            return False, f"Failed to spawn gateway instance: {e}"

    def start_gateway(self, gw_id: str) -> Tuple[bool, str]:
        """Backward compatibility helper."""
        return self.start_instance(gw_id)

    def stop_instance(self, instance_id: str) -> None:
        """Stops a running gateway instance."""
        proc = self._processes.pop(instance_id, None)
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            print(f"[Supervisor] ⏹️ Stopped gateway instance '{instance_id}'")

        # Also cleanup PID file process if exists
        pid_file = Path.home() / ".config" / "sayri" / f"gateway_{instance_id}.pid"
        if pid_file.is_file():
            try:
                old_pid = int(pid_file.read_text().strip())
                if old_pid != os.getpid():
                    try:
                        os.kill(old_pid, signal.SIGTERM)
                    except OSError:
                        pass
            except Exception:
                pass

    def stop_gateway(self, gw_id: str) -> None:
        """Backward compatibility helper."""
        self.stop_instance(gw_id)

    def auto_start_all(self) -> None:
        """Auto-starts all enabled gateway instances with present credentials."""
        for inst in self.list_instances():
            if not inst.get("enabled", True):
                continue
            sec_key = inst.get("secret_key", "")
            if sec_key:
                has_sec = bool(secrets_manager.get_secret(sec_key) or sec_key in os.environ)
                if has_sec:
                    self.start_instance(inst["id"])


gateway_supervisor = GatewaySupervisor.get_instance()
