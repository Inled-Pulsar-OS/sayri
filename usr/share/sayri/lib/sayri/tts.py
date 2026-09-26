"""Text-to-speech with Piper.

Synthesizes the text to a WAV with `piper`, then plays it with
pw-play / paplay / aplay. The orb animates while playback runs.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from typing import Callable, Optional

from . import audio, downloads, paths


class TTSEngine:
    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self._proc: Optional[subprocess.Popen] = None

    # ------------------------------------------------------------ status
    @property
    def binary(self) -> Optional[str]:
        return paths.find_binary("piper")

    @property
    def voice_files(self) -> Optional[tuple[str, Optional[str]]]:
        lang = self.cfg.get_string("tts", "language")
        voice = self.cfg.get_string("tts", "voice")
        quality = self.cfg.get_string("tts", "quality")
        onnx = downloads.piper_voice_path(lang, voice, quality)
        if not os.path.isfile(onnx):
            return None
        json_path = onnx + ".json"
        return (onnx, json_path if os.path.isfile(json_path) else None)

    @property
    def ready(self) -> bool:
        return bool(self.binary) and self.voice_files is not None

    @property
    def is_speaking(self) -> bool:
        return bool(self._proc and self._proc.poll() is None)

    def missing(self) -> list[str]:
        out = []
        if not self.binary:
            out.append("piper binary")
        if self.voice_files is None:
            out.append("piper voice")
        return out

    # ------------------------------------------------------------ speak
    def speak(
        self,
        text: str,
        *,
        on_start: Optional[Callable[[], None]] = None,
        on_end: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
        on_level: Optional[Callable[[float], None]] = None,
    ) -> None:
        """Synthesize + play. Blocking; run it in a worker thread."""
        if not text.strip():
            if on_end:
                on_end()
            return
        binary = self.binary
        voices = self.voice_files
        if not binary or not voices:
            if on_error:
                on_error(RuntimeError("Piper or the voice is not installed"))
            return

        onnx, json_path = voices
        speed = max(0.1, float(self.cfg.get_float("tts", "speed")))
        length_scale = 1.0 / speed
        wav_out = os.path.join(paths.tmp_dir(), f"tts-{os.getpid()}.wav")

        bin_dir = paths.bin_dir()
        env = dict(os.environ)
        cur_ld = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = f"{bin_dir}:{os.path.dirname(binary)}:{cur_ld}".rstrip(":")
        espeak_data = os.path.join(bin_dir, "espeak-ng-data")

        try:
            cmd = [binary, "--model", onnx, "--output_file", wav_out]
            if json_path:
                cmd += ["--config", json_path]
            if os.path.isdir(espeak_data):
                cmd += ["--espeak_data", espeak_data]
            cmd += ["--length_scale", f"{length_scale:.3f}"]

            proc = subprocess.run(
                cmd,
                input=text.encode("utf-8"),
                capture_output=True,
                env=env,
                timeout=120,
            )
            if proc.returncode != 0 or not os.path.isfile(wav_out):
                err_detail = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
                raise RuntimeError(f"piper failed (code {proc.returncode}): {err_detail[:200]}")

            if on_start:
                on_start()
            player = audio.play_wav(wav_out)
            if player:
                self._proc = player

                # Stream live audio RMS levels during playback
                if on_level:
                    def _level_monitor() -> None:
                        try:
                            import time, wave
                            with wave.open(wav_out, "rb") as wf:
                                framerate = wf.getframerate()
                                nchannels = wf.getnchannels()
                                sampwidth = wf.getsampwidth()
                                chunk_frames = int(framerate * 0.05)
                                while self._proc and self._proc.poll() is None:
                                    data = wf.readframes(chunk_frames)
                                    if not data:
                                        break
                                    lvl = audio.rms_level(data)
                                    on_level(min(1.0, lvl * 2.5))
                                    time.sleep(0.048)
                        except Exception:
                            pass
                        finally:
                            if on_level:
                                on_level(0.0)

                    threading.Thread(target=_level_monitor, daemon=True).start()

                try:
                    player.wait()
                    import time
                    time.sleep(0.35)
                finally:
                    self._proc = None
            if on_level:
                on_level(0.0)
            if on_end:
                on_end()
        except Exception as exc:  # noqa: BLE001
            if on_error:
                on_error(exc)
        finally:
            if on_level:
                on_level(0.0)
            for p in (wav_out,):
                try:
                    os.remove(p)
                except OSError:
                    pass

    def speak_async(self, text: str, **callbacks) -> threading.Thread:
        t = threading.Thread(target=self.speak, args=(text,), kwargs=callbacks, daemon=True)
        t.start()
        return t

    def cancel(self) -> None:
        if self._proc:
            try:
                self._proc.kill()
            except Exception:  # noqa: BLE001
                pass
            self._proc = None
