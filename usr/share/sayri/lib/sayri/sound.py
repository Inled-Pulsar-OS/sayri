"""Sound effects player for Sayri (activation sound, thinking loop)."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from typing import Optional

from . import paths

_loop_running = False
_loop_thread: Optional[threading.Thread] = None
_active_procs: list[subprocess.Popen] = []
_lock = threading.Lock()


def _get_player_cmd(sound_file: str) -> list[str]:
    """Find appropriate audio player binary."""
    if shutil.which("pw-play"):
        return ["pw-play", "--volume", "0.85", sound_file]
    if shutil.which("paplay"):
        return ["paplay", sound_file]
    if shutil.which("ffplay"):
        return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "-volume", "85", sound_file]
    if shutil.which("gst-play-1.0"):
        return ["gst-play-1.0", "--no-interactive", sound_file]
    return []


def play(name: str) -> None:
    """Play a one-shot sound effect (e.g. 'activate')."""
    sound_path = paths.find_sound(name)
    if not sound_path:
        return

    cmd = _get_player_cmd(sound_path)
    if not cmd:
        return

    def _worker():
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            with _lock:
                _active_procs.append(proc)
            proc.wait(timeout=10)
        except Exception:
            pass
        finally:
            with _lock:
                if proc in _active_procs:
                    _active_procs.remove(proc)

    threading.Thread(target=_worker, daemon=True).start()


def start_loop(name: str) -> None:
    """Start playing a sound effect continuously in a loop (e.g. 'thinking')."""
    global _loop_running, _loop_thread
    sound_path = paths.find_sound(name)
    if not sound_path:
        return

    cmd = _get_player_cmd(sound_path)
    if not cmd:
        return

    stop_loop()

    _loop_running = True

    def _loop():
        global _loop_running
        while _loop_running:
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                with _lock:
                    _active_procs.append(proc)
                while _loop_running and proc.poll() is None:
                    time.sleep(0.05)
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
            except Exception:
                break
            finally:
                with _lock:
                    if proc in _active_procs:
                        _active_procs.remove(proc)

    _loop_thread = threading.Thread(target=_loop, daemon=True)
    _loop_thread.start()


def stop_loop() -> None:
    """Stop the thinking loop sound."""
    global _loop_running
    _loop_running = False
    stop_all()


def stop_all() -> None:
    """Stop all active sound playback immediately."""
    global _loop_running
    _loop_running = False
    with _lock:
        for p in list(_active_procs):
            try:
                p.kill()
                p.wait()
            except Exception:
                pass
        _active_procs.clear()
