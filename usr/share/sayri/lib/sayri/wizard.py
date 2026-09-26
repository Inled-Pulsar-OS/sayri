"""Sayri welcome wizard — the same setup flow in the CLI and the GUI.

Built on :mod:`sayri.xui`: a screen is a declarative JSON document and every
renderer (terminal, WebKit window, browser page) speaks the same event
protocol, so the wizard logic below is shared verbatim by all surfaces.

    sayri welcome            # interactive TUI (or piped line mode)
    xui_window.open_wizard() # GTK/WebKit window (first run / settings)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

from . import paths
from .providers import available_providers
from .xui import (
    TaskRunner, button, check, collect_defaults, entry, note,
    progress, screen, select as _select, sub, text,
)

DownloadFn = Callable[[Callable[[Optional[float]], None], list], Any]

LANGUAGES: list[dict] = [
    {"value": "es_ES", "label": "Español (España)"},
    {"value": "es_MX", "label": "Español (México)"},
    {"value": "en_US", "label": "English (US)"},
    {"value": "en_GB", "label": "English (UK)"},
    {"value": "ca_ES", "label": "Català"},
    {"value": "fr_FR", "label": "Français"},
    {"value": "de_DE", "label": "Deutsch"},
    {"value": "it_IT", "label": "Italiano"},
    {"value": "pt_BR", "label": "Português (BR)"},
    {"value": "nl_NL", "label": "Nederlands"},
    {"value": "ru_RU", "label": "Русский"},
    {"value": "pl_PL", "label": "Polski"},
    {"value": "zh_CN", "label": "中文 (简体)"},
]

PROVIDERS: list[dict] = [
    {"value": "ollama", "label": "Ollama (local, recommended)",
     "base_url": "http://127.0.0.1:11434/v1", "model": "llama3.2", "key": False},
    {"value": "openai", "label": "OpenAI",
     "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini", "key": True},
    {"value": "groq", "label": "Groq (fast & free)",
     "base_url": "https://api.groq.com/openai/v1", "model": "llama-3.3-70b-versatile", "key": True},
    {"value": "openrouter", "label": "OpenRouter",
     "base_url": "https://openrouter.ai/api/v1", "model": "openai/gpt-4o-mini", "key": True},
    {"value": "custom", "label": "Other (OpenAI-compatible)",
     "base_url": "", "model": "", "key": False},
]

WHISPER_SIZES: list[dict] = [
    {"value": "tiny", "label": "Tiny (~75 MB)", "desc": "fast, less accurate"},
    {"value": "base", "label": "Base (~142 MB)", "desc": "good balance, recommended"},
    {"value": "small", "label": "Small (~466 MB)", "desc": "more accurate, slower"},
    {"value": "medium", "label": "Medium (~1.5 GB)", "desc": "very accurate, heavy"},
    {"value": "large-v3", "label": "Large-v3 (~3.1 GB)", "desc": "maximum accuracy, slow"},
]

# Real quantization per family (slugs → labels), kept in sync with the plugin.
PRISM_QUANTS: dict[str, list[tuple[str, str, str]]] = {
    "ternary": [
        ("pq2_0", "PQ2_0", "the lightest (recommended)"),
        ("q2_0_g64", "Q2_0_g64", "good balance"),
        ("q2_0", "Q2_0", "classic 2-bit"),
        ("f16", "F16", "best quality, largest"),
    ],
    "bonsai": [
        ("q1_0", "Q1_0", "Bonsai is 1-bit: only option"),
    ],
}
PRISM_DEFAULT_QUANT: dict[str, str] = {"ternary": "pq2_0", "bonsai": "q1_0"}


def _default_language() -> str:
    lc = os.environ.get("LANG", "") or os.environ.get("LC_ALL", "") or "es_ES"
    tag = lc.split(".")[0].replace("-", "_")
    for opt in LANGUAGES:
        if tag == opt["value"] or tag.startswith(opt["value"].split("_")[0] + "_"):
            return opt["value"]
    if tag[:2] == "en":
        return "en_US"
    return "es_ES"


class WelcomeApp:
    """The host: owns wizard state, renders screens and reacts to events.

    Events seen by every renderer (TUI / WebKit / HTTP) land in
    :meth:`dispatch`; long operations (voice / STT downloads) run in a
    background :class:`TaskRunner` and publish live progress screens that each
    renderer polls with ``{"type": "poll"}``.
    """

    def __init__(self) -> None:
        self.val: dict = {
            "language": _default_language(),
            "voice": "",
            "download_voice": True,
            "stt_size": "base",
            "download_stt": False,
            "provider": "ollama",
            "prism_family": "ternary",
            "prism_quant": "",
            "prism_size": "8B",
            "base_url": "http://127.0.0.1:11434/v1",
            "model": "llama3.2",
            "api_key": "",
        }
        self.idx = 0
        self.task = TaskRunner()
        self._on_task_done: Optional[Callable[[Any], None]] = None
        self._final: Optional[dict] = None

    # ------------------------------------------------------------- render
    def _is_prism(self) -> bool:
        """The chosen provider is the Prism ML plugin (local server)."""
        return self.val.get("provider", "") == "bonsai"

    def _review_idx(self) -> int:
        return 10 if self._is_prism() else 6

    def render(self) -> Optional[dict]:
        if self.idx == 0:
            return self._welcome_screen()
        if self.idx == 1:
            return self._language_screen()
        if self.idx == 2:
            return self._provider_screen()
        if self.idx == 3:
            # provider slides come before the model details when Prism ML is chosen
            return self._prism_family_screen() if self._is_prism() else self._provider_details_screen()
        if self._is_prism():
            if self.idx == 4:
                return self._prism_quant_screen()
            if self.idx == 5:
                return self._prism_size_screen()
            if self.idx == 6:
                return self._prism_overview_screen()
            if self.idx == 7:
                return self._provider_details_screen()
            if self.idx == 8:
                return self._voice_screen()
            if self.idx == 9:
                return self._stt_screen()
            if self.idx == 10:
                return self._review_screen()
        else:
            if self.idx == 4:
                return self._voice_screen()
            if self.idx == 5:
                return self._stt_screen()
            if self.idx == 6:
                return self._review_screen()
        return self._final or self._done_screen()

    def _step_label(self, n: int) -> str:
        return f"{n}/{11 if self._is_prism() else 7}"

    def _welcome_screen(self) -> dict:
        return screen(
            "Welcome to Sayri",
            [
                text("Hi 👋", accent=True),
                text("Sayri is your personal AI assistant.", dim=True),
                text("This one-minute setup guides you through language, AI provider, voice and STT.", dim=True),
                note("Everything you configure is saved in ~/.config/sayri and can be changed later in Settings.", "info"),
            ],
            subtitle="One-minute setup",
            footer=[
                button("start", "Start", kind="primary"),
                button("skip", "Skip", kind="secondary"),
            ],
            id="welcome", step=self._step_label(7),
        )

    def _language_screen(self) -> dict:
        return screen(
            "What language do you speak with Sayri?",
            [
                _select("language", "Language", LANGUAGES, default=self.val.get("language", "es_ES")),
                note("Sayri will transcribe your voice (STT) and reply (TTS) in this language.", "info"),
            ],
            footer=[button("back", "Back"), button("next", "Next", kind="primary")],
            id="language", step=self._step_label(1),
        )

    def _provider_screen(self) -> dict:
        opts = [{"value": p["value"], "label": p["label"],
                 "desc": p.get("model", "") and f"default model: {p['model']}"} for p in available_providers(PROVIDERS)]
        return screen(
            "Connect an AI provider",
            [
                _select("provider", "Provider", opts, default=self.val.get("provider", "ollama")),
                note("Sayri calls any OpenAI-compatible API. Ollama is local, free and private.", "info"),
            ],
            footer=[button("back", "Back"), button("next", "Next", kind="primary")],
            id="provider", step=self._step_label(2),
        )

    def _prism_autofill(self) -> dict:
        """Autofill base_url/model from the configured Prism plugin (family/size/port)."""
        try:
            fp = Path(paths.config_dir()) / "prismml.json"
            if fp.is_file():
                cfg = json.loads(fp.read_text(encoding="utf-8"))
                host = str(cfg.get("host") or "127.0.0.1")
                port = cfg.get("port") or 8080
                fam = str(cfg.get("family") or "ternary")
                size = str(cfg.get("size") or "8B")
                return {
                    "base_url": f"http://{host}:{port}/v1",
                    "model": f"{fam}-{size.lower()}",
                }
        except Exception:  # noqa: BLE001
            pass
        return {}

    def _provider_details_screen(self) -> dict:
        prov = self._provider_info(self.val.get("provider", "ollama"))
        if self._is_prism():
            fill = self._prism_autofill()
            base_url_default = fill.get("base_url") or self.val.get("base_url") or prov["base_url"]
            model_default = fill.get("model") or self.val.get("model") or prov.get("model", "")
            body = [
                entry("base_url", "Base URL", default=base_url_default,
                      placeholder="http://127.0.0.1:8080/v1", hint="OpenAI-compatible endpoint (…/v1)"),
                entry("model", "Model", default=model_default,
                      placeholder="ternary-8b"),
                entry("api_key", "API Key (optional)", default=self.val.get("api_key", ""),
                      secret=True, hint="Leave empty for Ollama / local servers."),
            ]
            body.insert(0, note(
                f"Prism ML runs locally — pre-filled with your configured model ({self.val.get('prism_family', 'ternary')}/"
                f"{self.val.get('prism_size', '8B')}) and server ({base_url_default}). Leave the API key empty.", "info"))
        else:
            body = [
                entry("base_url", "Base URL", default=self.val.get("base_url") or prov["base_url"],
                      placeholder="http://127.0.0.1:11434/v1", hint="OpenAI-compatible endpoint (…/v1)"),
                entry("model", "Model", default=self.val.get("model") or prov.get("model", ""),
                      placeholder="llama3.2"),
                entry("api_key", "API Key (optional)", default=self.val.get("api_key", ""),
                      secret=True, hint="Leave empty for Ollama / local servers."),
            ]
        return screen(
            "Provider details",
            body,
            footer=[button("back", "Back"), button("next", "Next", kind="primary")],
            id="provider_details", step=self._step_label(self.idx),
        )

    # ── Prism ML (local plugin): family → quantization → size → overview ──
    def _prism_detection(self) -> dict:
        """Real download state for the current prism selection."""
        family = self.val.get("prism_family", "ternary")
        size = self.val.get("prism_size", "8B")
        quant = self.val.get("prism_quant", "") or ""
        root = Path(os.environ.get("PRISM_ROOT") or os.path.join(paths.state_dir(), "prismml"))
        binary = Path(root) / "bins" / "llama-server"
        auto_quant = PRISM_DEFAULT_QUANT.get(family, "pq2_0")
        models_dir = Path(root) / "models"
        name = f"{family}-{size}-{quant}.gguf" if quant else f"{family}-{size}.gguf"
        model = models_dir / name
        model_ok = model.is_file()
        if not model_ok:  # any previously downloaded file for this family/size counts
            try:
                model_ok = any(p.is_file() for p in models_dir.glob(f"{family}-{size}*.gguf")) if models_dir.is_dir() else False
            except Exception:  # noqa: BLE001
                model_ok = False
        return {
            "binary_ok": binary.is_file() and os.access(binary, os.X_OK),
            "model_ok": model_ok,
            "binary": binary,
            "model": model,
            "quant_display": (quant or auto_quant).upper(),
        }

    def _prism_family_screen(self) -> dict:
        return screen(
            "Model family (Prism ML)",
            [_select("prism_family", "Family",
                     [
                         {"value": "ternary", "label": "Ternary-Bonsai",
                          "desc": "quantized ternary mode (recommended, PQ2_0)"},
                         {"value": "bonsai", "label": "Bonsai",
                          "desc": "classic 1-bit GGUF quantization (Q1_0)"},
                     ],
                     default=self.val.get("prism_family", "ternary"))],
            footer=[button("back", "Back"), button("next", "Next", kind="primary")],
            id="prism_family", step=self._step_label(self.idx),
        )

    def _prism_quant_screen(self) -> dict:
        family = self.val.get("prism_family", "ternary")
        options = [{"value": v, "label": lab, "desc": desc} for v, lab, desc in PRISM_QUANTS.get(family, PRISM_QUANTS["ternary"])]
        default = self.val.get("prism_quant", "") or PRISM_DEFAULT_QUANT.get(family, "pq2_0")
        body = [_select("prism_quant", "Quantization", options, default=default)]
        if family == "bonsai":
            body.append(note("Bonsai is a 1-bit model: Q1_0 is the only option.", "info"))
        else:
            body.append(note(
                "PQ2_0 is the recommended one (lightest). F16 gives the best quality but is several "
                "times larger. The choice decides which .gguf file gets downloaded.", "info"))
        body.append(text("if you already have something downloaded, we detect it and continue from there.", dim=True))
        return screen(
            "Quantization (Prism ML)",
            body,
            footer=[button("back", "Back"), button("next", "Next", kind="primary")],
            id="prism_quant", step=self._step_label(self.idx),
        )

    def _prism_size_screen(self) -> dict:
        body = [_select("prism_size", "Size", [
            {"value": "27B", "label": "27B", "desc": "maximum quality · a lot of RAM"},
            {"value": "8B", "label": "8B", "desc": "balanced (recommended)"},
            {"value": "4B", "label": "4B", "desc": "fast and light"},
            {"value": "1.7B", "label": "1.7B", "desc": "minimal, runs even on CPU"},
        ], default=self.val.get("prism_size", "8B"))]
        det = self._prism_detection()
        if det["model_ok"]:
            body.append(note(
                f"✓ The {self.val.get('prism_size', '8B')} ({det['quant_display']}) model is already downloaded — it will be reused.", "info"))
        else:
            body.append(text(
                f"   model: {'✓ downloaded' if det['model_ok'] else '✗ not downloaded'}   binary: {'✓ installed' if det['binary_ok'] else '✗ not installed'}", dim=True))
        return screen(
            "Model size (Prism ML)",
            body,
            footer=[button("back", "Back"), button("next", "Next", kind="primary")],
            id="prism_size", step=self._step_label(self.idx),
        )

    def _prism_overview_screen(self) -> dict:
        det = self._prism_detection()
        family = self.val.get("prism_family", "ternary")
        size = self.val.get("prism_size", "8B")
        lines = [
            f"llama-server binary: {'✓ already downloaded' if det['binary_ok'] else '✗ not downloaded'}",
            f"Model ({size}, {det['quant_display']}): {'✓ already downloaded' if det['model_ok'] else '✗ not downloaded'}",
        ]
        if det["binary_ok"] and det["model_ok"]:
            hint = ("Everything needed is already downloaded: you only need to enable the provider "
                    "and, if you want, start the server.")
        else:
            hint = ("The download comes directly from Hugging Face / GitHub and is resumable. "
                    "Anything you already had is detected and reused.")
        return screen(
            "Prism ML — what is missing",
            [
                text(f"Your choice: {family} family · {det['quant_display']} quantization · {size} model", accent=True),
                text("\n".join(lines), dim=True),
                note(hint, "info"),
                note(
                    "When you finish, run 'sayri-prismml wizard' to download and start the local server "
                    "({family}/{size}).",
                     "info"),
            ],
            footer=[button("back", "Back"), button("next", "Next", kind="primary")],
            id="prism_overview", step=self._step_label(self.idx),
        )

    def _persist_prism_cfg(self) -> None:
        """Persist family/quantization/size into prismml.json (the plugin config)."""
        try:
            fp = Path(paths.config_dir()) / "prismml.json"
            if fp.is_file():
                try:
                    data = json.loads(fp.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    data = {}
            else:
                data = {}
            for key in ("family", "size", "quant"):
                val = self.val.get(f"prism_{key}", "")
                if key == "quant":
                    val = "" if val == PRISM_DEFAULT_QUANT.get(self.val.get("prism_family", "ternary"), "pq2_0") else val
                    data[key] = val
                elif val:
                    data[key] = val
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:  # noqa: BLE001 - persisting must never break the wizard
            pass

    def _voice_screen(self) -> dict:
        lang = self.val.get("language", "es_ES")
        voices = self._voices_for(lang) or [{"value": "", "label": "(no voices for this language)", "desc": ""}]
        return screen(
            "Choose Sayri's voice",
            [
                _select("voice", "Voice (Piper)", voices, default=self.val.get("voice", "")),
                check("download_voice", "Download the voice now (~63 MB)", default=bool(self.val.get("download_voice", True))),
                note("You need the Piper binary (piper). If you don't have it, `sayri downloads piper` installs it.", "info"),
            ],
            footer=[button("back", "Back"), button("next", "Next", kind="primary")],
            id="voice", step=self._step_label(self.idx),
        )

    def _stt_screen(self) -> dict:
        return screen(
            "Speech recognition (whisper)",
            [
                _select("stt_size", "STT model", WHISPER_SIZES, default=self.val.get("stt_size", "base")),
                check("download_stt", "Download the model and whisper-cli binary now", default=bool(self.val.get("download_stt", False))),
                note("You can install it later with `sayri downloads model <size> <lang>` and `sayri downloads whisper`.", "info"),
            ],
            footer=[button("back", "Back"), button("next", "Next", kind="primary")],
            id="stt", step=self._step_label(self.idx),
        )

    def _review_screen(self) -> dict:
        lang = self.val.get("language", "es_ES")
        prov = self._provider_info(self.val.get("provider", ""))
        rows = [
            text("Your configuration summary", accent=True),
            sub("Language"),
            text(f"  {lang}   ·   voice: {self.val.get('voice') or '(no voice)'}   ·   STT: {self.val.get('stt_size')}"),
            sub("AI provider"),
            text(f"  {prov['label']}  →  base_url: {self.val.get('base_url')}  ·  model: {self.val.get('model')}"),
            sub("Voices / models"),
            text(f"  Piper voice: {'downloading…' if self.task.running else '✓ (downloaded)' if self._voice_done() else '⧖ (will download)'}"),
            text(f"  Whisper    : {'downloading…' if self.task.running else '✓ (ready)' if self._stt_done() else '⧖ (pending)'}"),
        ]
        if self._is_prism():
            det = self._prism_detection()
            rows += [
                sub("Prism ML"),
                text(f"  {self.val.get('prism_family')} / {self.val.get('prism_size')} · {det['quant_display']} "
                     f"· model: {'✓' if det['model_ok'] else '✗'} binary: {'✓' if det['binary_ok'] else '✗'}"),
            ]
        return screen(
            "Review before you finish",
            rows,
            footer=[button("back", "Back"), button("finish", "Finish", kind="primary"),
                    button("apply", "Apply & download", kind="secondary")],
            id="review", step=self._step_label(self.idx),
        )

    def _progress_screen(self) -> dict:
        info = self.task.poll()
        pct = info["progress"]
        lines = list(info["log"][-6:])
        body: list = [text("One moment…", accent=True)]
        if info["running"]:
            body.append(progress(info["log"][-1] if info["log"] else "Working…", pct))
            if lines:
                body.append(text("\n".join(lines), dim=True))
            body.append(sub("You can wait; we will continue when it finishes."))
            return screen("Working…", body, footer=[], id="busy", busy=True)
        if info["error"]:
            body.append(note(f"Error: {info['error']}", "error"))
            body.append(note("You can retry from the summary or leave it for later.", "info"))
            return screen("Something went wrong", body,
                          footer=[button("continue", "Continue", kind="primary")], id="task_error")
        body.append(note("All set!", "ok"))
        return screen("Done", body, footer=[button("continue", "Continue", kind="primary")], id="task_done")

    def _done_screen(self) -> dict:
        return screen(
            "Sayri is ready 🎉",
            [
                text("You can now talk with Sayri.", accent=True),
                text("Say «hey sayri» or run `sayri ui default` to open the UI.", dim=True),
                note("Reminder: if you configured Ollama, run `ollama serve` with the model downloaded first. You can change everything in Settings > AI Provider.", "info"),
            ],
            footer=[button("launch", "Open Sayri", kind="primary"), button("close", "Close")],
            id="done", done=True, step="✓",
        )

    def _finalize_done(self) -> dict:
        self._final = self._done_screen()
        return self._final

    def _task_prism(self, progress: Callable, log: Callable) -> bool:
        """Download the llama-server binary and the chosen GGUF via the plugin gateway."""
        gate = self._prism_gateway()
        if gate is None:
            log(["Prism ML plugin not found — install it from the Store first."])
            return False
        family = self.val.get("prism_family", "ternary")
        size = self.val.get("prism_size", "8B")
        quant = self.val.get("prism_quant", "") or ""
        ok = True
        log(["Installing llama-server binary…"])
        ok = self._run_gateway_phase(gate, ["install"], progress, log, 0.0, 0.4) and ok
        log([f"Downloading model {family}/{size} ({quant or 'auto'})…"])
        cmd = ["download", family, size]
        if quant:
            cmd += ["--quant", quant]
        ok = self._run_gateway_phase(gate, cmd, progress, log, 0.4, 1.0) and ok
        if ok:
            log(["Everything is downloaded ✓"])
        return ok

    def _prism_gateway(self) -> Optional[Path]:
        """Locate the Prism ML plugin gateway entrypoint on disk."""
        try:
            roots = [Path(paths.plugins_dir()), Path(paths.shared_plugins_dir())]
        except Exception:  # noqa: BLE001
            return None
        for root in roots:
            for manifest_path in root.glob("*/manifest.json"):
                try:
                    data = json.loads(manifest_path.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    continue
                if data.get("id") in ("sayri-prismml", "prismml"):
                    cand = manifest_path.parent / (data.get("entrypoint") or "gateway.py")
                    if cand.is_file():
                        return cand
        return None

    def _run_gateway_phase(self, gate: Path, args: list, progress: Callable,
                           log: Callable, lo: float, hi: float) -> bool:
        """Stream one gateway subcommand, mapping its percent output onto [lo, hi]."""
        try:
            proc = subprocess.Popen(
                [sys.executable, str(gate)] + list(args),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1)
        except Exception as exc:  # noqa: BLE001
            log([f"error: {exc}"])
            return False
        buf: list[str] = []
        pct = 0.0
        while True:
            ch = proc.stdout.read(1)  # type: ignore[union-attr]
            if not ch:
                break
            if ch in "\r\n":
                line = "".join(buf)
                buf.clear()
                s = line.strip()
                if not s:
                    continue
                m = re.findall(r"(\d{1,3})\s*%", s)
                if m:
                    pct = float(int(m[-1])) / 100.0
                    progress(lo + (hi - lo) * pct)
                else:
                    log([s])
            else:
                buf.append(ch)
        rc = proc.wait()
        return rc == 0

    def _prism_ready(self) -> bool:
        det = self._prism_detection()
        return bool(det["binary_ok"] and det["model_ok"])

    # ------------------------------------------------------------ dispatch
    def dispatch(self, event: dict) -> Optional[dict]:
        t = event.get("type")

        if self.task.running:
            if t == "poll":
                return self._poll_handle()
            return self._progress_screen()

        if t == "poll":
            return self.render()

        if t == "action":
            return self._on_action(event.get("widget", ""), event.get("value", {}))

        if t == "submit":
            return self._on_submit(event.get("value", {}))

        return self.render()

    def _poll_handle(self) -> dict:
        # still running — keep showing the progress screen
        if self.task.running:
            return self._progress_screen()
        # task finished (or errored) while a busy screen was up
        if self.task.poll()["error"]:
            self._final = self._progress_screen()
            self._final["busy"] = False
            return self._final
        cb = self._on_task_done
        if cb:
            self._on_task_done = None
            try:
                cb(self.task.poll()["result"])
            except Exception as exc:  # noqa: BLE001
                self._final = screen("Error", [note(str(exc), "error")],
                                     footer=[button("close", "Close")], done=True)
                return self._final
        return self.render()

    def _on_action(self, wid: str, value: Optional[dict] = None) -> Optional[dict]:
        if wid in ("start",):
            self.idx = 1
            return self.render()
        if wid in ("skip",):
            self._write_ui_flag()
            return self._finalize_done()
        if wid in ("next", "siguiente"):
            return self._on_submit(value or {})
        if wid in ("back",):
            self.idx = max(0, self.idx - 1)
            if self.idx == 2:
                # repopulate provider details from the last chosen provider
                p = self._provider_info(self.val.get("provider", "ollama"))
                self.val["base_url"] = self.val.get("base_url") or p["base_url"]
                self.val["model"] = self.val.get("model") or p["model"]
            return self.render()
        if wid in ("continue",):
            target = self._review_idx()
            if self.idx < target:
                self.idx = target
            return self.render()
        if wid in ("close", "quit"):
            return None
        if wid == "launch":
            self._launch_ui()
            return None
        if wid == "finish":
            self._apply_config()
            self._write_ui_flag()
            return self._finalize_done()
        if wid == "apply":
            self._apply_config()
            if self._is_prism() and not self._prism_ready():
                self._launch_task("Prism ML — downloading binaries & model…", self._task_prism, lambda _r: None)
                return self._progress_screen()
            if self._start_pending_downloads():
                return self._progress_screen()
            return self.render()
        return self.render()

    def _on_submit(self, value: dict) -> Optional[dict]:
        if self.idx == 1:  # language
            self.val.update(value)
            self.idx = 2
            return self.render()
        if self.idx == 2:  # provider
            self.val.update(value)
            p = self._provider_info(self.val.get("provider", ""))
            if not self.val.get("base_url"):
                self.val["base_url"] = p["base_url"]
            if not self.val.get("model"):
                self.val["model"] = p["model"]
            self.idx = 3
            return self.render()
        if self.idx == 3:  # prism family (Prism) / provider details
            if self._is_prism():
                fam = value.get("prism_family") or self.val.get("prism_family", "ternary")
                self.val["prism_family"] = str(fam)
                if self.val.get("prism_quant", "") not in [v for v, _, _ in PRISM_QUANTS.get(str(fam), [])]:
                    self.val["prism_quant"] = ""
                self._persist_prism_cfg()
                self.idx = 4
            else:
                self.val.update(value)
                self.idx = 4
            return self.render()
        if self._is_prism():
            if self.idx == 4:  # prism quant
                if value.get("prism_quant"):
                    self.val["prism_quant"] = str(value["prism_quant"])
                self._persist_prism_cfg()
                self.idx = 5
                return self.render()
            if self.idx == 5:  # prism size
                if value.get("prism_size"):
                    self.val["prism_size"] = str(value["prism_size"])
                self._persist_prism_cfg()
                self.idx = 6
                return self.render()
            if self.idx == 6:  # prism overview → provider details
                self.idx = 7
                return self.render()
            if self.idx == 7:  # provider details
                self.val.update(value)
                self.idx = 8
                return self.render()
            if self.idx == 8:  # voice
                return self._submit_voice(value, next_idx=9)
            if self.idx == 9:  # stt
                return self._submit_stt(value, next_idx=10)
        else:
            if self.idx == 4:  # voice
                return self._submit_voice(value, next_idx=5)
            if self.idx == 5:  # stt
                return self._submit_stt(value, next_idx=6)
        return self.render()

    def _submit_voice(self, value: dict, next_idx: int) -> Optional[dict]:
        self.val.update(value)
        self.idx = next_idx
        if value.get("download_voice") and value.get("voice"):
            self._launch_task("Downloading voice…", self._task_voice, self._on_voice_done)
            return self._progress_screen()
        return self.render()

    def _submit_stt(self, value: dict, next_idx: int) -> Optional[dict]:
        self.val.update(value)
        self.idx = next_idx
        if value.get("download_stt"):
            self._launch_task("Installing whisper…", self._task_stt, lambda _r: None)
            return self._progress_screen()
        return self.render()

    # ------------------------------------------------------------ helpers
    def _provider_info(self, value: str) -> dict:
        for p in available_providers(PROVIDERS):
            if p["value"] == value:
                return p
        return {"value": "custom", "label": "Other (OpenAI-compatible)",
                "base_url": "", "model": "", "key": False}

    def _resolve_language(self, lang: str) -> str:
        """Map the chosen UI language to a Piper catalog key (es_ES, en_GB…)."""
        try:
            from . import downloads
            from .cli import _resolve_piper_lang
        except Exception:  # noqa: BLE001
            return ""
        lang = (lang or "es_ES").replace("-", "_")
        if lang == "es":
            lang = "es_ES"
        if lang == "en":
            lang = "en_US"
        if lang in downloads.PIPER_VOICES:
            return lang
        return _resolve_piper_lang(lang, downloads.PIPER_VOICES) or ""

    def _lang_tag(self) -> str:
        """STT-style language tag from the chosen UI language (es, en…)."""
        lang = self.val.get("language", "es_ES")
        if lang in ("en_US", "en_GB"):
            return "en"
        return lang.split("_")[0]

    def _voices_for(self, lang: str) -> list:
        try:
            from . import downloads
        except Exception:  # noqa: BLE001
            return []
        lang = self._resolve_language(lang)
        if not lang:
            return []
        opts = []
        for v in downloads.PIPER_VOICES[lang]:
            opts.append({"value": v["voice"], "label": f"{v['voice']} · {v['quality']}",
                         "desc": v.get("size", "")})
        return opts

    def _voice_done(self) -> bool:
        try:
            from . import downloads
        except Exception:  # noqa: BLE001
            return False
        lang = self._resolve_language(self.val.get("language", "es_ES"))
        voice = self.val.get("voice", "")
        if not lang or not voice:
            return False
        return downloads.has_piper_voice(lang, voice, self._voice_quality())

    def _voice_quality(self) -> str:
        """Exact Piper quality for the currently selected voice."""
        try:
            from . import downloads
        except Exception:  # noqa: BLE001
            return "medium"
        lang = self._resolve_language(self.val.get("language", "es_ES"))
        voice = self.val.get("voice", "")
        if not lang or not voice:
            return "medium"
        for e in downloads.PIPER_VOICES.get(lang, []):
            if e["voice"] == voice:
                return e["quality"]
        return "medium"

    def _stt_done(self) -> bool:
        try:
            from . import downloads
        except Exception:  # noqa: BLE001
            return False
        return downloads.has_whisper_model(self.val.get("stt_size", "base"), self._lang_tag())

    def _task_voice(self, progress: Callable, log: Callable) -> bool:
        from . import downloads, paths
        lang = self._resolve_language(self.val.get("language", "es_ES"))
        voice = self.val.get("voice", "")
        if not lang or not voice:
            return False
        quality = self._voice_quality()
        if downloads.has_piper_voice(lang, voice, quality):
            log(["Voice already downloaded ✓"])
            return True
        log([f"Downloading Piper voice {lang}/{voice} ({quality})…"])
        ok = downloads.download_piper_voice(lang, voice, quality, progress=progress)
        if ok:
            log(["Voice installed ✓"])
        if not paths.find_binary("piper"):
            log(["Installing piper binary…"])
            try:
                downloads.install_piper(progress=progress)
                log(["Piper installed ✓"])
            except Exception as exc:  # noqa: BLE001
                log([f"piper not installed ({exc}) — use `sayri downloads piper` later"])
        return ok

    def _task_stt(self, progress: Callable, log: Callable) -> bool:
        from . import downloads
        size = self.val.get("stt_size", "base")
        lang = self._lang_tag()
        log(["Ensuring whisper-cli binary…"])
        try:
            downloads.install_whisper_cli(progress=progress)
        except Exception as exc:  # noqa: BLE001
            log([f"whisper-cli not downloaded ({exc}) — use `sayri downloads whisper` later"])
        if downloads.has_whisper_model(size, lang):
            log(["Whisper model already downloaded ✓"])
            return True
        log([f"Downloading whisper model {size} ({lang})…"])
        ok = downloads.download_whisper_model(size, lang, progress=progress)
        if ok:
            log(["Model installed ✓"])
        return ok

    def _launch_task(self, label: str, fn: DownloadFn, on_done: Callable) -> None:
        self._on_task_done = on_done
        self.task.start(label, fn)

    def _on_voice_done(self, _result: Any) -> None:
        pass  # voice step already advanced; nothing else to do

    def _start_pending_downloads(self) -> bool:
        """Launch the downloads the user ticked but that are still missing."""
        if self.val.get("download_voice") and self.val.get("voice") and not self._voice_done():
            self._launch_task("Downloading voice…", self._task_voice, self._on_voice_done)
            return True
        if self.val.get("download_stt") and not self._stt_done():
            self._launch_task("Installing whisper…", self._task_stt, lambda _r: None)
            return True
        return False

    def _apply_config(self) -> None:
        from . import config as cfg
        lang = self.val.get("language", "es_ES")
        cfg.config.set("stt", "language", self._lang_tag(), persist=False)
        cfg.config.set("tts", "language", lang, persist=False)
        if self.val.get("voice"):
            cfg.config.set("tts", "voice", self.val["voice"], persist=False)
            cfg.config.set("tts", "quality", self._voice_quality(), persist=False)
        if self.val.get("stt_size"):
            cfg.config.set("stt", "model_size", self.val["stt_size"], persist=False)
        cfg.config.set("provider", "base_url", self.val.get("base_url", "http://127.0.0.1:11434/v1"), persist=False)
        cfg.config.set("provider", "model", self.val.get("model", "llama3.2"), persist=False)
        if self.val.get("api_key"):
            cfg.config.set("provider", "api_key", self.val["api_key"], persist=False)
        cfg.config.save()

    def _write_ui_flag(self) -> None:
        try:
            from . import config as cfg
            cfg.config.set("ui", "setup_complete", True, persist=True)
        except Exception:  # noqa: BLE001
            pass

    def _launch_ui(self) -> None:
        try:
            from . import sysinfo
            subprocess.Popen(
                [sys.executable, "-m", "sayri", "ui", "start", "default"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **sysinfo.spawn_flags(),
            )
        except Exception:  # noqa: BLE001
            pass


def welcome_app() -> WelcomeApp:
    return WelcomeApp()


def run_cli(stream: Any = None, out: Any = None) -> int:
    from .xui import run_cli as _run_cli
    return _run_cli(WelcomeApp(), stream=stream, out=out)


def build_html(transport: Any = None) -> str:
    """Self-contained HTML for the welcome wizard (WebKit bridge or browser)."""
    from .xui import wizard_page
    app = WelcomeApp()
    transport = transport if transport is not None else {"kind": "none"}
    return wizard_page(app.render(), title="Sayri · Welcome", transport=transport)


def install_html_to(dirpath: Optional[str] = None) -> str:
    """Write the wizard page to the xui dir served by the sayri:// scheme."""
    target = os.path.join(dirpath or os.path.join(paths.state_dir(), "xui"), "welcome.html")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    html = build_html({"kind": "webkit"})
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(html)
    os.replace(tmp, target)
    return target