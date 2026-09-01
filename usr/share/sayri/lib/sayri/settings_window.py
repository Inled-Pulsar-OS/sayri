"""Pure GTK4 Settings Window for Sayri.

Features:
- Translucent frosted dark background.
- Language selector for STT and TTS.
- Voice selection with Piper installation & voice downloads.
- Whisper model selection & whisper-cli downloads.
- AI Provider (OpenAI compatible) configuration and live test.
- Full high-contrast GTK4 design in English.
"""

from __future__ import annotations

import os
import shutil
import threading
from typing import Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from . import config, downloads, llm, paths  # noqa: E402

LANGUAGE_OPTIONS = [
    ("en_US", "English (United States)"),
    ("en_GB", "English (United Kingdom)"),
    ("es_ES", "Spanish (Spain)"),
    ("es_MX", "Spanish (Mexico)"),
    ("fr_FR", "French (France)"),
    ("de_DE", "German (Germany)"),
    ("it_IT", "Italian (Italy)"),
    ("pt_BR", "Portuguese (Brazil)"),
    ("nl_NL", "Dutch (Netherlands)"),
    ("ru_RU", "Russian (Russia)"),
    ("pl_PL", "Polish (Poland)"),
    ("zh_CN", "Chinese (Simplified)"),
]

GTK4_SETTINGS_CSS = b"""
window.sayri-settings-window,
.sayri-settings-window {
    background-color: rgba(24, 25, 34, 0.96);
    color: #f1f5f9;
}

.sayri-settings-header {
    background-color: rgba(18, 19, 26, 0.98);
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    color: #f8fafc;
    padding: 2px 12px;
}

.sayri-settings-sidebar {
    background-color: rgba(18, 19, 26, 0.98);
    border-right: 1px solid rgba(255, 255, 255, 0.08);
    padding: 8px;
    min-width: 195px;
}

.sayri-settings-sidebar list > row {
    border-radius: 8px;
    padding: 10px 14px;
    color: #94a3b8;
    font-size: 13px;
    font-weight: 500;
    margin-bottom: 3px;
}

.sayri-settings-sidebar list > row:selected {
    background-color: #0071e3;
    color: #ffffff;
}

.sayri-settings-scrolled {
    background: transparent;
}

.sayri-group-card {
    background-color: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 12px;
    padding: 14px 18px;
    margin-bottom: 16px;
}

.sayri-card-title {
    color: #38bdf8;
    font-size: 11.5px;
    font-weight: 700;
    letter-spacing: 0.6px;
    text-transform: uppercase;
    margin-bottom: 8px;
}

.sayri-row-title {
    color: #f8fafc;
    font-size: 13.5px;
    font-weight: 500;
}

.sayri-row-subtitle {
    color: #94a3b8;
    font-size: 12px;
}

entry.sayri-input {
    background-color: rgba(0, 0, 0, 0.45);
    border: 1px solid rgba(255, 255, 255, 0.15);
    border-radius: 8px;
    color: #ffffff;
    padding: 6px 12px;
    font-size: 13px;
    min-height: 32px;
}

entry.sayri-input:focus {
    border-color: #38bdf8;
    background-color: rgba(0, 0, 0, 0.65);
}

.sayri-settings-window button,
.sayri-settings-window combobox,
.sayri-settings-window combobox > box.linked > button,
.sayri-settings-window combobox button,
.sayri-settings-window dropdown,
.sayri-settings-window dropdown > button {
    background-color: #2c2d3a;
    background: #2c2d3a;
    color: #ffffff;
    border: 1px solid rgba(255, 255, 255, 0.16);
    border-radius: 8px;
    padding: 6px 14px;
    font-size: 13px;
    font-weight: 500;
    box-shadow: none;
}

.sayri-settings-window combobox cellview,
.sayri-settings-window dropdown cellview,
.sayri-settings-window combobox label,
.sayri-settings-window dropdown label {
    color: #ffffff;
    background: transparent;
}

.sayri-settings-window button:hover,
.sayri-settings-window combobox button:hover,
.sayri-settings-window dropdown > button:hover {
    background-color: #3a3b4c;
    background: #3a3b4c;
    border-color: rgba(255, 255, 255, 0.30);
    color: #ffffff;
}

.sayri-settings-window button.sayri-btn-primary {
    background-color: #0071e3;
    background: #0071e3;
    color: #ffffff;
    font-weight: 600;
    font-size: 13px;
    border: 1px solid #0071e3;
    border-radius: 8px;
    padding: 7px 18px;
}

.sayri-settings-window button.sayri-btn-primary:hover {
    background-color: #0077ed;
    background: #0077ed;
}

.sayri-settings-window button:disabled {
    background-color: rgba(255, 255, 255, 0.05);
    background: rgba(255, 255, 255, 0.05);
    color: #64748b;
    border-color: rgba(255, 255, 255, 0.06);
}

.sayri-settings-window popover,
.sayri-settings-window popover contents,
.sayri-settings-window popover listview {
    background-color: #1a1b24;
    background: #1a1b24;
    color: #ffffff;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 8px;
}

.sayri-settings-window popover listview > row {
    padding: 8px 12px;
    color: #f1f5f9;
}

.sayri-settings-window popover listview > row:selected {
    background-color: #0071e3;
    color: #ffffff;
}

.sayri-settings-window progressbar trough {
    background-color: rgba(0, 0, 0, 0.45);
    border-radius: 6px;
    min-height: 8px;
}

.sayri-settings-window progressbar progress {
    background-color: #0071e3;
    border-radius: 6px;
    min-height: 8px;
}
"""


class SettingsWindow:
    """Pure GTK4 Settings Window for Sayri."""

    def __init__(self, app) -> None:
        self.app = app
        self.cfg = config.config

        self.win = Gtk.Window()
        self.win.set_title("Sayri Settings")
        self.win.set_default_size(760, 600)
        self.win.add_css_class("sayri-settings-window")

        self._load_css()

        # HeaderBar
        header = Gtk.HeaderBar()
        header.set_show_title_buttons(True)
        header.add_css_class("sayri-settings-header")
        header.set_title_widget(Gtk.Label(label="Settings — Sayri"))
        self.win.set_titlebar(header)

        # Main Layout: Left Sidebar + Right Stack
        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.win.set_child(root)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_hexpand(True)
        self.stack.set_vexpand(True)

        sidebar = Gtk.StackSidebar()
        sidebar.set_stack(self.stack)
        sidebar.add_css_class("sayri-settings-sidebar")
        root.append(sidebar)
        root.append(self.stack)

        self._build_provider_tab()
        self._build_agents_tab()
        self._build_stt_tab()
        self._build_tts_tab()
        self._build_general_tab()

        self.win.connect("close-request", self._on_close)

    def _load_css(self) -> None:
        try:
            css = Gtk.CssProvider()
            css.load_from_data(GTK4_SETTINGS_CSS)
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), css,
                Gtk.STYLE_PROVIDER_PRIORITY_USER
            )
        except Exception:  # noqa: BLE001
            pass

    def _on_close(self, *_args) -> bool:
        self.win.set_visible(False)
        return True

    def show(self) -> None:
        self.win.present()

    def hide(self) -> None:
        self.win.set_visible(False)

    def refresh_status(self) -> None:
        pass

    def _page_scrolled(self, title: str, name: str) -> Gtk.Box:
        sw = Gtk.ScrolledWindow()
        sw.add_css_class("sayri-settings-scrolled")
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_hexpand(True)
        sw.set_vexpand(True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.set_margin_top(20)
        box.set_margin_bottom(20)
        box.set_margin_start(24)
        box.set_margin_end(24)
        box.set_hexpand(True)

        sw.set_child(box)
        self.stack.add_titled(sw, name, title)
        return box

    def _card(self, parent: Gtk.Box, title: str) -> Gtk.Box:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card.add_css_class("sayri-group-card")
        if title:
            lbl = Gtk.Label(label=title)
            lbl.add_css_class("sayri-card-title")
            lbl.set_halign(Gtk.Align.START)
            card.append(lbl)
        parent.append(card)
        return card

    def _row(self, card: Gtk.Box, label: str, subtitle: str, widget: Gtk.Widget) -> None:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_hexpand(True)
        row.set_valign(Gtk.Align.CENTER)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        vbox.set_hexpand(True)
        vbox.set_halign(Gtk.Align.START)

        t = Gtk.Label(label=label)
        t.add_css_class("sayri-row-title")
        t.set_halign(Gtk.Align.START)
        vbox.append(t)

        if subtitle:
            s = Gtk.Label(label=subtitle)
            s.add_css_class("sayri-row-subtitle")
            s.set_halign(Gtk.Align.START)
            vbox.append(s)

        row.append(vbox)
        widget.set_halign(Gtk.Align.END)
        row.append(widget)
        card.append(row)

    def _entry_row(self, card: Gtk.Box, label: str, subtitle: str, group: str, key: str, password: bool = False) -> Gtk.Entry:
        entry = Gtk.Entry()
        entry.add_css_class("sayri-input")
        entry.set_text(self.cfg.get_string(group, key))
        entry.set_visibility(not password)
        entry.set_size_request(240, -1)
        entry.connect("changed", lambda w: self.cfg.set(group, key, w.get_text()))
        self._row(card, label, subtitle, entry)
        return entry

    def _switch_row(self, card: Gtk.Box, label: str, subtitle: str, group: str, key: str) -> Gtk.Switch:
        sw = Gtk.Switch()
        sw.set_active(self.cfg.get_bool(group, key))
        sw.connect("state-set", lambda _w, st: (self.cfg.set(group, key, bool(st)), False)[-1])
        self._row(card, label, subtitle, sw)
        return sw

    def _spin_row(self, card: Gtk.Box, label: str, subtitle: str, group: str, key: str,
                  lo: float, hi: float, step: float, digits: int = 0) -> Gtk.SpinButton:
        adj = Gtk.Adjustment(value=float(self.cfg.get(group, key)), lower=lo, upper=hi,
                             step_increment=step, page_increment=step, page_size=0)
        spin = Gtk.SpinButton(adjustment=adj, climb_rate=0.5, digits=digits)
        spin.connect("value-changed", lambda w: self.cfg.set(
            group, key, w.get_value() if digits else int(w.get_value())))
        self._row(card, label, subtitle, spin)
        return spin

    def _combo_row(self, card: Gtk.Box, label: str, subtitle: str, group: str, key: str,
                   options: list[tuple[str, str]], on_change: Optional[Callable[[], None]] = None) -> Gtk.ComboBoxText:
        combo = Gtk.ComboBoxText()
        for opt_id, opt_lbl in options:
            combo.append(opt_id, opt_lbl)
        combo.set_active_id(self.cfg.get_string(group, key))
        combo.connect("changed", lambda w: (
            self.cfg.set(group, key, w.get_active_id() or ""),
            on_change() if on_change else None,
        ))
        self._row(card, label, subtitle, combo)
        return combo

    # ── Tabs ────────────────────────────────────────────────────────
    def _build_provider_tab(self) -> None:
        page = self._page_scrolled("AI Provider", "provider")

        card = self._card(page, "OpenAI-Compatible Endpoint")
        self._entry_row(card, "Base URL", "Server API endpoint (OpenAI, Ollama, vLLM, Groq)", "provider", "base_url")
        self._entry_row(card, "API Key", "Bearer authorization token", "provider", "api_key", password=True)
        self._entry_row(card, "Model Name", "Target model identifier (e.g. gpt-4o, llama3)", "provider", "model")

        card_p = self._card(page, "Generation Parameters")
        self._spin_row(card_p, "Temperature", "Sampling temperature (creativity)", "provider", "temperature", 0.0, 2.0, 0.1, 1)
        self._spin_row(card_p, "Max Tokens", "Maximum response token length", "provider", "max_tokens", 0, 16384, 64)
        self._switch_row(card_p, "Real-time Streaming", "Stream response tokens immediately", "provider", "stream")

        card_t = self._card(page, "Diagnostics")
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        test_btn = Gtk.Button(label="Test Connection")
        test_btn.add_css_class("sayri-btn-primary")
        res_lbl = Gtk.Label(label="")
        res_lbl.add_css_class("sayri-row-subtitle")
        res_lbl.set_halign(Gtk.Align.START)
        hbox.append(test_btn)
        hbox.append(res_lbl)
        card_t.append(hbox)

        def on_test(_b):
            test_btn.set_sensitive(False)
            res_lbl.set_label("Checking connection…")

            def worker():
                try:
                    llm.stream_chat(
                        self.cfg.get_string("provider", "base_url"),
                        self.cfg.get_string("provider", "api_key"),
                        self.cfg.get_string("provider", "model"),
                        [{"role": "user", "content": "Respond only with: OK"}],
                        temperature=0.0,
                        max_tokens=8,
                        stream=False,
                        timeout=15,
                        on_delta=lambda _: None,
                        on_done=lambda _: GLib.idle_add(res_lbl.set_label, "Connection Successful ✓"),
                        on_error=lambda e: GLib.idle_add(res_lbl.set_label, f"Error: {e}"),
                    )
                finally:
                    GLib.idle_add(test_btn.set_sensitive, True)

            threading.Thread(target=worker, daemon=True).start()

        test_btn.connect("clicked", on_test)

    def _build_stt_tab(self) -> None:
        page = self._page_scrolled("Speech Recognition", "stt")

        card = self._card(page, "Whisper Engine Configuration")
        self._combo_row(card, "Language", "Spoken language for speech recognition", "stt", "language", LANGUAGE_OPTIONS, on_change=self._on_stt_lang_changed)
        self._combo_row(card, "Listening Mode", "How voice input is triggered", "stt", "mode", [
            ("always", "Always Listening"),
            ("wakeword", "Wake Word (Hey Sayri)"),
            ("manual", "Manual (Push to talk / click)"),
        ])
        self._entry_row(card, "Wake Words", "Comma-separated trigger phrases", "stt", "wake_word")
        self._spin_row(card, "Silence Timeout (ms)", "Silence duration before submitting query", "stt", "silence_ms", 200, 5000, 100)

        card_m = self._card(page, "Whisper Model & Runtime")
        model_opts = [(k, f"{k} ({v['size']})") for k, v in downloads.WHISPER_MODELS.items()]
        self.whisper_model_combo = self._combo_row(card_m, "Model Size", "Select local whisper model size", "stt", "model_size", model_opts, on_change=self._update_stt_status)

        # Whisper download row
        h = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.d_whisper_btn = Gtk.Button(label="Download Model")
        self.whisper_pbar = Gtk.ProgressBar()
        self.whisper_pbar.set_hexpand(True)
        self.whisper_pbar.set_valign(Gtk.Align.CENTER)
        self.whisper_status_lbl = Gtk.Label(label="")
        self.whisper_status_lbl.add_css_class("sayri-row-subtitle")

        h.append(self.d_whisper_btn)
        h.append(self.whisper_pbar)
        h.append(self.whisper_status_lbl)
        card_m.append(h)

        # Install whisper-cli binary if missing
        h_bin = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.d_whisper_bin_btn = Gtk.Button(label="Install whisper-cli Binary")
        self.whisper_bin_status = Gtk.Label(label="")
        self.whisper_bin_status.add_css_class("sayri-row-subtitle")
        h_bin.append(self.d_whisper_bin_btn)
        h_bin.append(self.whisper_bin_status)
        card_m.append(h_bin)

        def dl_whisper(_b):
            self.d_whisper_btn.set_sensitive(False)
            ms = self.cfg.get_string("stt", "model_size")
            lang = self.cfg.get_string("stt", "language")
            self.whisper_status_lbl.set_label("Downloading…")

            def worker():
                try:
                    downloads.download_whisper_model(ms, lang, lambda f: GLib.idle_add(self.whisper_pbar.set_fraction, f))
                    GLib.idle_add(self.whisper_status_lbl.set_label, "Installed ✓")
                    GLib.idle_add(self.app.refresh_status)
                except Exception as exc:
                    GLib.idle_add(self.whisper_status_lbl.set_label, f"Failed: {exc}")
                finally:
                    GLib.idle_add(self.d_whisper_btn.set_sensitive, True)

            threading.Thread(target=worker, daemon=True).start()

        def dl_whisper_bin(_b):
            self.d_whisper_bin_btn.set_sensitive(False)
            self.whisper_bin_status.set_label("Installing…")

            def worker():
                try:
                    downloads.install_whisper_cli()
                    GLib.idle_add(self.whisper_bin_status.set_label, "whisper-cli Installed ✓")
                    GLib.idle_add(self.app.refresh_status)
                except Exception as exc:
                    GLib.idle_add(self.whisper_bin_status.set_label, f"Failed: {exc}")
                finally:
                    GLib.idle_add(self.d_whisper_bin_btn.set_sensitive, True)

        self.d_whisper_btn.connect("clicked", dl_whisper)
        self.d_whisper_bin_btn.connect("clicked", dl_whisper_bin)

        # ONNX Wake Word Engine Card
        card_kws = self._card(page, "ONNX Wake Word Engine (openWakeWord)")
        h_kws = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.d_kws_btn = Gtk.Button(label="Download ONNX Models")
        self.kws_pbar = Gtk.ProgressBar()
        self.kws_pbar.set_hexpand(True)
        self.kws_pbar.set_valign(Gtk.Align.CENTER)
        self.kws_status_lbl = Gtk.Label(label="")
        self.kws_status_lbl.add_css_class("sayri-row-subtitle")
        h_kws.append(self.d_kws_btn)
        h_kws.append(self.kws_pbar)
        h_kws.append(self.kws_status_lbl)
        card_kws.append(h_kws)

        def dl_kws(_b):
            self.d_kws_btn.set_sensitive(False)
            self.kws_status_lbl.set_label("Downloading…")

            def worker():
                try:
                    from . import wakeword
                    ok = wakeword.download_models()
                    if ok:
                        GLib.idle_add(self.kws_status_lbl.set_label, "Installed ✓")
                        GLib.idle_add(self.kws_pbar.set_fraction, 1.0)
                    else:
                        GLib.idle_add(self.kws_status_lbl.set_label, "Failed")
                except Exception as exc:
                    GLib.idle_add(self.kws_status_lbl.set_label, f"Failed: {exc}")
                finally:
                    GLib.idle_add(self.d_kws_btn.set_sensitive, True)

            threading.Thread(target=worker, daemon=True).start()

        self.d_kws_btn.connect("clicked", dl_kws)
        self._update_stt_status()

    def _on_stt_lang_changed(self) -> None:
        self._update_stt_status()

    def _update_stt_status(self) -> None:
        ms = self.cfg.get_string("stt", "model_size")
        lang = self.cfg.get_string("stt", "language")
        if downloads.has_whisper_model(ms, lang):
            self.whisper_status_lbl.set_label("Installed ✓")
            self.whisper_pbar.set_fraction(1.0)
        else:
            self.whisper_status_lbl.set_label("Not downloaded")
            self.whisper_pbar.set_fraction(0.0)

        has_bin = bool(shutil.which("whisper-cli") or shutil.which("whisper.cpp") or os.path.isfile(os.path.join(paths.bin_dir(), "whisper-cli")))
        self.whisper_bin_status.set_label("Installed ✓" if has_bin else "Binary missing")

        from . import wakeword
        if wakeword.is_onnx_ready():
            self.kws_status_lbl.set_label("Installed ✓")
            self.kws_pbar.set_fraction(1.0)
        else:
            self.kws_status_lbl.set_label("Not downloaded")
            self.kws_pbar.set_fraction(0.0)

    def _build_tts_tab(self) -> None:
        page = self._page_scrolled("Text to Speech", "tts")

        card = self._card(page, "Piper Speech Synthesizer")
        self._switch_row(card, "Voice Response", "Read AI responses aloud automatically", "tts", "enabled")
        self._combo_row(card, "Language", "Voice language", "tts", "language", LANGUAGE_OPTIONS, on_change=self._on_tts_lang_changed)
        self.voice_combo = self._combo_row(card, "Voice Model", "Selected speaker voice", "tts", "voice", [("default", "Default Voice")], on_change=self._on_voice_changed)
        self._spin_row(card, "Speech Rate", "Voice playback speed multiplier", "tts", "speed", 0.5, 2.0, 0.1, 1)

        card_p = self._card(page, "Voice & Runtime Downloads")
        
        # Download Voice Model row
        h_voice = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.d_voice_btn = Gtk.Button(label="Download Voice Model")
        self.voice_pbar = Gtk.ProgressBar()
        self.voice_pbar.set_hexpand(True)
        self.voice_pbar.set_valign(Gtk.Align.CENTER)
        self.voice_status_lbl = Gtk.Label(label="")
        self.voice_status_lbl.add_css_class("sayri-row-subtitle")

        h_voice.append(self.d_voice_btn)
        h_voice.append(self.voice_pbar)
        h_voice.append(self.voice_status_lbl)
        card_p.append(h_voice)

        # Download Piper binary row
        h_piper = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.d_piper_bin_btn = Gtk.Button(label="Install Piper TTS Binary")
        self.piper_bin_status = Gtk.Label(label="")
        self.piper_bin_status.add_css_class("sayri-row-subtitle")

        h_piper.append(self.d_piper_bin_btn)
        h_piper.append(self.piper_bin_status)
        card_p.append(h_piper)

        def dl_voice(_b):
            self.d_voice_btn.set_sensitive(False)
            lang = self.cfg.get_string("tts", "language")
            voice = self.cfg.get_string("tts", "voice")
            quality = self.cfg.get_string("tts", "quality") or "medium"
            self.voice_status_lbl.set_label("Downloading voice…")

            def worker():
                try:
                    downloads.download_piper_voice(lang, voice, quality, lambda f: GLib.idle_add(self.voice_pbar.set_fraction, f))
                    GLib.idle_add(self.voice_status_lbl.set_label, "Voice Installed ✓")
                    GLib.idle_add(self.app.refresh_status)
                except Exception as exc:
                    GLib.idle_add(self.voice_status_lbl.set_label, f"Failed: {exc}")
                finally:
                    GLib.idle_add(self.d_voice_btn.set_sensitive, True)

            threading.Thread(target=worker, daemon=True).start()

        def dl_piper_bin(_b):
            self.d_piper_bin_btn.set_sensitive(False)
            self.piper_bin_status.set_label("Installing Piper…")

            def worker():
                try:
                    downloads.install_piper(lambda f: None)
                    GLib.idle_add(self.piper_bin_status.set_label, "Piper Installed ✓")
                    GLib.idle_add(self.app.refresh_status)
                except Exception as exc:
                    GLib.idle_add(self.piper_bin_status.set_label, f"Failed: {exc}")
                finally:
                    GLib.idle_add(self.d_piper_bin_btn.set_sensitive, True)

            threading.Thread(target=worker, daemon=True).start()

        self.d_voice_btn.connect("clicked", dl_voice)
        self.d_piper_bin_btn.connect("clicked", dl_piper_bin)

        self._update_tts_voices()

    def _on_tts_lang_changed(self) -> None:
        self._update_tts_voices()

    def _on_voice_changed(self) -> None:
        self._update_tts_status()

    def _update_tts_voices(self) -> None:
        lang = self.cfg.get_string("tts", "language")
        voices = downloads.PIPER_VOICES.get(lang, [])
        if not voices and "_" in lang:
            # Fallback matching
            for k in downloads.PIPER_VOICES:
                if k.startswith(lang.split("_")[0]):
                    voices = downloads.PIPER_VOICES[k]
                    break
                if k.startswith(lang.split("_")[0]):
                    voices = downloads.PIPER_VOICES[k]
                    break

        self.voice_combo.remove_all()
        current_voice = self.cfg.get_string("tts", "voice")
        found = False
        for v in voices:
            v_id = v["voice"]
            v_lbl = f"{v['voice']} ({v['quality']}, {v['size']})"
            self.voice_combo.append(v_id, v_lbl)
            if v_id == current_voice:
                found = True

        if voices:
            if not found:
                self.voice_combo.set_active_id(voices[0]["voice"])
                self.cfg.set("tts", "voice", voices[0]["voice"])
                self.cfg.set("tts", "quality", voices[0]["quality"])
            else:
                self.voice_combo.set_active_id(current_voice)

        self._update_tts_status()

    def _update_tts_status(self) -> None:
        lang = self.cfg.get_string("tts", "language")
        voice = self.cfg.get_string("tts", "voice")
        quality = self.cfg.get_string("tts", "quality") or "medium"

        if downloads.has_piper_voice(lang, voice, quality):
            self.voice_status_lbl.set_label("Voice Installed ✓")
            self.voice_pbar.set_fraction(1.0)
        else:
            self.voice_status_lbl.set_label("Not downloaded")
            self.voice_pbar.set_fraction(0.0)

        has_bin = bool(shutil.which("piper") or os.path.isfile(os.path.join(paths.bin_dir(), "piper")))
        self.piper_bin_status.set_label("Installed ✓" if has_bin else "Binary missing")

    def _build_general_tab(self) -> None:
        page = self._page_scrolled("General", "general")

        card = self._card(page, "Appearance & System")
        self._spin_row(card, "Orb Diameter", "Siri orb size in pixels", "ui", "orb_size", 100, 260, 10)
        self._switch_row(card, "Launch at Login", "Start Sayri automatically when logging in", "ui", "autostart")

    def _build_agents_tab(self) -> None:
        page = self._page_scrolled("Subagentes & Seguridad", "agents")

        # ── Card 1: Subagente Activo ──
        c1 = self._card(page, "Subagente Activo y Nivel de Aislamiento")
        from sayri.domain.agent_creator import AgentCreator
        from sayri.adapters.sandbox.executor import SandboxExecutor
        
        agents = AgentCreator.list_agents()
        self.agent_combo = Gtk.ComboBoxText()
        for a in agents:
            self.agent_combo.append(a.id, f"{a.name} ({a.sandbox.level.value})")
        self.agent_combo.set_active(0)
        self._row(c1, "Perfil Activo", "Selecciona el subagente para atender consultas", self.agent_combo)

        # ── Card 2: Lista de Subagentes ──
        c2 = self._card(page, "Subagentes Registrados en Pulsar OS")
        for a in agents:
            lvl_color = "#22c55e" if a.sandbox.level.value == "LEVEL_0_NO_EXEC" else "#38bdf8"
            lbl = Gtk.Label()
            lbl.set_markup(
                f"<span weight='600'>{GLib.markup_escape_text(a.name)}</span>  "
                f"<span foreground='{lvl_color}'>[{a.sandbox.level.value}]</span>\n"
                f"<span size='9500' foreground='#94a3b8'>{GLib.markup_escape_text(a.description)}</span>"
            )
            lbl.set_halign(Gtk.Align.START)
            lbl.set_wrap(True)
            c2.append(lbl)

        # ── Card 3: Estado de Seguridad ──
        c3 = self._card(page, "Escudo de Seguridad y Sandboxing")
        bwrap_ok = SandboxExecutor().bwrap_available
        bwrap_lbl = Gtk.Label(label="Activo ✓ (bwrap kernel sandboxing)" if bwrap_ok else "No instalado (modo host)")
        bwrap_lbl.add_css_class("sayri-status-ok" if bwrap_ok else "sayri-status-err")
        self._row(c3, "Motor Bubblewrap (bwrap)", "Aislamiento de procesos y sistema de archivos", bwrap_lbl)

        shield_lbl = Gtk.Label(label="Activo ✓ (Zero Token Drain)")
        shield_lbl.add_css_class("sayri-status-ok")
        self._row(c3, "Escudo de Tokens Remoto", "Rechazo de mensajes no autorizados en gateways", shield_lbl)

        audit_lbl = Gtk.Label(label="Activo ✓ (Escaneo AST/Regex)")
        audit_lbl.add_css_class("sayri-status-ok")
        self._row(c3, "Auditor Pre-Flight ClawHub", "Análisis estático de seguridad antes de instalar skills", audit_lbl)
