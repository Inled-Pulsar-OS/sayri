"""Downloads for Sayri: whisper.cpp models, Piper voices and runtime binaries.

Sources:
  - whisper.cpp models:  HuggingFace  ggerganov/whisper.cpp
  - Piper voices:        HuggingFace  rhasspy/piper-voices
  - whisper-cli binary:  GitHub      ggml-org/whisper.cpp releases
  - piper binary:        GitHub      rhasspy/piper releases
"""

from __future__ import annotations

import os
import shutil
import tarfile
import urllib.error
import urllib.request
import zipfile
from typing import Callable, Optional

from . import paths

# ---------------------------------------------------------------------------
# whisper.cpp models (multilingual; .en variants are English-only)
# ---------------------------------------------------------------------------
WHISPER_BASE_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"

WHISPER_MODELS: dict[str, dict] = {
    "tiny": {"file": "ggml-tiny.bin", "size": "~75 MB"},
    "tiny.en": {"file": "ggml-tiny.en.bin", "size": "~75 MB"},
    "base": {"file": "ggml-base.bin", "size": "~142 MB"},
    "base.en": {"file": "ggml-base.en.bin", "size": "~142 MB"},
    "small": {"file": "ggml-small.bin", "size": "~466 MB"},
    "small.en": {"file": "ggml-small.en.bin", "size": "~466 MB"},
    "medium": {"file": "ggml-medium.bin", "size": "~1.5 GB"},
    "medium.en": {"file": "ggml-medium.en.bin", "size": "~1.5 GB"},
    "large-v3": {"file": "ggml-large-v3.bin", "size": "~3.1 GB"},
}

# Preferred English-only variant when the user picks language "en".
ENGLISH_ONLY_VARIANT = {
    "tiny": "tiny.en",
    "base": "base.en",
    "small": "small.en",
    "medium": "medium.en",
    "large-v3": "large-v3",
}

# ---------------------------------------------------------------------------
# Piper voices (rhasspy/piper-voices layout: <lang>/<voice>/<quality>/file)
# ---------------------------------------------------------------------------
PIPER_BASE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/"

PIPER_VOICES: dict[str, list[dict]] = {
    "en_US": [
        {"voice": "amy", "quality": "medium", "path": "en/en_US/amy/medium/en_US-amy-medium.onnx", "size": "~63 MB"},
        {"voice": "lessac", "quality": "medium", "path": "en/en_US/lessac/medium/en_US-lessac-medium.onnx", "size": "~63 MB"},
        {"voice": "ryan", "quality": "high", "path": "en/en_US/ryan/high/en_US-ryan-high.onnx", "size": "~120 MB"},
        {"voice": "joe", "quality": "medium", "path": "en/en_US/joe/medium/en_US-joe-medium.onnx", "size": "~62 MB"},
        {"voice": "kathleen", "quality": "low", "path": "en/en_US/kathleen/low/en_US-kathleen-low.onnx", "size": "~41 MB"},
    ],
    "en_GB": [
        {"voice": "alan", "quality": "medium", "path": "en/en_GB/alan/medium/en_GB-alan-medium.onnx", "size": "~63 MB"},
        {"voice": "alba", "quality": "medium", "path": "en/en_GB/alba/medium/en_GB-alba-medium.onnx", "size": "~63 MB"},
        {"voice": "northern_english_male", "quality": "medium", "path": "en/en_GB/northern_english_male/medium/en_GB-northern_english_male-medium.onnx", "size": "~63 MB"},
        {"voice": "cori", "quality": "medium", "path": "en/en_GB/cori/medium/en_GB-cori-medium.onnx", "size": "~63 MB"},
    ],
    "es_ES": [
        {"voice": "sharvard", "quality": "medium", "path": "es/es_ES/sharvard/medium/es_ES-sharvard-medium.onnx", "size": "~63 MB"},
        {"voice": "davefx", "quality": "medium", "path": "es/es_ES/davefx/medium/es_ES-davefx-medium.onnx", "size": "~63 MB"},
        {"voice": "carlfm", "quality": "x_low", "path": "es/es_ES/carlfm/x_low/es_ES-carlfm-x_low.onnx", "size": "~16 MB"},
    ],
    "es_MX": [
        {"voice": "ald", "quality": "medium", "path": "es/es_MX/ald/medium/es_MX-ald-medium.onnx", "size": "~63 MB"},
        {"voice": "claude", "quality": "high", "path": "es/es_MX/claude/high/es_MX-claude-high.onnx", "size": "~120 MB"},
    ],
    "fr_FR": [
        {"voice": "siwis", "quality": "medium", "path": "fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx", "size": "~62 MB"},
        {"voice": "upmc", "quality": "medium", "path": "fr/fr_FR/upmc/medium/fr_FR-upmc-medium.onnx", "size": "~62 MB"},
    ],
    "de_DE": [
        {"voice": "thorsten", "quality": "medium", "path": "de/de_DE/thorsten/medium/de_DE-thorsten-medium.onnx", "size": "~63 MB"},
        {"voice": "ramona", "quality": "low", "path": "de/de_DE/ramona/low/de_DE-ramona-low.onnx", "size": "~19 MB"},
    ],
    "it_IT": [
        {"voice": "paola", "quality": "medium", "path": "it/it_IT/paola/medium/it_IT-paola-medium.onnx", "size": "~63 MB"},
    ],
    "pt_BR": [
        {"voice": "faber", "quality": "medium", "path": "pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx", "size": "~63 MB"},
        {"voice": "edresson", "quality": "low", "path": "pt/pt_BR/edresson/low/pt_BR-edresson-low.onnx", "size": "~28 MB"},
    ],
    "nl_NL": [
        {"voice": "mls_7432", "quality": "low", "path": "nl/nl_NL/mls_7432/low/nl_NL-mls_7432-low.onnx", "size": "~28 MB"},
    ],
    "ru_RU": [
        {"voice": "irina", "quality": "medium", "path": "ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx", "size": "~62 MB"},
    ],
    "pl_PL": [
        {"voice": "darkman", "quality": "medium", "path": "pl/pl_PL/darkman/medium/pl_PL-darkman-medium.onnx", "size": "~62 MB"},
    ],
    "zh_CN": [
        {"voice": "huayan", "quality": "medium", "path": "zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx", "size": "~63 MB"},
    ],
    "ca_ES": [
        {"voice": "upc_ona", "quality": "medium", "path": "ca/ca_ES/upc_ona/medium/ca_ES-upc_ona-medium.onnx", "size": "~63 MB"},
        {"voice": "upc_pau", "quality": "medium", "path": "ca/ca_ES/upc_pau/medium/ca_ES-upc_pau-medium.onnx", "size": "~63 MB"},
    ],
    "sv_SE": [
        {"voice": "nst", "quality": "medium", "path": "sv/sv_SE/nst/medium/sv_SE-nst-medium.onnx", "size": "~63 MB"},
    ],
    "uk_UA": [
        {"voice": "lada", "quality": "medium", "path": "uk/uk_UA/lada/medium/uk_UA-lada-medium.onnx", "size": "~63 MB"},
    ],
    "tr_TR": [
        {"voice": "fdf", "quality": "medium", "path": "tr/tr_TR/fdf/medium/tr_TR-fdf-medium.onnx", "size": "~63 MB"},
    ],
    "el_GR": [
        {"voice": "rapunzelina", "quality": "low", "path": "el/el_GR/rapunzelina/low/el_GR-rapunzelina-low.onnx", "size": "~28 MB"},
    ],
    "ar_JO": [
        {"voice": "kareem", "quality": "medium", "path": "ar/ar_JO/kareem/medium/ar_JO-kareem-medium.onnx", "size": "~63 MB"},
    ],
}

# ---------------------------------------------------------------------------
# Runtime binaries (static builds, used when the distro packages are missing)
# ---------------------------------------------------------------------------
WHISPER_CLI_URL = (
    "https://github.com/ggml-org/whisper.cpp/releases/download/b4938/"
    "whisper-bin-ubuntu-x64.tar.gz"
)
PIPER_URL = (
    "https://github.com/rhasspy/piper/releases/download/2023.11.14-2/"
    "piper_linux_x86_64.tar.gz"
)


class DownloadError(Exception):
    pass


def download_file(
    url: str,
    dest: str,
    progress: Optional[Callable[[float], None]] = None,
) -> str:
    """Download `url` to `dest`, reporting progress 0..1. Returns dest."""
    paths.ensure_dirs()
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"

    for f in (dest, tmp):
        if os.path.exists(f):
            try:
                os.remove(f)
            except OSError:
                pass

    def _report(frac: float) -> None:
        if progress:
            progress(max(0.0, min(1.0, frac)))

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "sayri/0.1"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            got = 0
            with open(tmp, "wb") as out:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    out.write(chunk)
                    got += len(chunk)
                    if total:
                        _report(got / total)
            _report(1.0)
        os.replace(tmp, dest)
        return dest
    except urllib.error.HTTPError as exc:
        raise DownloadError(f"HTTP {exc.code} downloading {url}") from exc
    except urllib.error.URLError as exc:
        raise DownloadError(f"Network error downloading {url}: {exc.reason}") from exc
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _extract_tar(archive: str, dest_dir: str) -> None:
    with tarfile.open(archive, "r:gz") as tf:
        tf.extractall(dest_dir)


def _extract_zip(archive: str, dest_dir: str) -> None:
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(dest_dir)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------
def whisper_model_filename(model_size: str, language: str) -> str:
    key = model_size
    if language.strip().lower() == "en" and model_size in ENGLISH_ONLY_VARIANT:
        key = ENGLISH_ONLY_VARIANT[model_size]
    return WHISPER_MODELS[key]["file"]


def whisper_model_url(model_size: str, language: str) -> str:
    return WHISPER_BASE_URL + whisper_model_filename(model_size, language)


def whisper_model_path(model_size: str, language: str) -> str:
    return os.path.join(paths.models_dir(), whisper_model_filename(model_size, language))


def has_whisper_model(model_size: str, language: str) -> bool:
    return os.path.isfile(whisper_model_path(model_size, language))


def download_whisper_model(
    model_size: str,
    language: str,
    progress: Optional[Callable[[float], None]] = None,
) -> str:
    return download_file(
        whisper_model_url(model_size, language),
        whisper_model_path(model_size, language),
        progress,
    )


def piper_voice_path(language: str, voice: str, quality: str) -> str:
    entry = _piper_entry(language, voice, quality)
    return os.path.join(paths.voices_dir(), os.path.basename(entry["path"]))


def _piper_entry(language: str, voice: str, quality: str) -> dict:
    for entry in PIPER_VOICES.get(language, []):
        if entry["voice"] == voice and entry["quality"] == quality:
            return entry
    # Fall back to the first voice of the language.
    return PIPER_VOICES[language][0]


def has_piper_voice(language: str, voice: str, quality: str) -> bool:
    entry = _piper_entry(language, voice, quality)
    base = os.path.join(paths.voices_dir(), os.path.basename(entry["path"]))
    return os.path.isfile(base) and os.path.isfile(base + ".json")


def download_piper_voice(
    language: str,
    voice: str,
    quality: str,
    progress: Optional[Callable[[float], None]] = None,
) -> str:
    """Download the .onnx and its .json config. Reports progress for both."""
    entry = _piper_entry(language, voice, quality)
    onnx_name = os.path.basename(entry["path"])
    onnx_dest = os.path.join(paths.voices_dir(), onnx_name)
    json_url = PIPER_BASE_URL + entry["path"] + ".json"
    json_dest = onnx_dest + ".json"

    download_file(PIPER_BASE_URL + entry["path"], onnx_dest, progress)
    try:
        download_file(json_url, json_dest)
    except DownloadError:
        # Some voices ship without a .json (rare); piper can still run.
        pass
    return onnx_dest


def install_whisper_cli(progress: Optional[Callable[[float], None]] = None) -> str:
    """Download the whisper.cpp build and extract whisper-cli and libraries."""
    archive = os.path.join(paths.tmp_dir(), "whisper-bin.tar.gz")
    extract_dir = os.path.join(paths.tmp_dir(), "whisper-bin")
    download_file(WHISPER_CLI_URL, archive, progress)
    shutil.rmtree(extract_dir, ignore_errors=True)
    _extract_tar(archive, extract_dir)
    dest = os.path.join(paths.bin_dir(), "whisper-cli")
    for root, _dirs, files in os.walk(extract_dir):
        for f in files:
            src = os.path.join(root, f)
            dst = os.path.join(paths.bin_dir(), f)
            shutil.copy2(src, dst)
            if f.startswith("whisper-cli") or f.endswith(".so") or ".so." in f:
                try:
                    os.chmod(dst, 0o755)
                except OSError:
                    pass
    if os.path.isfile(dest):
        return dest
    raise DownloadError("whisper-cli not found inside the downloaded archive")


def install_piper(progress: Optional[Callable[[float], None]] = None) -> str:
    """Download the piper build and extract binary, libraries and espeak-ng-data."""
    archive = os.path.join(paths.tmp_dir(), "piper.tar.gz")
    extract_dir = os.path.join(paths.tmp_dir(), "piper")
    download_file(PIPER_URL, archive, progress)
    shutil.rmtree(extract_dir, ignore_errors=True)
    _extract_tar(archive, extract_dir)
    dest = os.path.join(paths.bin_dir(), "piper")
    espeak_dest = os.path.join(paths.bin_dir(), "espeak-ng-data")
    for root, dirs, files in os.walk(extract_dir):
        for f in files:
            src = os.path.join(root, f)
            dst = os.path.join(paths.bin_dir(), f)
            shutil.copy2(src, dst)
            if f in ("piper", "espeak-ng") or f.endswith(".so") or ".so." in f:
                try:
                    os.chmod(dst, 0o755)
                except OSError:
                    pass
        if "espeak-ng-data" in dirs:
            src_data = os.path.join(root, "espeak-ng-data")
            shutil.rmtree(espeak_dest, ignore_errors=True)
            shutil.copytree(src_data, espeak_dest)
    if os.path.isfile(dest):
        return dest
    raise DownloadError("piper binary not found inside the downloaded archive")
