"""System Event Triggers Manager for Sayri / Pulsar OS."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Dict, List, Optional

from sayri import paths


class TriggerEngine:
    """Dispatches background tasks and routines on system events (login, schedule, battery)."""

    def __init__(self) -> None:
        self.triggers_dir = paths.triggers_dir()
        os.makedirs(self.triggers_dir, exist_ok=True)

    def list_triggers(self) -> List[Dict[str, Any]]:
        results = []
        for filename in sorted(os.listdir(self.triggers_dir)):
            if not filename.endswith(".json"):
                continue
            try:
                with open(os.path.join(self.triggers_dir, filename), "r", encoding="utf-8") as f:
                    data = json.load(f)
                    results.append(data)
            except Exception:
                pass
        return results

    def add_trigger(
        self,
        trigger_id: str,
        event_type: str,  # "on_login", "on_schedule", "on_battery_low"
        prompt: str,
        target_agent_id: str = "default",
        cron_expr: Optional[str] = None,
    ) -> str:
        payload = {
            "id": trigger_id,
            "event_type": event_type,
            "prompt": prompt,
            "target_agent_id": target_agent_id,
            "cron_expr": cron_expr,
            "created_at": time.time(),
            "enabled": True,
        }
        fpath = os.path.join(self.triggers_dir, f"{trigger_id}.json")
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        return fpath

    def run_on_login_triggers(self, callback: Callable[[str, str], None]) -> None:
        """Executes all enabled on_login triggers at system startup."""
        for t in self.list_triggers():
            if t.get("enabled", False) and t.get("event_type") == "on_login":
                agent_id = t.get("target_agent_id", "default")
                prompt = t.get("prompt", "")
                if prompt:
                    print(f"[Triggers] ⚡ Executing on_login trigger: {t.get('id')} -> {prompt}")
                    callback(agent_id, prompt)
