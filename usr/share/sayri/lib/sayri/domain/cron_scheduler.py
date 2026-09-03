"""Automated Routines and Cron Job Scheduler for Sayri / Pulsar OS."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

ROUTINES_FILE = Path.home() / ".config" / "sayri" / "routines.json"


@dataclass
class Routine:
    id: str
    name: str
    description: str
    trigger: str  # "on_login", "daily_at", "hourly", "cron"
    time_spec: str = "09:00"  # HH:MM for daily_at, or interval in hours
    prompt: str = ""
    agent_id: str = "default"
    speak_tts: bool = True
    notify_desktop: bool = True
    enabled: bool = True
    last_run: float = 0.0
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CronScheduler:
    """Manages automated tasks, daily briefings, and cron events in Sayri."""

    def __init__(self, app: Any = None):
        self.app = app
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._routines: List[Routine] = []
        self._load_routines()

    def _load_routines(self) -> None:
        self._routines = []
        if not ROUTINES_FILE.is_file():
            # Seed default routines (Morning Briefing)
            default_routines = [
                Routine(
                    id="morning_briefing",
                    name="Morning News & Weather",
                    description="Speaks morning news and weather update when logging into Pulsar OS",
                    trigger="on_login",
                    time_spec="09:00",
                    prompt="Buenos días. Dame un breve resumen de 2 frases con un saludo matutino y las noticias de hoy.",
                    agent_id="default",
                    speak_tts=True,
                    notify_desktop=True,
                    enabled=False,
                ),
                Routine(
                    id="hourly_break",
                    name="Hourly Posture & Rest Reminder",
                    description="Reminds to hydrate and take a short stretch every 2 hours",
                    trigger="hourly",
                    time_spec="2",
                    prompt="Recuérdame amablemente en una frase corta que descanse la vista y beba agua.",
                    agent_id="default",
                    speak_tts=True,
                    notify_desktop=True,
                    enabled=False,
                ),
            ]
            self._save_routines(default_routines)
            self._routines = default_routines
            return

        try:
            data = json.loads(ROUTINES_FILE.read_text(encoding="utf-8"))
            for r_data in data.get("routines", []):
                self._routines.append(
                    Routine(
                        id=r_data.get("id", f"routine_{int(time.time())}"),
                        name=r_data.get("name", "Custom Routine"),
                        description=r_data.get("description", ""),
                        trigger=r_data.get("trigger", "daily_at"),
                        time_spec=r_data.get("time_spec", "09:00"),
                        prompt=r_data.get("prompt", ""),
                        agent_id=r_data.get("agent_id", "default"),
                        speak_tts=bool(r_data.get("speak_tts", True)),
                        notify_desktop=bool(r_data.get("notify_desktop", True)),
                        enabled=bool(r_data.get("enabled", True)),
                        last_run=float(r_data.get("last_run", 0.0)),
                        created_at=float(r_data.get("created_at", time.time())),
                    )
                )
        except Exception as e:
            print(f"[CronScheduler] Error loading routines: {e}")

    def _save_routines(self, routines: Optional[List[Routine]] = None) -> None:
        if routines is not None:
            self._routines = routines
        try:
            ROUTINES_FILE.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "routines": [r.to_dict() for r in self._routines],
            }
            ROUTINES_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[CronScheduler] Error saving routines: {e}")

    def list_routines(self) -> List[Routine]:
        self._load_routines()
        return self._routines

    def save_routine(self, routine: Routine) -> None:
        existing = [r for r in self._routines if r.id != routine.id]
        existing.append(routine)
        self._save_routines(existing)

    def delete_routine(self, routine_id: str) -> None:
        existing = [r for r in self._routines if r.id != routine_id]
        self._save_routines(existing)

    def toggle_routine(self, routine_id: str, enabled: bool) -> None:
        for r in self._routines:
            if r.id == routine_id:
                r.enabled = enabled
                break
        self._save_routines()

    def start(self) -> None:
        """Starts the background cron evaluator."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

        # Check for on_login triggers after startup
        threading.Thread(target=self._check_on_login_routines, daemon=True).start()

    def stop(self) -> None:
        self._running = False

    def _check_on_login_routines(self) -> None:
        time.sleep(3)
        now = time.time()
        for r in self.list_routines():
            if r.enabled and r.trigger == "on_login":
                if now - r.last_run > 3600 * 4:
                    print(f"[CronScheduler] 🌅 Executing login routine: {r.name}")
                    r.last_run = now
                    self._save_routines()
                    self._execute_routine(r)

    def _run_loop(self) -> None:
        while self._running:
            try:
                self._evaluate_due_routines()
            except Exception as e:
                print(f"[CronScheduler] Loop error: {e}")
            time.sleep(30)

    def _evaluate_due_routines(self) -> None:
        now = time.time()
        now_dt = datetime.now()
        current_hm = now_dt.strftime("%H:%M")

        for r in self.list_routines():
            if not r.enabled:
                continue

            is_due = False
            if r.trigger == "daily_at":
                if current_hm == r.time_spec and (now - r.last_run > 70):
                    is_due = True
            elif r.trigger == "hourly":
                try:
                    hours_interval = float(r.time_spec)
                except ValueError:
                    hours_interval = 1.0
                if now - r.last_run >= hours_interval * 3600:
                    is_due = True

            if is_due:
                print(f"[CronScheduler] ⏰ Routine triggered: {r.name} ({r.trigger} -> {r.time_spec})")
                r.last_run = now
                self._save_routines()
                self._execute_routine(r)

    def _execute_routine(self, routine: Routine) -> None:
        if not self.app or not routine.prompt:
            return

        def _worker():
            try:
                if routine.notify_desktop:
                    subprocess.Popen([
                        "notify-send",
                        "-a", "Sayri AI",
                        f"⏰ {routine.name}",
                        "Ejecutando rutina programada…",
                    ])

                result_text = self.app.process_remote_message(
                    text=routine.prompt,
                    author="Automated Routine",
                    target_agent_id=routine.agent_id,
                    instance_id=f"routine-{routine.id}",
                )

                if result_text and routine.speak_tts and hasattr(self.app, "tts"):
                    clean_for_speech = re.sub(r"[`*#_]", "", result_text).strip()
                    self.app.tts.speak(clean_for_speech)

                if routine.notify_desktop and result_text:
                    subprocess.Popen([
                        "notify-send",
                        "-a", "Sayri AI",
                        f"✨ {routine.name}",
                        result_text[:180],
                    ])
            except Exception as e:
                print(f"[CronScheduler] Error running routine {routine.id}: {e}")

        threading.Thread(target=_worker, daemon=True).start()


cron_scheduler = CronScheduler()
