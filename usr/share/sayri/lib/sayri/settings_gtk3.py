"""Pure GTK3 Settings Window for Sayri.

Designed with high-contrast macOS/GNOME dark styling, sidebar navigation,
full language selection, and one-click Whisper/Piper downloads.
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
from typing import Callable, Optional

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import Gdk, GLib, Gtk  # noqa: E402

Gtk.init(sys.argv)

from sayri import config, downloads, llm, paths  # noqa: E402

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

GTK3_SETTINGS_CSS = b"""
window.sayri-settings-gtk3,
.sayri-settings-gtk3 {
    background-color: #1a1a22;
    color: #f1f5f9;
}

headerbar.sayri-header {
    background-color: #13131a;
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    color: #f8fafc;
    padding: 4px 12px;
}

.sayri-sidebar {
    background-color: #13131a;
    border-right: 1px solid rgba(255, 255, 255, 0.08);
    padding: 8px;
    min-width: 195px;
}

.sayri-sidebar list > row {
    border-radius: 8px;
    padding: 10px 14px;
    color: #94a3b8;
    font-size: 13px;
    font-weight: 500;
    margin-bottom: 3px;
}

.sayri-sidebar list > row:selected {
    background-color: #0071e3;
    color: #ffffff;
}

.sayri-scrolled {
    background-color: #1a1a22;
}

.sayri-group-card {
    background-color: rgba(255, 255, 255, 0.045);
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

.sayri-settings-gtk3 button,
.sayri-settings-gtk3 combobox,
.sayri-settings-gtk3 combobox > box.linked > button,
.sayri-settings-gtk3 combobox button {
    background-color: #2c2d3a;
    background-image: none;
    color: #ffffff;
    border: 1px solid rgba(255, 255, 255, 0.16);
    border-radius: 8px;
    padding: 6px 14px;
    font-size: 13px;
    font-weight: 500;
    box-shadow: none;
}

.sayri-settings-gtk3 combobox cellview {
    color: #ffffff;
}

.sayri-settings-gtk3 button:hover,
.sayri-settings-gtk3 combobox button:hover {
    background-color: #3a3b4c;
    background-image: none;
    border-color: rgba(255, 255, 255, 0.30);
    color: #ffffff;
}

.sayri-settings-gtk3 button.sayri-btn-primary {
    background-color: #0071e3;
    background-image: none;
    color: #ffffff;
    font-weight: 600;
    font-size: 13px;
    border: 1px solid #0071e3;
    border-radius: 8px;
    padding: 7px 18px;
}

.sayri-settings-gtk3 button.sayri-btn-primary:hover {
    background-color: #0077ed;
    background-image: none;
}

.sayri-settings-gtk3 button:disabled {
    background-color: rgba(255, 255, 255, 0.05);
    color: #64748b;
    border-color: rgba(255, 255, 255, 0.06);
}

.sayri-settings-gtk3 progressbar trough {
    background-color: rgba(0, 0, 0, 0.45);
    border-radius: 6px;
    min-height: 8px;
}

.sayri-settings-gtk3 progressbar progress {
    background-color: #0071e3;
    border-radius: 6px;
    min-height: 8px;
}
"""


class SettingsWindowGTK3:
    """Pure GTK3 Settings Window."""

    def __init__(self) -> None:
        self.cfg = config.config

        self.win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.win.set_title("Sayri Settings")
        self.win.set_default_size(760, 600)
        self.win.get_style_context().add_class("sayri-settings-gtk3")

        self._load_css()

        # HeaderBar
        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.get_style_context().add_class("sayri-header")
        header.set_title("Settings — Sayri")
        self.win.set_titlebar(header)

        # Main Layout: Sidebar + Stack
        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.win.add(root)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_hexpand(True)
        self.stack.set_vexpand(True)

        sidebar = Gtk.StackSidebar()
        sidebar.set_stack(self.stack)
        sidebar.get_style_context().add_class("sayri-sidebar")
        root.pack_start(sidebar, False, False, 0)
        root.pack_start(self.stack, True, True, 0)

        self._build_provider_tab()
        self._build_stt_tab()
        self._build_tts_tab()
        self._build_general_tab()

        self.win.connect("destroy", Gtk.main_quit)

    def _load_css(self) -> None:
        try:
            css = Gtk.CssProvider()
            css.load_from_data(GTK3_SETTINGS_CSS)
            screen = Gdk.Screen.get_default()
            if screen is not None:
                Gtk.StyleContext.add_provider_for_screen(
                    screen, css,
                    Gtk.STYLE_PROVIDER_PRIORITY_USER
                )
        except Exception:  # noqa: BLE001
            pass

    def show(self) -> None:
        self.win.show_all()

    def _page_scrolled(self, title: str, name: str) -> Gtk.Box:
        sw = Gtk.ScrolledWindow()
        sw.get_style_context().add_class("sayri-scrolled")
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_hexpand(True)
        sw.set_vexpand(True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.set_margin_top(20)
        box.set_margin_bottom(20)
        box.set_margin_start(24)
        box.set_margin_end(24)
        box.set_hexpand(True)

        sw.add(box)
        self.stack.add_titled(sw, name, title)
        return box

    def _card(self, parent: Gtk.Box, title: str) -> Gtk.Box:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card.get_style_context().add_class("sayri-group-card")
        if title:
            lbl = Gtk.Label(label=title)
            lbl.get_style_context().add_class("sayri-card-title")
            lbl.set_halign(Gtk.Align.START)
            card.pack_start(lbl, False, False, 0)
        parent.pack_start(card, False, False, 0)
        return card

    def _row(self, card: Gtk.Box, label: str, subtitle: str, widget: Gtk.Widget) -> None:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_hexpand(True)
        row.set_valign(Gtk.Align.CENTER)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        vbox.set_hexpand(True)
        vbox.set_halign(Gtk.Align.START)

        t = Gtk.Label(label=label)
        t.get_style_context().add_class("sayri-row-title")
        t.set_halign(Gtk.Align.START)
        vbox.pack_start(t, False, False, 0)

        if subtitle:
            s = Gtk.Label(label=subtitle)
            s.get_style_context().add_class("sayri-row-subtitle")
            s.set_halign(Gtk.Align.START)
            vbox.pack_start(s, False, False, 0)

        row.pack_start(vbox, True, True, 0)
        widget.set_halign(Gtk.Align.END)
        row.pack_end(widget, False, False, 0)
        card.pack_start(row, False, False, 0)

    def _entry_row(self, card: Gtk.Box, label: str, subtitle: str, group: str, key: str, password: bool = False) -> Gtk.Entry:
        entry = Gtk.Entry()
        entry.get_style_context().add_class("sayri-input")
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
        combo.set_wrap_width(1)
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
        self.base_url_entry = self._entry_row(card, "Base URL", "Server API endpoint (OpenAI, Ollama, vLLM, Groq)", "provider", "base_url")
        self.api_key_entry = self._entry_row(card, "API Key", "Bearer authorization token", "provider", "api_key", password=True)
        self.model_entry = self._entry_row(card, "Model Name", "Target model identifier (e.g. gpt-4o, llama3)", "provider", "model")

        card_p = self._card(page, "Generation Parameters")
        self._spin_row(card_p, "Temperature", "Sampling temperature (creativity)", "provider", "temperature", 0.0, 2.0, 0.1, 1)
        self._spin_row(card_p, "Max Tokens", "Maximum response token length", "provider", "max_tokens", 0, 16384, 64)
        self._entry_row(card_p, "Strip / Filter Patterns", "Comma-separated tags or phrases to remove (e.g. <think>.*?</think>)", "provider", "strip_patterns")
        self._switch_row(card_p, "Real-time Streaming", "Stream response tokens immediately", "provider", "stream")

        card_t = self._card(page, "Diagnostics")
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        test_btn = Gtk.Button(label="Test Connection")
        test_btn.get_style_context().add_class("sayri-btn-primary")
        res_lbl = Gtk.Label(label="")
        res_lbl.get_style_context().add_class("sayri-row-subtitle")
        res_lbl.set_halign(Gtk.Align.START)
        hbox.pack_start(test_btn, False, False, 0)
        hbox.pack_start(res_lbl, False, False, 0)
        card_t.pack_start(hbox, False, False, 0)

        def on_test(_b):
            test_btn.set_sensitive(False)
            res_lbl.set_label("Checking connection…")
            b_url = self.base_url_entry.get_text().strip()
            a_key = self.api_key_entry.get_text().strip()
            m_name = self.model_entry.get_text().strip()

            self.cfg.set("provider", "base_url", b_url)
            self.cfg.set("provider", "api_key", a_key)
            self.cfg.set("provider", "model", m_name)

            def worker():
                try:
                    llm.stream_chat(
                        b_url,
                        a_key,
                        m_name,
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
        self.whisper_status_lbl.get_style_context().add_class("sayri-row-subtitle")

        h.pack_start(self.d_whisper_btn, False, False, 0)
        h.pack_start(self.whisper_pbar, True, True, 0)
        h.pack_start(self.whisper_status_lbl, False, False, 0)
        card_m.pack_start(h, False, False, 0)

        # Install whisper-cli binary if missing
        h_bin = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.d_whisper_bin_btn = Gtk.Button(label="Install whisper-cli Binary")
        self.whisper_bin_status = Gtk.Label(label="")
        self.whisper_bin_status.get_style_context().add_class("sayri-row-subtitle")
        h_bin.pack_start(self.d_whisper_bin_btn, False, False, 0)
        h_bin.pack_start(self.whisper_bin_status, False, False, 0)
        card_m.pack_start(h_bin, False, False, 0)

        def dl_whisper(_b):
            self.d_whisper_btn.set_sensitive(False)
            ms = self.cfg.get_string("stt", "model_size")
            lang = self.cfg.get_string("stt", "language")
            self.whisper_status_lbl.set_label("Downloading…")

            def worker():
                try:
                    downloads.download_whisper_model(ms, lang, lambda f: GLib.idle_add(self.whisper_pbar.set_fraction, f))
                    GLib.idle_add(self.whisper_status_lbl.set_label, "Installed ✓")
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
                except Exception as exc:
                    GLib.idle_add(self.whisper_bin_status.set_label, f"Failed: {exc}")
                finally:
                    GLib.idle_add(self.d_whisper_bin_btn.set_sensitive, True)

            threading.Thread(target=worker, daemon=True).start()

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
        self.kws_status_lbl.get_style_context().add_class("sayri-row-subtitle")
        h_kws.pack_start(self.d_kws_btn, False, False, 0)
        h_kws.pack_start(self.kws_pbar, True, True, 0)
        h_kws.pack_start(self.kws_status_lbl, False, False, 0)
        card_kws.pack_start(h_kws, False, False, 0)

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
        self.voice_status_lbl.get_style_context().add_class("sayri-row-subtitle")

        h_voice.pack_start(self.d_voice_btn, False, False, 0)
        h_voice.pack_start(self.voice_pbar, True, True, 0)
        h_voice.pack_start(self.voice_status_lbl, False, False, 0)
        card_p.pack_start(h_voice, False, False, 0)

        # Download Piper binary row
        h_piper = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.d_piper_bin_btn = Gtk.Button(label="Install Piper TTS Binary")
        self.piper_bin_status = Gtk.Label(label="")
        self.piper_bin_status.get_style_context().add_class("sayri-row-subtitle")

        h_piper.pack_start(self.d_piper_bin_btn, False, False, 0)
        h_piper.pack_start(self.piper_bin_status, False, False, 0)
        card_p.pack_start(h_piper, False, False, 0)

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
            for k in downloads.PIPER_VOICES:
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


def main() -> None:
    app = SettingsWindowGTK3()
    app.show()
    Gtk.main()


if __name__ == "__main__":
    main()
