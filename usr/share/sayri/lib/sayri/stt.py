"""Speech-to-text with whisper.cpp.

The microphone is captured as raw S16LE @ 16 kHz. An energy-based VAD
splits the stream into utterances (speech followed by `silence_ms` of
silence). Each utterance is transcribed with `whisper-cli`; while the user
keeps talking, optional live partial transcriptions are produced so the UI
can show the text as it forms.

The wake-word check ("hey sayri" / "hey siri" / custom) is applied by the
caller on the utterance/partial texts.
"""

from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
import wave
from typing import Callable, Optional

from . import audio, downloads, paths

VAD_THRESHOLD = 0.02       # RMS level that counts as speech
MIN_SPEECH_MS = 100        # speech must persist this long to count
MAX_UTTERANCE_MS = 15000   # safety cap for a single utterance
PARTIAL_EVERY_MS = 2000    # live partial transcription interval


def _whisper_lang(raw: str) -> str:
    if not raw or raw.lower() == "auto":
        return "auto"
    raw = raw.strip().lower()
    if "_" in raw:
        return raw.split("_")[0]
    if "-" in raw:
        return raw.split("-")[0]
    return raw[:2]


GHOST_PATTERNS = [
    r"^[\s\.\,\!\?\:\;\-_]*$",
    # Spanish ghost phrases commonly hallucinated by whisper
    r"subt[ií]tulos",
    r"suscr[ií]bete",
    r"gracias por ver",
    r"amara\.org",
    r"transcripci[oó]n por",
    r"para m[aá]s videos",
    r"siguiente video",
    r"hasta la pr[oó]xima",
    r"dale like",
    r"v[ií]deo siguiente",
    r"todos los derechos reservados",
    # English ghost phrases commonly hallucinated by whisper
    r"subtitles",
    r"subscribe",
    r"thanks for watching",
    r"amara\.org",
    r"transcript",
    r"transcription by",
    r"for more videos",
    r"next video",
    r"see you next time",
    r"give a like",
    r"hit like",
    r"all rights reserved",
    r"please like and subscribe",
]


def clean_transcription(text: str) -> str:
    if not text:
        return ""
    import re
    # Remove bracketed, parenthesized, or starred sound annotations e.g. [music], (engine), [knock, knock], *applause*
    cleaned = re.sub(r"\[.*?\]", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\(.*?\)", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\*.*?\*", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip(" \t\n\r.,;:¿?¡!-_'\"")

    # Filter out known ghost word hallucinations
    lower = cleaned.lower()
    for pat in GHOST_PATTERNS:
        if re.search(pat, lower):
            return ""

    # Check if there are actual words left
    words = re.findall(r"\w+", cleaned)
    if not words or len("".join(words)) < 2:
        return ""
    return cleaned


class STTEngine:
    def __init__(self, cfg) -> None:
        self.cfg = cfg

    # ------------------------------------------------------------ status
    @property
    def binary(self) -> Optional[str]:
        return paths.find_binary("whisper-cli")

    @property
    def model_path(self) -> str:
        return downloads.whisper_model_path(
            self.cfg.get_string("stt", "model_size"),
            self.cfg.get_string("stt", "language"),
        )

    @property
    def ready(self) -> bool:
        return bool(self.binary) and os.path.isfile(self.model_path)

    def missing(self) -> list[str]:
        """Human-readable list of what is missing (for the settings UI)."""
        out = []
        if not self.binary:
            out.append("whisper-cli binary")
        if not os.path.isfile(self.model_path):
            out.append("whisper model")
        return out

    # ------------------------------------------------------- one-shot
    def transcribe(self, wav_path: str, language: Optional[str] = None) -> str:
        """Blocking transcription of a WAV file; returns the text."""
        binary = self.binary
        if not binary:
            raise RuntimeError("whisper-cli is not installed")
        lang = _whisper_lang(language or self.cfg.get_string("stt", "language") or "auto")
        bin_dir = paths.bin_dir()
        env = dict(os.environ)
        cur_ld = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = f"{bin_dir}:{os.path.dirname(binary)}:{cur_ld}".rstrip(":")

        cmd = [
            binary,
            "-m", self.model_path,
            "-f", wav_path,
            "-l", lang,
            "-t", "4",      # 4 CPU threads for fast inference
            "-nt",          # no timestamps
            "-np",          # no prints beyond the result
            "-oj",          # also write JSON (keeps stdout clean)
            "--prompt", "Sayri, Oye Sayri, Hey Sayri, Hola Sayri, Hello Sayri, Hi Sayri.",
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                env=env,
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            return ""
        if proc.returncode != 0:
            return ""
        text = proc.stdout.strip()
        if not text:
            text = _read_json_text(wav_path + ".json")
        return text.strip()

    # ------------------------------------------------------- session
    def create_session(
        self,
        *,
        on_partial: Callable[[str], None],
        on_utterance: Callable[[str], None],
        on_level: Optional[Callable[[float], None]] = None,
        on_speech_start: Optional[Callable[[], None]] = None,
        on_transcribe_start: Optional[Callable[[], None]] = None,
    ) -> "STTSession":
        return STTSession(
            self.cfg,
            on_partial=on_partial,
            on_utterance=on_utterance,
            on_level=on_level,
            on_speech_start=on_speech_start,
            on_transcribe_start=on_transcribe_start,
        )


def _read_json_text(json_path: str) -> str:
    try:
        import json

        with open(json_path, encoding="utf-8") as fh:
            data = json.load(fh)
        return " ".join(t.get("text", "") for t in data.get("transcription", []))
    except Exception:  # noqa: BLE001
        return ""


class STTSession:
    """Long-running mic session producing utterance events."""

    def __init__(
        self,
        cfg,
        *,
        on_partial: Callable[[str], None],
        on_utterance: Callable[[str], None],
        on_level: Optional[Callable[[float], None]] = None,
        on_speech_start: Optional[Callable[[], None]] = None,
        on_transcribe_start: Optional[Callable[[], None]] = None,
    ) -> None:
        self.cfg = cfg
        self.on_partial = on_partial
        self.on_utterance = on_utterance
        self.on_level = on_level
        self.on_speech_start = on_speech_start
        self.on_transcribe_start = on_transcribe_start
        self.engine = STTEngine(cfg)

        self._mic: Optional[subprocess.Popen] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # utterance state
        self._chunks: list[bytes] = []
        self._speaking = False
        self._speech_ms = 0
        self._silence_ms = 0
        self._utt_started = 0.0
        self._last_partial = 0.0
        self._transcribing = False
        self._noise_floor = 0.02

    # ------------------------------------------------------------ life
    def start(self) -> bool:
        if self._running:
            return True
        self._mic = audio.start_mic(self.cfg.get_string("stt", "mic_device"))
        if not self._mic:
            return False
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._running = False
        if self._mic:
            try:
                self._mic.kill()
            except Exception:  # noqa: BLE001
                pass
            self._mic = None
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    def is_running(self) -> bool:
        return self._running

    def flush(self) -> None:
        """Force-finalize the current utterance right now (manual trigger)."""
        if self._speaking and self._chunks:
            self._finalize()

    # ------------------------------------------------------------ loop
    def _loop(self) -> None:
        mic = self._mic
        if not mic or not mic.stdout:
            return
        silence_ms = max(200, self.cfg.get_int("stt", "silence_ms"))
        live = self.cfg.get_bool("stt", "live_transcript")
        discard_until = time.monotonic() + 0.45

        while self._running:
            chunk = mic.stdout.read(audio.CHUNK_BYTES)
            if not chunk:
                if mic.poll() is not None:
                    break
                continue

            # Flush out hardware PipeWire residue / speaker echo on startup
            if time.monotonic() < discard_until:
                continue

            level = audio.rms_level(chunk)
            now = time.monotonic()
            if self.on_level and (now - getattr(self, "_last_lvl_emit", 0.0) >= 0.05):
                self._last_lvl_emit = now
                self.on_level(level)

            # Adaptive noise floor tracking (bounded so room noise never masks voice)
            if not self._speaking:
                self._noise_floor = max(0.005, min(0.024, 0.95 * self._noise_floor + 0.05 * level))

            # Dynamic thresholds adapting to environment
            speech_thresh = min(0.030, max(0.012, self._noise_floor * 1.30 + 0.004))
            silence_thresh = max(0.018, self._noise_floor * 1.20 + 0.005)

            if not self._speaking:
                if level >= speech_thresh:
                    self._speech_ms += audio.CHUNK_MS
                    if self._speech_ms >= MIN_SPEECH_MS:
                        self._speaking = True
                        self._utt_started = time.monotonic()
                        self._chunks = [chunk]
                        self._silence_ms = 0
                        print(f"[STT] 🎙️ Speech detected (RMS: {level:.3f}, noise floor: {self._noise_floor:.3f})")
                        if self.on_speech_start:
                            self.on_speech_start()
                else:
                    self._speech_ms = 0
            else:
                self._chunks.append(chunk)
                if level < silence_thresh:
                    self._silence_ms += audio.CHUNK_MS
                else:
                    self._silence_ms = max(0, self._silence_ms - 25)

            # utterance end: silence tail or cap
            elapsed = (time.monotonic() - self._utt_started) * 1000.0 if self._speaking else 0.0
            if self._speaking and (self._silence_ms >= silence_ms or elapsed >= MAX_UTTERANCE_MS):
                print(f"[STT] ⏸️ Silence detected ({self._silence_ms}ms) after {elapsed/1000.0:.2f}s speech")
                self._finalize()

        # flush anything left on stop
        if self._speaking and self._chunks:
            self._finalize()

    def _finalize(self) -> None:
        wav = self._dump_wav()
        self._speaking = False
        self._speech_ms = 0
        self._silence_ms = 0
        self._chunks = []
        if wav:
            self._transcribe_async(partial=False, wav=wav)

    def _dump_wav(self) -> Optional[str]:
        if not self._chunks:
            return None
        path = os.path.join(paths.tmp_dir(), f"utt-{int(time.time() * 1000)}.wav")
        with wave.open(path, "wb") as wf:
            wf.setnchannels(audio.CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(audio.RATE)
            wf.writeframes(b"".join(self._chunks))
        return path

    def _transcribe_async(self, partial: bool, wav: Optional[str] = None) -> None:
        self._transcribing = True
        wav_path = wav or self._dump_wav()
        if not wav_path:
            self._transcribing = False
            return

        if not partial and self.on_transcribe_start:
            self.on_transcribe_start()

        def work() -> None:
            text = ""
            t0 = time.time()
            model_name = os.path.basename(self.engine.model_path)
            try:
                raw_text = self.engine.transcribe(wav_path)
                took = time.time() - t0
                cleaned_text = clean_transcription(raw_text)
                if cleaned_text:
                    print(f"[STT] ✓ Whisper [{model_name}] transcribed in {took:.2f}s: \"{cleaned_text}\"")
                    text = cleaned_text
                elif raw_text:
                    print(f"[STT] ℹ️ Ignored non-speech artifact [{model_name}] in {took:.2f}s: \"{raw_text}\"")
                    text = ""
                else:
                    print(f"[STT] ℹ️ Whisper [{model_name}] transcribed in {took:.2f}s: (no words detected)")
                    text = ""
            except Exception as exc:  # noqa: BLE001
                print(f"[STT] ❌ Transcription error [{model_name}]: {exc}")
            finally:
                if partial:
                    if text:
                        self.on_partial(text)
                else:
                    self.on_utterance(text or "")
                try:
                    os.remove(wav_path)
                    os.remove(wav_path + ".json")
                except OSError:
                    pass
                self._transcribing = False

        threading.Thread(target=work, daemon=True).start()
