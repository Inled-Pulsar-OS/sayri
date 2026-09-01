"""Microphone capture and audio playback helpers.

Capture prefers PipeWire's pw-record and falls back to PulseAudio's parec.
Both are configured for raw S16LE mono @ 16 kHz.
"""

from __future__ import annotations

import array
import math
import shutil
import subprocess
from typing import Optional

RATE = 16000
CHANNELS = 1
CHUNK_MS = 100
CHUNK_BYTES = RATE * 2 * CHANNELS * CHUNK_MS // 1000  # 3200 bytes @ 100 ms


def mic_command(device: str = "") -> Optional[list[str]]:
    if shutil.which("pw-record"):
        cmd = [
            "pw-record",
            "--raw",
            "--format", "s16",
            "--rate", str(RATE),
            "--channels", str(CHANNELS),
        ]
        if device:
            cmd += ["--target", device]
        cmd += ["-"]
        return cmd
    if shutil.which("parec"):
        cmd = [
            "parec",
            "--raw",
            "--format=s16le",
            "--rate", str(RATE),
            "--channels", str(CHANNELS),
        ]
        if device:
            cmd += ["--device", device]
        return cmd
    return None


def start_mic(device: str = "") -> Optional[subprocess.Popen]:
    cmd = mic_command(device)
    if not cmd:
        return None
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )


def rms_level(chunk: bytes) -> float:
    """Normalized RMS (0..1) of a raw S16LE chunk."""
    if not chunk:
        return 0.0
    samples = array.array("h")
    samples.frombytes(chunk[: len(chunk) - (len(chunk) % 2)])
    if not samples:
        return 0.0
    total = 0.0
    for s in samples:
        total += s * s
    return math.sqrt(total / len(samples)) / 32768.0


def player_command() -> Optional[str]:
    for name in ("pw-play", "paplay", "aplay"):
        if shutil.which(name):
            return name
    return None


def play_wav(path: str) -> Optional[subprocess.Popen]:
    """Play a WAV file, returning the process (caller can poll/kill it)."""
    player = player_command()
    if not player:
        return None
    return subprocess.Popen([player, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
