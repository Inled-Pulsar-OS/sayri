"""Microphone capture and audio playback helpers.

Capture produces raw S16LE mono @ 16 kHz:

* Linux  -> PipeWire's ``pw-record``, falling back to PulseAudio ``parec``.
* macOS  -> ``ffmpeg`` AVFoundation capture (or ``sox`` if present).
* Windows -> ``ffmpeg`` DirectShow capture.

Playback prefers pw-play/paplay/aplay on Linux, afplay on macOS, ffplay on
Windows (anything else available is used as a last resort).
"""

from __future__ import annotations

import array
import math
import shutil
import subprocess
from typing import Optional

from . import sysinfo

RATE = 16000
CHANNELS = 1
CHUNK_MS = 100
CHUNK_BYTES = RATE * 2 * CHANNELS * CHUNK_MS // 1000  # 3200 bytes @ 100 ms


def _ffmpeg_capture(device: str, fmt: str) -> list[str]:
    """ffmpeg raw S16LE capture for a given input format/device."""
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", fmt,
        "-i", device,
        "-ar", str(RATE),
        "-ac", str(CHANNELS),
        "-f", "s16le", "-",
    ]
    return cmd


def mic_command(device: str = "") -> Optional[list[str]]:
    if sysinfo.is_macos():
        if sysinfo.cmd_exists("ffmpeg"):
            return _ffmpeg_capture(":" + (device or "0"), "avfoundation")
        if sysinfo.cmd_exists("sox"):
            src = ["-t", "coreaudio", device] if device else ["-d"]
            return ["sox", "-q"] + src + [
                "-b", "16", "-c", str(CHANNELS), "-r", str(RATE),
                "-e", "signed-integer", "-t", "raw", "-",
            ]
        return None

    if sysinfo.is_windows():
        if sysinfo.cmd_exists("ffmpeg"):
            return _ffmpeg_capture("audio=" + (device or "default"), "dshow")
        return None

    # Linux / POSIX: PipeWire or PulseAudio raw capture (unchanged).
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
    if sysinfo.is_windows():
        for name in ("ffplay", "mpv"):
            if shutil.which(name):
                return name
        return None
    if sysinfo.is_macos():
        for name in ("afplay", "ffplay", "mpv"):
            if shutil.which(name):
                return name
        return None
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