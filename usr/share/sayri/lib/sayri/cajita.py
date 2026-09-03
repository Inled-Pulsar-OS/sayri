"""Apple-Intelligence style Cajita widget for Sayri (GTK4).

Features:
- Top Input Pill with native Siri/Sayri tray PNG icon, transcription entry, history toggle, and Mic toggle.
- Dynamic Localized Outward Wave & Shadow Glow (#7c166e, #1e74fb, #0b1533):
  * Outward wave pulses radiate specifically from localized crest sections.
  * Internal padding prevents clipping against container edges and adjacent WebKit Orb.
- Bottom Acrylic Card with multi-view Stack:
  1. Live Chat Response Mode (Full Pango Markdown rendering with headers, bold, code, lists, larger typography, centered, no top clipping)
  2. Conversation History Manager (Toggleable, rename, delete, switch)
  3. Historical Thread Transcript Inspector
  4. Subagents Manager (with Sandboxing, Gateway selection, Store instructions, and Delete)
  5. Channel Gateways & Plugins Manager (Discord, Telegram, MCP with Store & Voice instructions)
  6. Zero-Plaintext Secrets Vault (Token Shield with prompt copy & clear explanation)
  7. In-Card Preferences & Live Progress Downloader (Whisper STT models/binaries with % bar, Piper TTS voices/binaries with % bar)
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Optional

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango  # noqa: E402

from sayri import downloads, paths
from sayri.domain.agent_creator import AgentCreator
from sayri.domain.models import (
    AgentModelConfig,
    AgentProfile,
    SandboxConfig,
    SandboxLevel,
)
from sayri.domain.secrets_manager import secrets_manager

CAJITA_CSS = b"""
.sayri-cajita-container,
.sayri-pill-container,
.sayri-pill-row,
.sayri-pill-row > * {
    background: none;
    background-color: transparent;
    background-image: none;
}

entry.sayri-pill-entry,
entry.sayri-pill-entry:focus,
entry.sayri-pill-entry:hover,
entry.sayri-pill-entry:backdrop,
entry.sayri-pill-entry text,
entry.sayri-pill-entry text:focus,
entry.sayri-pill-entry text:hover,
entry.sayri-pill-entry text:backdrop,
entry.sayri-pill-entry > text,
entry.sayri-pill-entry > text:focus,
entry.sayri-pill-entry > text > placeholder {
    background: none;
    background-color: transparent;
    background-image: none;
    border: none;
    border-radius: 0;
    box-shadow: none;
    outline: none;
    color: #ffffff;
    font-size: 15px;
    font-weight: 500;
    padding: 0 4px;
    min-height: 36px;
}

entry selection,
entry text selection,
entry.sayri-pill-entry text selection,
entry.sayri-pill-entry selection {
    background-color: rgba(30, 116, 251, 0.45);
    color: #ffffff;
}

button.sayri-icon-btn,
button.sayri-icon-btn:backdrop,
button.sayri-icon-btn * {
    background: none;
    background-color: transparent;
    background-image: none;
    border: none;
    border-radius: 8px;
    box-shadow: none;
    outline: none;
    color: #94a3b8;
    min-width: 28px;
    min-height: 28px;
    padding: 0 4px;
    transition: all 120ms ease;
}

button.sayri-icon-btn:hover {
    background-color: rgba(255, 255, 255, 0.12);
    color: #ffffff;
}

.sayri-tab-btn {
    background-color: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 9999px;
    padding: 2px 9px;
    color: #94a3b8;
    font-size: 11px;
    font-weight: 600;
    transition: all 100ms ease;
}

.sayri-tab-btn:hover {
    background-color: rgba(255, 255, 255, 0.12);
    color: #ffffff;
    border-color: rgba(255, 255, 255, 0.25);
}

.sayri-tab-btn.active {
    background-color: rgba(30, 116, 251, 0.25);
    border-color: rgba(30, 116, 251, 0.65);
    color: #ffffff;
}

.sayri-action-btn {
    background-color: rgba(255, 255, 255, 0.06);
    border: 1px solid rgba(255, 255, 255, 0.14);
    border-radius: 6px;
    padding: 3px 8px;
    color: #e2e8f0;
    font-size: 11px;
    font-weight: 600;
}

.sayri-action-btn:hover {
    background-color: rgba(255, 255, 255, 0.16);
    color: #ffffff;
}

.sayri-action-btn.primary {
    background-color: rgba(30, 116, 251, 0.35);
    border-color: rgba(30, 116, 251, 0.70);
    color: #ffffff;
}

.sayri-card-item {
    background: rgba(255, 255, 255, 0.03);
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-radius: 10px;
    padding: 7px 11px;
    transition: all 100ms ease;
}

.sayri-card-item:hover {
    background: rgba(255, 255, 255, 0.06);
    border-color: rgba(255, 255, 255, 0.16);
}

.sayri-card-item-active {
    border-color: rgba(30, 116, 251, 0.55);
    background: rgba(30, 116, 251, 0.10);
}

.sayri-response-label {
    color: #f8fafc;
    font-size: 15.5px;
    line-height: 1.65;
    font-weight: 450;
    margin-top: 6px;
    margin-bottom: 6px;
}

.sayri-cmd-expander {
    color: #38bdf8;
    font-size: 12px;
    font-weight: 600;
    margin-top: 4px;
}

.sayri-terminal-label {
    font-family: monospace, monospace;
    font-size: 12px;
    line-height: 1.4;
    padding: 8px 10px;
    background: rgba(11, 21, 51, 0.65);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 8px;
    color: #38bdf8;
}

.sayri-settings-entry {
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 6px;
    padding: 4px 8px;
    color: #ffffff;
    font-size: 12px;
}

.sayri-settings-entry:focus {
    border-color: rgba(30, 116, 251, 0.6);
    background: rgba(255, 255, 255, 0.08);
}

.sayri-info-banner {
    background: rgba(30, 116, 251, 0.08);
    border: 1px solid rgba(30, 116, 251, 0.25);
    border-radius: 8px;
    padding: 7px 10px;
    color: #93c5fd;
    font-size: 11.5px;
    line-height: 1.4;
}

progressbar.sayri-progress {
    min-height: 4px;
    border-radius: 9999px;
    background-color: rgba(255, 255, 255, 0.08);
}

progressbar.sayri-progress > trough > progress {
    background: linear-gradient(90deg, #7c166e, #1e74fb);
    border-radius: 9999px;
}
"""

SVG_MIC = """<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#94a3b8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
<rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2"/><line x1="12" y1="19" x2="12" y2="22"/><line x1="8" y1="22" x2="16" y2="22"/>
</svg>"""

SVG_MIC_ACTIVE = """<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#ec4899" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
<rect x="9" y="2" width="6" height="12" rx="3" fill="#ec4899" fill-opacity="0.3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2"/><line x1="12" y1="19" x2="12" y2="22"/><line x1="8" y1="22" x2="16" y2="22"/>
</svg>"""

SVG_HISTORY = """<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#94a3b8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l4 2"/>
</svg>"""

SVG_PLUS = """<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
</svg>"""

SVG_EDIT = """<svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#94a3b8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
<path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/>
</svg>"""

SVG_TRASH = """<svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#94a3b8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
</svg>"""

SVG_SETTINGS = """<svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#94a3b8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/>
</svg>"""

SVG_COPY = """<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#94a3b8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
<rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
</svg>"""

SVG_BACK = """<svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#94a3b8" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
<line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/>
</svg>"""


def _svg_icon(svg_str: str) -> Gtk.Image:
    try:
        loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
        loader.write(svg_str.encode("utf-8"))
        loader.close()
        pixbuf = loader.get_pixbuf()
        if pixbuf:
            tex = Gdk.Texture.new_for_pixbuf(pixbuf)
            return Gtk.Image.new_from_paintable(tex)
    except Exception:
        pass
    img = Gtk.Image.new_from_icon_name("image-missing")
    img.set_pixel_size(16)
    return img


def get_sayri_logo_widget(size: int = 24) -> Gtk.Widget:
    """Loads the official appindicator Siri/Sayri tray PNG icon."""
    icon_paths = [
        "/usr/share/icons/hicolor/256x256/apps/sayri-tray.png",
        os.path.expanduser("~/Documentos/pulsar/PKG/sayri/usr/share/icons/hicolor/256x256/apps/sayri-tray.png"),
        "/usr/share/icons/hicolor/scalable/apps/sayri-tray.png",
        "/usr/share/icons/hicolor/256x256/apps/sayri.png",
    ]
    for p in icon_paths:
        if os.path.isfile(p):
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(p, size, size, True)
                if pixbuf:
                    tex = Gdk.Texture.new_for_pixbuf(pixbuf)
                    pic = Gtk.Picture.new_for_paintable(tex)
                    pic.set_size_request(size, size)
                    pic.set_valign(Gtk.Align.CENTER)
                    pic.set_halign(Gtk.Align.CENTER)
                    return pic
            except Exception:
                pass
    img = Gtk.Image.new_from_icon_name("sayri-tray")
    img.set_pixel_size(size)
    return img


def markdown_to_pango(text: str) -> str:
    """Converts markdown text to valid Pango markup format."""
    # 1. Protect code blocks
    code_blocks = []
    def _cb_repl(m):
        code_blocks.append(m.group(2))
        return f"__CODE_BLOCK_{len(code_blocks)-1}__"
    text = re.sub(r"```([a-zA-Z0-9_\-]*)\n?(.*?)```", _cb_repl, text, flags=re.DOTALL)

    # 2. Protect inline code
    inline_codes = []
    def _ic_repl(m):
        inline_codes.append(m.group(1))
        return f"__INLINE_CODE_{len(inline_codes)-1}__"
    text = re.sub(r"`([^`]+)`", _ic_repl, text)

    # 3. Escape XML/HTML special characters
    text = GLib.markup_escape_text(text)

    # 4. Headers
    text = re.sub(r"(?m)^###\s+(.*?)$", r"<b><span foreground='#38bdf8' size='11000'>\1</span></b>", text)
    text = re.sub(r"(?m)^##\s+(.*?)$", r"<b><span foreground='#c084fc' size='12000'>\1</span></b>", text)
    text = re.sub(r"(?m)^#\s+(.*?)$", r"<b><span foreground='#ffffff' size='13500'>\1</span></b>", text)

    # 5. Bold & Italics
    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"<b><i>\1</i></b>", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)

    # 6. Bullet lists
    text = re.sub(r"(?m)^[\*\-]\s+(.*?)$", r"  • \1", text)

    # 7. Restore inline codes
    for i, code in enumerate(inline_codes):
        esc_c = GLib.markup_escape_text(code)
        text = text.replace(f"__INLINE_CODE_{i}__", f"<span font_family='monospace' foreground='#38bdf8'> {esc_c} </span>")

    # 8. Restore code blocks
    for i, code in enumerate(code_blocks):
        esc_c = GLib.markup_escape_text(code.strip())
        text = text.replace(f"__CODE_BLOCK_{i}__", f"\n<span font_family='monospace' foreground='#93c5fd'>\n{esc_c}\n</span>\n")

    return text


def _safe_set_markup(label: Gtk.Label, text: str) -> None:
    if not text:
        label.set_attributes(Pango.AttrList())
        label.set_text("")
        return
    try:
        markup = markdown_to_pango(text)
        label.set_markup(markup)
        return
    except Exception:
        pass
    label.set_attributes(Pango.AttrList())
    label.set_text(text)


class ChromaBackground(Gtk.DrawingArea):
    """Frosted Glass background with localized outward wave glow using palette #7c166e, #1e74fb, #0b1533."""

    def __init__(self, is_pill: bool = True):
        super().__init__()
        self.is_pill = is_pill
        self.mode = "idle"  # "idle" | "active" | "thinking" | "speaking"
        self.angle = 0.0
        self._anim_tag = None
        self._memory_bloom_alpha = 0.0
        self.set_draw_func(self._draw)

    def set_mode(self, mode: str) -> None:
        if self.mode == mode:
            return
        self.mode = mode
        if mode in ("active", "thinking", "speaking"):
            if not self._anim_tag:
                self._anim_tag = GLib.timeout_add(16, self._on_tick)
        else:
            if self._anim_tag:
                GLib.source_remove(self._anim_tag)
                self._anim_tag = None
        self.queue_draw()

    def trigger_memory_effect(self) -> None:
        self._memory_bloom_alpha = 1.0
        self.queue_draw()
        def _fade_step():
            self._memory_bloom_alpha -= 0.05
            if self._memory_bloom_alpha <= 0.0:
                self._memory_bloom_alpha = 0.0
                self.queue_draw()
                return False
            self.queue_draw()
            return True
        GLib.timeout_add(20, _fade_step)

    def _on_tick(self) -> bool:
        if self.mode == "thinking":
            self.angle = (self.angle + 0.085) % (2 * math.pi)
        elif self.mode == "speaking":
            self.angle = (self.angle + 0.035) % (2 * math.pi)
        elif self.mode == "active":
            self.angle = (self.angle + 0.022) % (2 * math.pi)
        else:
            return False
        self.queue_draw()
        return True

    def _draw(self, area, cr: cairo.Context, w: int, h: int) -> None:
        pad = 6.0
        r = (24.0 if self.is_pill else 18.0)
        bx, by, bw, bh = pad, pad, w - 2 * pad, h - 2 * pad

        # 1. Dark Frosted Glass Background
        cr.save()
        self._rounded_rect(cr, bx, by, bw, bh, r)
        cr.clip()

        cr.set_source_rgba(0.05, 0.08, 0.14, 0.96)
        cr.paint()

        # Memory bloom effect
        if self._memory_bloom_alpha > 0.0:
            rg = cairo.RadialGradient(w / 2, h / 2, 10, w / 2, h / 2, max(w, h) / 1.2)
            rg.add_color_stop_rgba(0.0, 0.486, 0.086, 0.431, self._memory_bloom_alpha * 0.50) # #7c166e
            rg.add_color_stop_rgba(0.5, 0.118, 0.455, 0.984, self._memory_bloom_alpha * 0.35) # #1e74fb
            rg.add_color_stop_rgba(1.0, 0.043, 0.082, 0.200, 0.0)                             # #0b1533
            cr.set_source(rg)
            cr.paint()

        cr.restore()

        # 2. Outward Localized Wave & Glow
        cr.save()
        if self.mode in ("active", "thinking", "speaking"):
            wave_cos = math.cos(self.angle)
            wave_sin = math.sin(self.angle)

            # Palette: #7c166e (Magenta), #1e74fb (Electric Blue), #0b1533 (Midnight Navy)
            lg = cairo.LinearGradient(
                w / 2 + wave_cos * (w / 1.5),
                h / 2 + wave_sin * (h / 1.5),
                w / 2 - wave_cos * (w / 1.5),
                h / 2 - wave_sin * (h / 1.5),
            )
            lg.add_color_stop_rgba(0.00, 0.486, 0.086, 0.431, 0.98) # #7c166e
            lg.add_color_stop_rgba(0.40, 0.118, 0.455, 0.984, 0.98) # #1e74fb
            lg.add_color_stop_rgba(0.75, 0.486, 0.086, 0.431, 0.98) # #7c166e
            lg.add_color_stop_rgba(1.00, 0.043, 0.082, 0.200, 0.98) # #0b1533

            # Asymmetric wave crest radiating outward at specific angles
            wave_factor = 0.5 + 0.5 * math.sin(self.angle * 2.0)
            if self.mode == "thinking":
                outward_bloom = 8.0 * wave_factor
                core_width = 2.8 + 1.2 * wave_factor
            elif self.mode == "speaking":
                outward_bloom = 6.0 * wave_factor
                core_width = 2.2 + 0.8 * wave_factor
            else:
                outward_bloom = 4.5 * wave_factor
                core_width = 1.8 + 0.5 * wave_factor

            # Outward localized wave bloom layer (only from active crest sections)
            if outward_bloom > 1.5:
                bloom_lg = cairo.LinearGradient(
                    w / 2 + wave_cos * (w / 1.5),
                    h / 2 + wave_sin * (h / 1.5),
                    w / 2 - wave_cos * (w / 1.5),
                    h / 2 - wave_sin * (h / 1.5),
                )
                bloom_lg.add_color_stop_rgba(0.00, 0.486, 0.086, 0.431, 0.45 * wave_factor)
                bloom_lg.add_color_stop_rgba(0.40, 0.118, 0.455, 0.984, 0.55 * wave_factor)
                bloom_lg.add_color_stop_rgba(1.00, 0.043, 0.082, 0.200, 0.10)

                cr.set_source(bloom_lg)
                cr.set_line_width(outward_bloom)
                self._rounded_rect(cr, bx, by, bw, bh, r)
                cr.stroke()

            # Crisp Core border
            cr.set_source(lg)
            cr.set_line_width(core_width)
            self._rounded_rect(cr, bx, by, bw, bh, r)
            cr.stroke()
        else:
            # Idle subtle border
            cr.set_source_rgba(0.118, 0.455, 0.984, 0.22)
            cr.set_line_width(1.0)
            self._rounded_rect(cr, bx, by, bw, bh, r)
            cr.stroke()

        cr.restore()

    def _rounded_rect(self, cr: cairo.Context, x: float, y: float, w: float, h: float, r: float) -> None:
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()


class SayriCajita(Gtk.Box):
    """Main Siri / Sayri interface widget."""

    def __init__(self, app: Any):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.app = app
        self._live_text = ""
        self._mic_is_active = False

        self.add_css_class("sayri-cajita-container")
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.START)

        self._load_css()
        self._build_ui()

    def _load_css(self) -> None:
        try:
            provider = Gtk.CssProvider()
            provider.load_from_data(CAJITA_CSS)
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), provider,
                Gtk.STYLE_PROVIDER_PRIORITY_USER,
            )
        except Exception:
            pass

    def _build_ui(self) -> None:
        # ── 1. Top Input Pill ─────────────────────────────────────────
        self.pill_overlay = Gtk.Overlay()
        self.pill_overlay.set_size_request(420, 52)

        self.pill_bg = ChromaBackground(is_pill=True)
        self.pill_bg.set_can_target(False)
        self.pill_overlay.set_child(self.pill_bg)

        pill_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        pill_row.set_margin_start(12)
        pill_row.set_margin_end(12)
        pill_row.set_margin_top(8)
        pill_row.set_margin_bottom(8)
        pill_row.set_valign(Gtk.Align.CENTER)

        # Official Sayri AppIndicator Tray PNG Logo (left)
        self.siri_btn = Gtk.Button()
        self.siri_btn.set_child(get_sayri_logo_widget(24))
        self.siri_btn.set_has_frame(False)
        self.siri_btn.add_css_class("sayri-icon-btn")
        self.siri_btn.set_tooltip_text("Toggle Preferences & Navigation")
        self.siri_btn.connect("clicked", lambda _b: self.toggle_navigation())
        pill_row.append(self.siri_btn)

        # Text Entry
        self.entry = Gtk.Entry()
        self.entry.set_has_frame(False)
        self.entry.add_css_class("flat")
        self.entry.add_css_class("sayri-pill-entry")
        self.entry.set_placeholder_text("Ask Sayri anything…")
        self.entry.set_hexpand(True)
        self.entry.connect("activate", self._on_entry_activate)
        self.entry.connect("changed", self._on_entry_changed)
        self.entry.connect("notify::is-focus", self._on_entry_focus)
        pill_row.append(self.entry)

        # History Button (Toggleable: opens history or closes card if open)
        self.history_btn = Gtk.Button()
        self.history_btn.set_child(_svg_icon(SVG_HISTORY))
        self.history_btn.set_has_frame(False)
        self.history_btn.add_css_class("sayri-icon-btn")
        self.history_btn.set_tooltip_text("History")
        self.history_btn.connect("clicked", lambda _b: self.toggle_history())
        pill_row.append(self.history_btn)

        # Mic Button
        self.mic_btn = Gtk.Button()
        self.mic_btn.set_child(_svg_icon(SVG_MIC))
        self.mic_btn.set_has_frame(False)
        self.mic_btn.add_css_class("sayri-icon-btn")
        self.mic_btn.set_tooltip_text("Microphone")
        self.mic_btn.connect("clicked", lambda _b: self.app.toggle_listening())
        pill_row.append(self.mic_btn)

        # ESC key handler
        key_ctrl = Gtk.EventControllerKey.new()
        def _on_key_pressed(_ctrl, keyval, _keycode, _state):
            if keyval == Gdk.KEY_Escape:
                if self.app.overlay:
                    self.app.overlay.hide()
                return True
            return False
        key_ctrl.connect("key-pressed", _on_key_pressed)
        self.entry.add_controller(key_ctrl)

        self.pill_overlay.add_overlay(pill_row)
        self.pill_overlay.set_measure_overlay(pill_row, True)
        self.append(self.pill_overlay)

        # ── 2. Bottom Acrylic Card & Multi-View Stack ────────────────
        self.card_overlay = Gtk.Overlay()
        self.card_overlay.set_size_request(420, -1)
        self.card_overlay.set_hexpand(True)

        self.card_bg = ChromaBackground(is_pill=False)
        self.card_bg.set_can_target(False)
        self.card_bg.set_hexpand(True)
        self.card_bg.set_vexpand(True)
        self.card_overlay.set_child(self.card_bg)

        card_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card_content.set_margin_start(16)
        card_content.set_margin_end(16)
        card_content.set_margin_top(12)
        card_content.set_margin_bottom(12)

        # Tab Navigation Bar
        self.tab_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.tab_bar.set_halign(Gtk.Align.CENTER)
        self.tab_bar.set_margin_bottom(2)

        self._tab_btns = {}
        for tab_id, label in [
            ("chat", "Chat"),
            ("history", "History"),
            ("agents", "Agents"),
            ("skills", "Skills"),
            ("plugins", "Plugins"),
            ("gateways", "Gateways"),
            ("secrets", "Vault"),
            ("settings", "Settings"),
        ]:
            btn = Gtk.Button(label=label)
            btn.add_css_class("sayri-tab-btn")
            btn.connect("clicked", lambda _b, tid=tab_id: self.switch_tab(tid))
            self.tab_bar.append(btn)
            self._tab_btns[tab_id] = btn

        card_content.append(self.tab_bar)

        # Card Stack with smooth crossfade
        self.card_stack = Gtk.Stack()
        self.card_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.card_stack.set_transition_duration(120)

        # ── View 1: Live Chat (Markdown Enabled, Larger Typography, No Clipping) ──
        self.chat_view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        self.badge_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.badge_box.set_valign(Gtk.Align.CENTER)
        self.badge_box.set_margin_top(2)
        self.badge_box.set_margin_bottom(4)

        self.agent_badge = Gtk.Label()
        self.agent_badge.set_markup("<span size='9500' weight='600' foreground='#38bdf8'>🤖 Sayri Primary</span>")
        self.badge_box.append(self.agent_badge)

        self.sandbox_badge = Gtk.Label()
        self.sandbox_badge.set_markup("<span size='9000' foreground='#94a3b8'>• 🛡️ Host L3</span>")
        self.badge_box.append(self.sandbox_badge)

        chat_spacer = Gtk.Box()
        chat_spacer.set_hexpand(True)
        self.badge_box.append(chat_spacer)

        quick_new_btn = Gtk.Button(label="+ New Chat")
        quick_new_btn.add_css_class("sayri-action-btn")
        def _on_quick_new(_b):
            if hasattr(self.app, "new_conversation"):
                self.app.new_conversation()
        quick_new_btn.connect("clicked", _on_quick_new)
        self.badge_box.append(quick_new_btn)

        self.chat_view.append(self.badge_box)

        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_propagate_natural_height(True)
        self.scroll.set_max_content_height(340)
        self.scroll.set_margin_top(4)
        self.scroll.set_margin_bottom(4)

        self.response_label = Gtk.Label()
        self.response_label.add_css_class("sayri-response-label")
        self.response_label.set_halign(Gtk.Align.FILL)
        self.response_label.set_valign(Gtk.Align.CENTER)
        self.response_label.set_wrap(True)
        self.response_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.response_label.set_selectable(True)
        self.scroll.set_child(self.response_label)
        self.chat_view.append(self.scroll)

        self.cmd_expander = Gtk.Expander(label="Command Output")
        self.cmd_expander.add_css_class("sayri-cmd-expander")
        self.cmd_scroll = Gtk.ScrolledWindow()
        self.cmd_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.cmd_scroll.set_propagate_natural_height(True)
        self.cmd_scroll.set_max_content_height(160)
        self.cmd_label = Gtk.Label()
        self.cmd_label.add_css_class("sayri-terminal-label")
        self.cmd_label.set_halign(Gtk.Align.FILL)
        self.cmd_label.set_wrap(True)
        self.cmd_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.cmd_label.set_selectable(True)
        self.cmd_scroll.set_child(self.cmd_label)
        self.cmd_expander.set_child(self.cmd_scroll)
        self.chat_view.append(self.cmd_expander)
        self.cmd_expander.set_visible(False)

        self.card_stack.add_named(self.chat_view, "chat")

        # ── View 2: History ──
        self.history_view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        hist_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        hist_header.set_valign(Gtk.Align.CENTER)

        hist_title = Gtk.Label()
        hist_title.set_markup("<span weight='700' size='10500' foreground='#f8fafc'>CONVERSATION HISTORY</span>")
        hist_title.set_halign(Gtk.Align.START)
        hist_title.set_hexpand(True)
        hist_header.append(hist_title)

        new_btn = Gtk.Button(label="+ New Chat")
        new_btn.add_css_class("sayri-action-btn")
        new_btn.add_css_class("primary")
        def _on_new_click(_b):
            if hasattr(self.app, "new_conversation"):
                self.app.new_conversation()
            self.switch_tab("chat")
        new_btn.connect("clicked", _on_new_click)
        hist_header.append(new_btn)
        self.history_view.append(hist_header)

        self.hist_scroll = Gtk.ScrolledWindow()
        self.hist_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.hist_scroll.set_propagate_natural_height(True)
        self.hist_scroll.set_max_content_height(240)
        self.sessions_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.hist_scroll.set_child(self.sessions_box)
        self.history_view.append(self.hist_scroll)

        self.card_stack.add_named(self.history_view, "history")

        # ── View 3: AI Subagents ──
        self.agents_view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        agents_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        agents_header.set_valign(Gtk.Align.CENTER)

        ag_title = Gtk.Label()
        ag_title.set_markup("<span weight='700' size='10500' foreground='#f8fafc'>AI SUBAGENTS</span>")
        ag_title.set_halign(Gtk.Align.START)
        ag_title.set_hexpand(True)
        agents_header.append(ag_title)

        create_ag_btn = Gtk.Button(label="+ Create Subagent")
        create_ag_btn.add_css_class("sayri-action-btn")
        create_ag_btn.add_css_class("primary")
        create_ag_btn.connect("clicked", lambda _b: self._prompt_create_agent())
        agents_header.append(create_ag_btn)
        self.agents_view.append(agents_header)

        ag_banner = Gtk.Label()
        ag_banner.add_css_class("sayri-info-banner")
        ag_banner.set_markup(
            "<b>Ecosystem Note:</b> Create specialized subagents with custom sandboxes and plugin permissions, or install new skills from the <b>Pulsar Store</b>."
        )
        ag_banner.set_wrap(True)
        ag_banner.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.agents_view.append(ag_banner)

        self.agents_scroll = Gtk.ScrolledWindow()
        self.agents_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.agents_scroll.set_propagate_natural_height(True)
        self.agents_scroll.set_max_content_height(220)
        self.agents_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.agents_scroll.set_child(self.agents_box)
        self.agents_view.append(self.agents_scroll)

        self.card_stack.add_named(self.agents_view, "agents")

        # ── View 4: Skills & Tools Manager ──
        self.skills_view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        sk_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        sk_header.set_valign(Gtk.Align.CENTER)

        sk_title = Gtk.Label()
        sk_title.set_markup("<span weight='700' size='10500' foreground='#f8fafc'>SKILLS &amp; TOOLS</span>")
        sk_title.set_halign(Gtk.Align.START)
        sk_title.set_hexpand(True)
        sk_header.append(sk_title)

        sk_install_btn = Gtk.Button(label="+ Install Skill")
        sk_install_btn.add_css_class("sayri-action-btn")
        sk_install_btn.add_css_class("primary")
        sk_install_btn.connect("clicked", lambda _b: self.show_install_skill_view())
        sk_header.append(sk_install_btn)

        sk_store_btn = Gtk.Button(label="Pulsar Store ↗")
        sk_store_btn.add_css_class("sayri-action-btn")
        sk_store_btn.connect("clicked", lambda _b: os.system("xdg-open https://store-os.inled.es &"))
        sk_header.append(sk_store_btn)
        self.skills_view.append(sk_header)

        sk_banner = Gtk.Label()
        sk_banner.add_css_class("sayri-info-banner")
        sk_banner.set_markup(
            "<b>Skills:</b> Extend Sayri with domain capabilities (research, code analysis, docs) in isolated <b>Bubblewrap</b> sandboxes."
        )
        sk_banner.set_wrap(True)
        sk_banner.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.skills_view.append(sk_banner)

        self.skills_scroll = Gtk.ScrolledWindow()
        self.skills_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.skills_scroll.set_propagate_natural_height(True)
        self.skills_scroll.set_max_content_height(220)
        self.skills_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.skills_scroll.set_child(self.skills_box)
        self.skills_view.append(self.skills_scroll)

        self.card_stack.add_named(self.skills_view, "skills")

        # ── View 5: Plugins & Tool Extensions (Dedicated) ──
        self.plugins_view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        pl_hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        pl_hdr.set_valign(Gtk.Align.CENTER)

        pl_t = Gtk.Label()
        pl_t.set_markup("<span weight='700' size='10500' foreground='#f8fafc'>PLUGINS &amp; EXTENSIONS</span>")
        pl_t.set_halign(Gtk.Align.START)
        pl_t.set_hexpand(True)
        pl_hdr.append(pl_t)

        pl_store_btn = Gtk.Button(label="Pulsar Store ↗")
        pl_store_btn.add_css_class("sayri-action-btn")
        pl_store_btn.connect("clicked", lambda _b: os.system("xdg-open https://store-os.inled.es &"))
        pl_hdr.append(pl_store_btn)
        self.plugins_view.append(pl_hdr)

        pl_banner = Gtk.Label()
        pl_banner.add_css_class("sayri-info-banner")
        pl_banner.set_markup(
            "<b>Plugins Security:</b> Plugins expose external tools and APIs. Sayri enforces strict sandbox levels (Level 0 / Level 1) to prevent any sandbox escapes or unauthorized host access."
        )
        pl_banner.set_wrap(True)
        pl_banner.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.plugins_view.append(pl_banner)

        self.plugins_scroll = Gtk.ScrolledWindow()
        self.plugins_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.plugins_scroll.set_propagate_natural_height(True)
        self.plugins_scroll.set_max_content_height(220)
        self.plugins_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.plugins_scroll.set_child(self.plugins_box)
        self.plugins_view.append(self.plugins_scroll)

        self.card_stack.add_named(self.plugins_view, "plugins")

        # ── View 6: Channels & Gateways (Dedicated) ──
        self.gateways_view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        gw_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        gw_header.set_valign(Gtk.Align.CENTER)

        gw_title = Gtk.Label()
        gw_title.set_markup("<span weight='700' size='10500' foreground='#f8fafc'>CHANNELS &amp; GATEWAYS</span>")
        gw_title.set_halign(Gtk.Align.START)
        gw_title.set_hexpand(True)
        gw_header.append(gw_title)

        add_gw_btn = Gtk.Button(label="+ Add Gateway")
        add_gw_btn.add_css_class("sayri-action-btn")
        add_gw_btn.add_css_class("primary")
        add_gw_btn.connect("clicked", lambda _b: self.show_create_gateway_view())
        gw_header.append(add_gw_btn)
        self.gateways_view.append(gw_header)

        gw_banner = Gtk.Label()
        gw_banner.add_css_class("sayri-info-banner")
        gw_banner.set_markup(
            "<b>Channel Gateways:</b> Manage remote bot instances (Telegram, Discord). Each gateway supports continuous conversations, stand-by timeouts, and OTP desktop pairing."
        )
        gw_banner.set_wrap(True)
        gw_banner.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.gateways_view.append(gw_banner)

        self.gateways_scroll = Gtk.ScrolledWindow()
        self.gateways_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.gateways_scroll.set_propagate_natural_height(True)
        self.gateways_scroll.set_max_content_height(220)
        self.gateways_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.gateways_scroll.set_child(self.gateways_box)
        self.gateways_view.append(self.gateways_scroll)

        self.card_stack.add_named(self.gateways_view, "gateways")

        # ── View 7: Zero-Plaintext Secrets Vault ──
        self.secrets_view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        sec_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        sec_header.set_valign(Gtk.Align.CENTER)

        sec_title = Gtk.Label()
        sec_title.set_markup("<span weight='700' size='10500' foreground='#f8fafc'>TOKEN SHIELD &amp; VAULT</span>")
        sec_title.set_halign(Gtk.Align.START)
        sec_title.set_hexpand(True)
        sec_header.append(sec_title)

        add_sec_btn = Gtk.Button(label="+ Add Secret")
        add_sec_btn.add_css_class("sayri-action-btn")
        add_sec_btn.add_css_class("primary")
        add_sec_btn.connect("clicked", lambda _b: self.show_add_secret_view())
        sec_header.append(add_sec_btn)
        self.secrets_view.append(sec_header)

        sec_banner = Gtk.Label()
        sec_banner.add_css_class("sayri-info-banner")
        sec_banner.set_markup(
            "<b>How Token Shield Works:</b> Secrets are stored in a zero-plaintext local vault and never transmitted in LLM prompts. Reference them in prompts or skills as <tt>$SECRET:KEY_NAME</tt>. Sayri injects the real tokens directly into child process environments during execution."
        )
        sec_banner.set_wrap(True)
        sec_banner.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.secrets_view.append(sec_banner)

        self.sec_scroll = Gtk.ScrolledWindow()
        self.sec_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.sec_scroll.set_propagate_natural_height(True)
        self.sec_scroll.set_max_content_height(220)
        self.secrets_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.sec_scroll.set_child(self.secrets_box)
        self.secrets_view.append(self.sec_scroll)

        # ── View 8: Automated Routines & Cron Jobs ──
        self.routines_view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        rt_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        rt_header.set_valign(Gtk.Align.CENTER)

        rt_title = Gtk.Label()
        rt_title.set_markup("<span weight='700' size='10500' foreground='#f8fafc'>ROUTINES &amp; CRON JOBS</span>")
        rt_title.set_halign(Gtk.Align.START)
        rt_title.set_hexpand(True)
        rt_header.append(rt_title)

        add_rt_btn = Gtk.Button(label="+ Add Routine")
        add_rt_btn.add_css_class("sayri-action-btn")
        add_rt_btn.add_css_class("primary")
        add_rt_btn.connect("clicked", lambda _b: self.show_create_routine_view())
        rt_header.append(add_rt_btn)
        self.routines_view.append(rt_header)

        rt_banner = Gtk.Label()
        rt_banner.add_css_class("sayri-info-banner")
        rt_banner.set_markup(
            "<b>Automated Routines:</b> Schedule automated voice briefings, login announcements, or background tasks executed autonomously by Sayri AI."
        )
        rt_banner.set_wrap(True)
        rt_banner.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.routines_view.append(rt_banner)

        self.routines_scroll = Gtk.ScrolledWindow()
        self.routines_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.routines_scroll.set_propagate_natural_height(True)
        self.routines_scroll.set_max_content_height(220)
        self.routines_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.routines_scroll.set_child(self.routines_box)
        self.routines_view.append(self.routines_scroll)

        self.card_stack.add_named(self.routines_view, "routines")

        # ── View 9: Conversation Thread Viewer (History Detail) ──
        self.thread_view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        thread_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        thread_header.set_valign(Gtk.Align.CENTER)

        thread_back = Gtk.Button()
        thread_back.set_child(_svg_icon(SVG_BACK))
        thread_back.set_tooltip_text("Back to History")
        thread_back.add_css_class("sayri-icon-btn")
        thread_back.connect("clicked", lambda _b: self.switch_tab("history"))
        thread_header.append(thread_back)

        self.thread_title_lbl = Gtk.Label()
        self.thread_title_lbl.set_markup("<span weight='700' size='10500' foreground='#f8fafc'>Conversation</span>")
        self.thread_title_lbl.set_halign(Gtk.Align.START)
        self.thread_title_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self.thread_title_lbl.set_hexpand(True)
        thread_header.append(self.thread_title_lbl)

        resume_live_btn = Gtk.Button(label="Continue in Chat 💬")
        resume_live_btn.add_css_class("sayri-action-btn")
        resume_live_btn.add_css_class("primary")
        resume_live_btn.connect("clicked", lambda _b: self.switch_tab("chat"))
        thread_header.append(resume_live_btn)
        self.thread_view.append(thread_header)

        self.thread_scroll = Gtk.ScrolledWindow()
        self.thread_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.thread_scroll.set_propagate_natural_height(True)
        self.thread_scroll.set_max_content_height(260)
        self.thread_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.thread_scroll.set_child(self.thread_box)
        self.thread_view.append(self.thread_scroll)

        self.card_stack.add_named(self.thread_view, "thread")

        # ── View 10: In-Card Settings & Full Model Downloader with Live Progress ──
        self.settings_view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        set_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        set_header.set_valign(Gtk.Align.CENTER)

        set_title = Gtk.Label()
        set_title.set_markup("<span weight='700' size='10500' foreground='#f8fafc'>PREFERENCES &amp; MODELS</span>")
        set_title.set_halign(Gtk.Align.START)
        set_title.set_hexpand(True)
        set_header.append(set_title)
        self.settings_view.append(set_header)

        self.settings_scroll = Gtk.ScrolledWindow()
        self.settings_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.settings_scroll.set_propagate_natural_height(True)
        self.settings_scroll.set_max_content_height(280)
        self.settings_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.settings_scroll.set_child(self.settings_box)
        self.settings_view.append(self.settings_scroll)

        self.card_stack.add_named(self.settings_view, "settings")

        # ── View 11: Generic Dynamic In-Card Subview (Zero Popups API) ──
        self.subview_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        sub_hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        sub_hdr.set_valign(Gtk.Align.CENTER)

        self.subview_back_btn = Gtk.Button()
        self.subview_back_btn.set_child(_svg_icon(SVG_BACK))
        self.subview_back_btn.set_has_frame(False)
        self.subview_back_btn.add_css_class("sayri-icon-btn")
        self.subview_back_btn.set_tooltip_text("Back")
        sub_hdr.append(self.subview_back_btn)

        self.subview_title_lbl = Gtk.Label()
        self.subview_title_lbl.set_halign(Gtk.Align.START)
        self.subview_title_lbl.set_hexpand(True)
        sub_hdr.append(self.subview_title_lbl)
        self.subview_box.append(sub_hdr)

        self.subview_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.subview_box.append(self.subview_body)

        self.card_stack.add_named(self.subview_box, "subview")

        card_content.append(self.card_stack)
        self.card_overlay.add_overlay(card_content)
        self.card_overlay.set_measure_overlay(card_content, True)
        self.append(self.card_overlay)

        # Initially hidden
        self.card_overlay.set_visible(False)

    def switch_tab(self, tab_id: str, trigger_effect: bool = True) -> None:
        """Switch between views inside the response card."""
        is_subview = tab_id in ("subview", "thread")
        self.tab_bar.set_visible(not is_subview)
        for tid, btn in self._tab_btns.items():
            if tid == tab_id:
                btn.add_css_class("active")
            else:
                btn.remove_css_class("active")

        if tab_id == "history":
            self._populate_history()
        elif tab_id == "agents":
            self._populate_agents()
        elif tab_id == "skills":
            self._populate_skills()
        elif tab_id == "plugins":
            self._populate_plugins_tools()
        elif tab_id == "gateways":
            self._populate_gateways()
        elif tab_id == "routines":
            self._populate_routines()
        elif tab_id == "secrets":
            self._populate_secrets()
        elif tab_id == "settings":
            self._populate_settings()

        self.card_stack.set_visible_child_name(tab_id)
        if trigger_effect:
            self.card_bg.trigger_memory_effect()
        self.card_overlay.set_visible(True)

    def toggle_navigation(self) -> None:
        """Toggles response card: closes if already open on settings, otherwise opens settings."""
        if self.card_overlay.get_visible() and self.card_stack.get_visible_child_name() == "settings":
            self.card_overlay.set_visible(False)
        else:
            self.switch_tab("settings")

    def toggle_history(self) -> None:
        """Toggles response card: closes if already open on history, otherwise opens history."""
        if self.card_overlay.get_visible() and self.card_stack.get_visible_child_name() == "history":
            self.card_overlay.set_visible(False)
        else:
            self.switch_tab("history")

    def show_history_view(self) -> None:
        self.switch_tab("history")

    def show_chat_view(self) -> None:
        self.switch_tab("chat")

    def _on_entry_activate(self, entry: Gtk.Entry) -> None:
        text = entry.get_text().strip()
        if text:
            entry.set_text("")
            self.clear()
            self.switch_tab("chat", trigger_effect=False)
            self.app.send_text(text)

    def _on_entry_changed(self, entry: Gtk.Entry) -> None:
        text = entry.get_text().strip()
        if text and self.pill_bg.mode == "idle":
            self.pill_bg.set_mode("active")
        elif not text and not self.app.listening_now() and self.pill_bg.mode == "active":
            self.pill_bg.set_mode("idle")

    def _on_entry_focus(self, entry: Gtk.Entry, _pspec) -> None:
        if entry.has_focus():
            self.pill_bg.set_mode("active")
        elif not entry.get_text().strip() and not self.app.listening_now() and self.pill_bg.mode == "active":
            self.pill_bg.set_mode("idle")

    # ── History Logic ──
    def _populate_history(self) -> None:
        while True:
            child = self.sessions_box.get_first_child()
            if not child:
                break
            self.sessions_box.remove(child)

        sessions = []
        if hasattr(self.app, "storage") and self.app.storage:
            try:
                sessions = self.app.storage.list_sessions(limit=30)
            except Exception:
                sessions = []

        if not sessions:
            lbl = Gtk.Label(label="No saved conversations yet.")
            lbl.set_halign(Gtk.Align.START)
            self.sessions_box.append(lbl)
            return

        import datetime
        active_id = getattr(self.app, "active_session_id", "")
        now = datetime.datetime.now()

        for s in sessions:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            row.add_css_class("sayri-card-item")
            if s.id == active_id:
                row.add_css_class("sayri-card-item-active")

            btn = Gtk.Button()
            btn.set_has_frame(False)
            btn.add_css_class("flat")
            btn.set_hexpand(True)

            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            title = s.title if s.title else "Conversation"
            if s.id.startswith("tg-") or "telegram" in s.id.lower():
                title = f"✈️ {title}"
            elif s.id.startswith("discord-") or "discord" in s.id.lower():
                title = f"🎮 {title}"
            elif "remote" in s.id.lower():
                title = f"🌐 {title}"

            if s.id == active_id:
                title = f"[Active] {title}"

            t = Gtk.Label()
            t.set_markup(f"<span foreground='#ffffff' weight='600' size='10000'>{GLib.markup_escape_text(title)}</span>")
            t.set_halign(Gtk.Align.START)
            t.set_ellipsize(Pango.EllipsizeMode.END)

            sess_dt = datetime.datetime.fromtimestamp(s.updated_at)
            dt_str = f"Today {sess_dt.strftime('%H:%M')}" if sess_dt.date() == now.date() else sess_dt.strftime("%d/%m %H:%M")

            sub = Gtk.Label()
            sub.set_markup(f"<span foreground='#94a3b8' size='9000'>{dt_str} • {GLib.markup_escape_text(s.agent_id)}</span>")
            sub.set_halign(Gtk.Align.START)

            v.append(t)
            v.append(sub)
            btn.set_child(v)

            sid = s.id
            btn.connect("clicked", lambda _b, session_id=sid: self.app.switch_session(session_id))
            row.append(btn)

            # Edit Title
            ren = Gtk.Button()
            ren.set_child(_svg_icon(SVG_EDIT))
            ren.set_has_frame(False)
            ren.add_css_class("sayri-icon-btn")
            ren.set_tooltip_text("Rename conversation")
            ren.connect("clicked", lambda _b, session_id=sid, cur_t=s.title: self._prompt_rename_session(session_id, cur_t))
            row.append(ren)

            # Delete
            del_b = Gtk.Button()
            del_b.set_child(_svg_icon(SVG_TRASH))
            del_b.set_has_frame(False)
            del_b.add_css_class("sayri-icon-btn")
            del_b.set_tooltip_text("Delete conversation")
            def _del(_b, session_id=sid):
                if hasattr(self.app, "storage") and self.app.storage:
                    self.app.storage.delete_session(session_id)
                    self._populate_history()
            del_b.connect("clicked", _del)
            row.append(del_b)

            self.sessions_box.append(row)

    # ── Subagents Logic ──
    def _populate_agents(self) -> None:
        while True:
            child = self.agents_box.get_first_child()
            if not child:
                break
            self.agents_box.remove(child)

        agents = AgentCreator.list_agents()
        active_agent = getattr(self.app, "active_agent", None)
        active_id = active_agent.id if active_agent else "default"

        for ag in agents:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.add_css_class("sayri-card-item")
            is_active = (ag.id == active_id)
            if is_active:
                row.add_css_class("sayri-card-item-active")

            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            v.set_hexpand(True)

            t = Gtk.Label()
            prefix = "[Active] " if is_active else ""
            t.set_markup(f"<span foreground='#ffffff' weight='700' size='10500'>{prefix}{GLib.markup_escape_text(ag.name)}</span>")
            t.set_halign(Gtk.Align.START)

            sub = Gtk.Label()
            sb_text = ag.sandbox.level.value
            sub.set_markup(f"<span foreground='#94a3b8' size='9000'>{sb_text} • Model: <tt>{GLib.markup_escape_text(ag.model.model_name)}</tt></span>")
            sub.set_halign(Gtk.Align.START)

            v.append(t)
            v.append(sub)
            row.append(v)

            # Select button
            sw_btn = Gtk.Button(label="Active" if is_active else "Select")
            sw_btn.add_css_class("sayri-action-btn")
            if is_active:
                sw_btn.add_css_class("primary")
            def _switch(_b, profile=ag):
                self.app.active_agent = profile
                self.update_agent_badge(profile.name, profile.sandbox.level.value)
                self._populate_agents()
                self.switch_tab("chat")
            sw_btn.connect("clicked", _switch)
            row.append(sw_btn)

            # Delete button (custom agents only)
            if not getattr(ag, "is_builtin", False) and ag.id != "default":
                del_ag_btn = Gtk.Button()
                del_ag_btn.set_child(_svg_icon(SVG_TRASH))
                del_ag_btn.set_has_frame(False)
                del_ag_btn.add_css_class("sayri-icon-btn")
                del_ag_btn.set_tooltip_text("Delete Subagent")
                del_ag_btn.connect("clicked", lambda _b, aid=ag.id: (AgentCreator.delete_agent(aid), self._populate_agents()))
                row.append(del_ag_btn)

            self.agents_box.append(row)

    # ── Skills Logic (Dedicated View) ──
    def _populate_skills(self) -> None:
        while True:
            child = self.skills_box.get_first_child()
            if not child:
                break
            self.skills_box.remove(child)

        from sayri import skills as skills_mod
        installed = skills_mod.list_skills()
        # Filter out gateways that run as daemons (entrypoint)
        skill_items = []
        for s in installed:
            m_path = Path(s["path"]) / "manifest.json"
            if m_path.is_file():
                try:
                    m = json.loads(m_path.read_text(encoding="utf-8"))
                    if m.get("entrypoint"):
                        continue
                except Exception:
                    pass
            skill_items.append(s)

        if not skill_items:
            empty_lbl = Gtk.Label(label="No custom skills installed yet. Click (+ Install Skill) to add tools from Pulsar Store.")
            empty_lbl.set_halign(Gtk.Align.START)
            empty_lbl.set_wrap(True)
            empty_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            self.skills_box.append(empty_lbl)
            return

        for sk in skill_items:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.add_css_class("sayri-card-item")

            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            v.set_hexpand(True)

            t = Gtk.Label()
            t.set_markup(f"<span foreground='#ffffff' weight='700' size='10500'>{GLib.markup_escape_text(sk['name'])}</span>")
            t.set_halign(Gtk.Align.START)

            sub = Gtk.Label()
            sub.set_markup(f"<span foreground='#94a3b8' size='9000'>{GLib.markup_escape_text(sk['description'])} • <span foreground='#38bdf8'>SANDBOXED</span></span>")
            sub.set_halign(Gtk.Align.START)
            sub.set_wrap(True)

            v.append(t)
            v.append(sub)
            row.append(v)

            # View SKILL.md button
            view_btn = Gtk.Button(label="SKILL.md")
            view_btn.add_css_class("sayri-action-btn")
            view_btn.connect("clicked", lambda _b, name=sk["name"]: self.show_skill_details_view(name))
            row.append(view_btn)

            # Delete button (🗑️)
            del_btn = Gtk.Button()
            del_btn.set_child(_svg_icon(SVG_TRASH))
            del_btn.set_has_frame(False)
            del_btn.add_css_class("sayri-icon-btn")
            del_btn.set_tooltip_text("Delete Skill")
            del_btn.connect("clicked", lambda _b, name=sk["name"]: (skills_mod.uninstall_skill(name), self._populate_skills()))
            row.append(del_btn)

            self.skills_box.append(row)

    # ── Plugins & Tool Extensions Logic ──
    def _populate_plugins_tools(self) -> None:
        while True:
            child = self.plugins_box.get_first_child()
            if not child:
                break
            self.plugins_box.remove(child)

        from sayri.gateway_supervisor import gateway_supervisor

        all_installed = gateway_supervisor.list_installed_plugins()
        # Filter for tools/extensions (exclude pure gateways or show all with security tags)
        tool_plugins = [p for p in all_installed if p.get("plugin_type", "gateway") != "gateway" or "gateway" not in p.get("id", "")]
        if not tool_plugins:
            tool_plugins = all_installed

        if not tool_plugins:
            empty_lbl = Gtk.Label(label="No plugins installed. Visit the Pulsar Store (store-os.inled.es) to install developer tools, web search, or MCP servers.")
            empty_lbl.set_halign(Gtk.Align.START)
            empty_lbl.set_wrap(True)
            empty_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            self.plugins_box.append(empty_lbl)
            return

        for pl in tool_plugins:
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            card.add_css_class("sayri-card-item")

            header_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            header_row.set_valign(Gtk.Align.CENTER)

            t = Gtk.Label()
            t.set_markup(f"<span foreground='#ffffff' weight='700' size='10000'>{GLib.markup_escape_text(pl.get('name', pl['id']))}</span>")
            t.set_halign(Gtk.Align.START)
            t.set_hexpand(True)
            header_row.append(t)

            # Security level tag
            min_lvl = pl.get("min_sandbox_level", "LEVEL_1_READONLY")
            allow_l0 = pl.get("allow_in_level_0", False)

            sec_badge = Gtk.Label()
            if allow_l0 or "NO_EXEC" in min_lvl:
                sec_badge.set_markup("<span foreground='#22c55e' size='8500' weight='600'>🟢 Safe for L0/L1</span>")
            elif "READONLY" in min_lvl or "ISOLATED" in min_lvl:
                sec_badge.set_markup("<span foreground='#38bdf8' size='8500' weight='600'>🛡️ Requires Sandbox L1+</span>")
            else:
                sec_badge.set_markup("<span foreground='#f59e0b' size='8500' weight='600'>⚠️ Requires Host L3</span>")
            header_row.append(sec_badge)

            # Settings / Sandbox configuration button
            cfg_btn = Gtk.Button()
            cfg_btn.set_child(_svg_icon(SVG_SETTINGS))
            cfg_btn.set_has_frame(False)
            cfg_btn.add_css_class("sayri-icon-btn")
            cfg_btn.set_tooltip_text("Configure Plugin & Sandbox Policy")
            cfg_btn.connect("clicked", lambda _b, p=pl: self.show_edit_plugin_view(p))
            header_row.append(cfg_btn)

            card.append(header_row)

            desc_lbl = Gtk.Label()
            desc_text = pl.get("description", "Plugin extension for Sayri AI")
            ver = pl.get("version", "1.0.0")
            desc_lbl.set_markup(f"<span foreground='#94a3b8' size='9000'>{GLib.markup_escape_text(desc_text)} • v{ver}</span>")
            desc_lbl.set_halign(Gtk.Align.START)
            desc_lbl.set_wrap(True)
            card.append(desc_lbl)

            self.plugins_box.append(card)

    def show_edit_plugin_view(self, plugin_data: dict) -> None:
        def _builder(box: Gtk.Box):
            pid = plugin_data.get("id", "plugin")
            pname = plugin_data.get("name", pid)

            desc_lbl = Gtk.Label()
            desc_lbl.set_markup(f"<span foreground='#f8fafc' weight='700'>{GLib.markup_escape_text(pname)}</span>\n<span size='9000' foreground='#94a3b8'>{GLib.markup_escape_text(plugin_data.get('description', ''))}</span>")
            desc_lbl.set_halign(Gtk.Align.START)
            desc_lbl.set_wrap(True)
            box.append(desc_lbl)

            lbl_sb = Gtk.Label(label="Minimum Required Sandbox Level:")
            lbl_sb.set_halign(Gtk.Align.START)
            box.append(lbl_sb)

            sb_options = [
                ("LEVEL_0_NO_EXEC", "LEVEL_0: Pure Chat (Safe in Zero-Execution)"),
                ("LEVEL_1_READONLY", "LEVEL_1: Sandbox L1 (Read-Only)"),
                ("LEVEL_2_ISOLATED_DEV", "LEVEL_2: Sandbox L2 (Isolated Workspace)"),
                ("LEVEL_3_HOST_USER", "LEVEL_3: Host L3 (Active Desktop User)"),
            ]
            sb_model = Gtk.StringList.new([opt[1] for opt in sb_options])
            sb_drop = Gtk.DropDown.new(sb_model, None)

            cur_sb = plugin_data.get("min_sandbox_level", "LEVEL_1_READONLY")
            for idx, opt in enumerate(sb_options):
                if opt[0] == cur_sb:
                    sb_drop.set_selected(idx)
                    break
            box.append(sb_drop)

            l0_check = Gtk.CheckButton(label="Allow execution in Level 0 (Zero-Execution) agents")
            l0_check.set_active(plugin_data.get("allow_in_level_0", False))
            box.append(l0_check)

            save_btn = Gtk.Button(label="Save Security Configuration")
            save_btn.add_css_class("sayri-action-btn")
            save_btn.add_css_class("primary")

            def _save(_b):
                sel_sb = sb_options[sb_drop.get_selected()][0]
                plugin_data["min_sandbox_level"] = sel_sb
                plugin_data["allow_in_level_0"] = l0_check.get_active()

                for p_dir in [Path.home() / ".config" / "sayri" / "plugins" / pid, Path("/usr/share/sayri/plugins") / pid]:
                    m_path = p_dir / "manifest.json"
                    if m_path.is_file():
                        try:
                            m_json = json.loads(m_path.read_text(encoding="utf-8"))
                            m_json["min_sandbox_level"] = sel_sb
                            m_json["allow_in_level_0"] = l0_check.get_active()
                            m_path.write_text(json.dumps(m_json, indent=2), encoding="utf-8")
                        except Exception:
                            pass
                self._populate_plugins_tools()
                self.switch_tab("plugins")

            save_btn.connect("clicked", _save)
            box.append(save_btn)

        self.open_subview(f"Configure {plugin_data.get('name', 'Plugin')}", _builder, on_back_tab="plugins")

    # ── Channel Gateways Logic (Multi-Instance Channel Architecture) ──
    def _populate_gateways(self) -> None:
        while True:
            child = self.gateways_box.get_first_child()
            if not child:
                break
            self.gateways_box.remove(child)

        from sayri.gateway_supervisor import gateway_supervisor
        from sayri.domain.agent_creator import AgentCreator

        installed_plugins = {p["id"]: p for p in gateway_supervisor.list_installed_plugins()}
        instances = gateway_supervisor.list_instances()
        all_agents = {a.id: a.name for a in AgentCreator.list_agents()}

        if not instances:
            empty_lbl = Gtk.Label(label="No channel gateways configured. Click (+ Add Gateway) to bind a Telegram/Discord bot to an agent or install from the Pulsar Store.")
            empty_lbl.set_halign(Gtk.Align.START)
            empty_lbl.set_wrap(True)
            empty_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            self.gateways_box.append(empty_lbl)
            return

        for inst in instances:
            inst_id = inst["id"]
            plugin_meta = installed_plugins.get(inst.get("plugin_id", inst_id), {})
            agent_id = inst.get("agent_id", "default")
            agent_name = all_agents.get(agent_id, agent_id)
            sandbox_lvl = inst.get("sandbox_level", "LEVEL_1_READONLY")

            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            card.add_css_class("sayri-card-item")

            # Header row: Title, Agent Badge, Sandbox Badge, Settings, Trash, Switch
            header_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            header_row.set_valign(Gtk.Align.CENTER)

            t = Gtk.Label()
            t.set_markup(f"<span foreground='#ffffff' weight='700' size='10000'>{GLib.markup_escape_text(inst.get('name', inst_id))}</span>")
            t.set_halign(Gtk.Align.START)
            t.set_hexpand(True)
            header_row.append(t)

            # Agent Badge
            ag_badge = Gtk.Label()
            ag_badge.set_markup(f"<span foreground='#38bdf8' size='8500' weight='600'>🤖 {GLib.markup_escape_text(agent_name)}</span>")
            header_row.append(ag_badge)

            # Sandbox Badge
            sb_badge = Gtk.Label()
            sb_color = "#22c55e" if "READONLY" in sandbox_lvl or "NO_EXEC" in sandbox_lvl else "#f59e0b"
            sb_badge.set_markup(f"<span foreground='{sb_color}' size='8500' weight='600'>🛡️ {sandbox_lvl.replace('LEVEL_', 'L')}</span>")
            header_row.append(sb_badge)

            # Guests Access Badge
            guests_enabled = inst.get("allow_channel_guests", False)
            auth_file = Path.home() / ".config" / "sayri" / f"authorizations_{inst_id}.json"
            if auth_file.is_file():
                try:
                    auth_d = json.loads(auth_file.read_text(encoding="utf-8"))
                    if "allow_channel_guests" in auth_d:
                        guests_enabled = auth_d["allow_channel_guests"]
                except Exception:
                    pass
            g_badge = Gtk.Label()
            if guests_enabled:
                g_badge.set_markup("<span foreground='#10b981' size='8500' weight='600'>👥 Guests: ON</span>")
            else:
                g_badge.set_markup("<span foreground='#94a3b8' size='8500' weight='600'>🔒 Owner Only</span>")
            header_row.append(g_badge)

            # Edit / Configure Button
            edit_btn = Gtk.Button()
            edit_btn.set_child(_svg_icon(SVG_SETTINGS))
            edit_btn.set_has_frame(False)
            edit_btn.add_css_class("sayri-icon-btn")
            edit_btn.set_tooltip_text("Edit Gateway Instance Settings")
            edit_btn.connect("clicked", lambda _b, it=inst: self.show_edit_gateway_view(it))
            header_row.append(edit_btn)

            # Delete Button
            del_gw_btn = Gtk.Button()
            del_gw_btn.set_child(_svg_icon(SVG_TRASH))
            del_gw_btn.set_has_frame(False)
            del_gw_btn.add_css_class("sayri-icon-btn")
            del_gw_btn.set_tooltip_text("Delete Gateway Instance")
            del_gw_btn.connect("clicked", lambda _b, i_id=inst_id: (gateway_supervisor.delete_instance(i_id), self._populate_gateways()))
            header_row.append(del_gw_btn)

            # Running status & switch
            is_running = gateway_supervisor.is_instance_running(inst_id)
            sec_key = inst.get("secret_key", "")
            has_secret = bool(secrets_manager.get_secret(sec_key) or sec_key in os.environ) if sec_key else False

            sw = Gtk.Switch()
            sw.set_active(is_running)
            sw.set_valign(Gtk.Align.CENTER)
            def _on_sw_toggle(switch, _param, i_id=inst_id):
                if switch.get_active():
                    ok, msg = gateway_supervisor.start_instance(i_id)
                    if not ok:
                        switch.set_active(False)
                else:
                    gateway_supervisor.stop_instance(i_id)
                self._populate_gateways()
            sw.connect("notify::active", _on_sw_toggle)
            header_row.append(sw)

            card.append(header_row)

            # Description / Status row
            if is_running:
                status_text = "<span foreground='#22c55e' weight='600'>● Active (Listening)</span>"
            elif not has_secret and sec_key:
                status_text = f"<span foreground='#eab308' weight='600'>Set {sec_key} to Start</span>"
            else:
                status_text = "<span foreground='#94a3b8'>○ Daemon Stopped</span>"

            # Check paired accounts for this instance
            auth_file = Path.home() / ".config" / "sayri" / f"authorizations_{inst_id}.json"
            if not auth_file.is_file():
                auth_file = Path.home() / ".config" / "sayri" / "authorizations.json"
            if auth_file.is_file():
                try:
                    auth_data = json.loads(auth_file.read_text(encoding="utf-8"))
                    paired = auth_data.get("allowed_telegram_users", [])
                    if paired:
                        paired_str = ', '.join(str(p) for p in paired[:2])
                        status_text += f" • {len(paired)} Paired ({paired_str})"
                except Exception:
                    pass

            desc_lbl = Gtk.Label()
            p_desc = plugin_meta.get("description", "Channel Gateway instance")
            desc_lbl.set_markup(f"<span foreground='#94a3b8' size='9000'>{GLib.markup_escape_text(p_desc)} • {status_text}</span>")
            desc_lbl.set_halign(Gtk.Align.START)
            desc_lbl.set_wrap(True)
            card.append(desc_lbl)

            # Action bar: Show Pairing PIN or Set Token
            act_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            act_bar.set_margin_top(2)

            if inst.get("auth_mode") == "pairing_otp":
                pair_btn = Gtk.Button(label="Show Pairing PIN")
                pair_btn.add_css_class("sayri-action-btn")
                pair_btn.connect("clicked", lambda _b, it=inst, pm=plugin_meta: self.show_generic_otp_pairing_view(pm, instance_id=it["id"]))
                act_bar.append(pair_btn)

            if sec_key and not has_secret:
                cfg_btn = Gtk.Button(label=f"Set {sec_key}")
                cfg_btn.add_css_class("sayri-action-btn")
                cfg_btn.connect("clicked", lambda _b, it=inst: self.show_edit_gateway_view(it))
                act_bar.append(cfg_btn)

            card.append(act_bar)
            self.gateways_box.append(card)

    def _populate_plugins(self) -> None:
        self._populate_plugins_tools()
        self._populate_gateways()

    # ── Automated Routines & Cron Jobs Logic ──
    def _populate_routines(self) -> None:
        while True:
            child = self.routines_box.get_first_child()
            if not child:
                break
            self.routines_box.remove(child)

        from sayri.domain.cron_scheduler import cron_scheduler

        routines = cron_scheduler.list_routines()
        if not routines:
            empty_lbl = Gtk.Label(label="No automated routines configured yet. Click (+ Add Routine) to schedule morning news, reminders, or background tasks.")
            empty_lbl.set_halign(Gtk.Align.START)
            empty_lbl.set_wrap(True)
            empty_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            self.routines_box.append(empty_lbl)
            return

        for r in routines:
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            card.add_css_class("sayri-card-item")

            header_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            header_row.set_valign(Gtk.Align.CENTER)

            t = Gtk.Label()
            t.set_markup(f"<span foreground='#ffffff' weight='700' size='10000'>{GLib.markup_escape_text(r.name)}</span>")
            t.set_halign(Gtk.Align.START)
            t.set_hexpand(True)
            header_row.append(t)

            # Trigger badge
            trig_badge = Gtk.Label()
            if r.trigger == "on_login":
                trig_str = "🌅 On Startup / Login"
            elif r.trigger == "daily_at":
                trig_str = f"⏰ Daily at {r.time_spec}"
            elif r.trigger == "hourly":
                trig_str = f"⏳ Every {r.time_spec}h"
            else:
                trig_str = f"⚙️ {r.trigger}"
            trig_badge.set_markup(f"<span foreground='#38bdf8' size='8500' weight='600'>{GLib.markup_escape_text(trig_str)}</span>")
            header_row.append(trig_badge)

            # Voice badge
            if r.speak_tts:
                v_badge = Gtk.Label()
                v_badge.set_markup("<span foreground='#10b981' size='8500'>🔊 Voice</span>")
                header_row.append(v_badge)

            # Edit button
            edit_btn = Gtk.Button()
            edit_btn.set_child(_svg_icon(SVG_SETTINGS))
            edit_btn.set_has_frame(False)
            edit_btn.add_css_class("sayri-icon-btn")
            edit_btn.set_tooltip_text("Edit Routine")
            edit_btn.connect("clicked", lambda _b, rt=r: self.show_edit_routine_view(rt))
            header_row.append(edit_btn)

            # Delete button
            del_btn = Gtk.Button()
            del_btn.set_child(_svg_icon(SVG_TRASH))
            del_btn.set_has_frame(False)
            del_btn.add_css_class("sayri-icon-btn")
            del_btn.set_tooltip_text("Delete Routine")
            del_btn.connect("clicked", lambda _b, r_id=r.id: (cron_scheduler.delete_routine(r_id), self._populate_routines()))
            header_row.append(del_btn)

            # Enable/Disable switch
            sw = Gtk.Switch()
            sw.set_active(r.enabled)
            sw.set_valign(Gtk.Align.CENTER)
            def _on_sw_toggle(switch, _param, r_id=r.id):
                cron_scheduler.toggle_routine(r_id, switch.get_active())
            sw.connect("notify::active", _on_sw_toggle)
            header_row.append(sw)

            card.append(header_row)

            # Subtitle / prompt preview
            sub_lbl = Gtk.Label()
            p_desc = r.description or r.prompt
            sub_lbl.set_markup(f"<span foreground='#94a3b8' size='9000'><i>“{GLib.markup_escape_text(p_desc[:80])}…”</i></span>")
            sub_lbl.set_halign(Gtk.Align.START)
            sub_lbl.set_wrap(True)
            card.append(sub_lbl)

            self.routines_box.append(card)

    def show_create_routine_view(self) -> None:
        def _builder(box: Gtk.Box):
            lbl_name = Gtk.Label(label="Routine Name:")
            lbl_name.set_halign(Gtk.Align.START)
            box.append(lbl_name)

            name_entry = Gtk.Entry()
            name_entry.set_placeholder_text("e.g. Morning Briefing & News")
            name_entry.add_css_class("sayri-settings-entry")
            box.append(name_entry)

            lbl_prompt = Gtk.Label(label="Instructions / Prompt for Sayri:")
            lbl_prompt.set_halign(Gtk.Align.START)
            box.append(lbl_prompt)

            prompt_entry = Gtk.Entry()
            prompt_entry.set_placeholder_text("e.g. Sayri, dame un saludo matutino y el resumen de noticias de hoy en 3 frases.")
            prompt_entry.add_css_class("sayri-settings-entry")
            box.append(prompt_entry)

            lbl_trig = Gtk.Label(label="Trigger Schedule:")
            lbl_trig.set_halign(Gtk.Align.START)
            box.append(lbl_trig)

            trig_options = [
                ("on_login", "🌅 On Startup / User Login"),
                ("daily_at", "⏰ Daily at Specific Time (HH:MM)"),
                ("hourly", "⏳ Periodic Interval (Every X Hours)"),
            ]
            trig_model = Gtk.StringList.new([opt[1] for opt in trig_options])
            trig_drop = Gtk.DropDown.new(trig_model, None)
            trig_drop.set_selected(0)
            box.append(trig_drop)

            lbl_spec = Gtk.Label(label="Time / Interval Parameter (e.g. 09:00 or 2 for hours):")
            lbl_spec.set_halign(Gtk.Align.START)
            box.append(lbl_spec)

            spec_entry = Gtk.Entry()
            spec_entry.set_text("09:00")
            spec_entry.add_css_class("sayri-settings-entry")
            box.append(spec_entry)

            voice_check = Gtk.CheckButton(label="🔊 Read response aloud with Voice (TTS)")
            voice_check.set_active(True)
            box.append(voice_check)

            notify_check = Gtk.CheckButton(label="🔔 Show Desktop Notification")
            notify_check.set_active(True)
            box.append(notify_check)

            save_btn = Gtk.Button(label="Create Routine")
            save_btn.add_css_class("sayri-action-btn")
            save_btn.add_css_class("primary")

            def _save(_b):
                from sayri.domain.cron_scheduler import Routine, cron_scheduler
                nm = name_entry.get_text().strip()
                pr = prompt_entry.get_text().strip()
                if nm and pr:
                    sel_trig = trig_options[trig_drop.get_selected()][0]
                    rid = re.sub(r"[^a-zA-Z0-9_\-]", "-", nm.lower()).strip("-") or f"routine-{int(time.time())}"
                    new_r = Routine(
                        id=rid,
                        name=nm,
                        description=f"Scheduled routine ({sel_trig})",
                        trigger=sel_trig,
                        time_spec=spec_entry.get_text().strip() or "09:00",
                        prompt=pr,
                        speak_tts=voice_check.get_active(),
                        notify_desktop=notify_check.get_active(),
                        enabled=True,
                    )
                    cron_scheduler.save_routine(new_r)
                    self._populate_routines()
                    self.switch_tab("routines")

            save_btn.connect("clicked", _save)
            box.append(save_btn)

        self.open_subview("Add Routine / Cron Job", _builder, on_back_tab="routines")

    def show_edit_routine_view(self, routine: Any) -> None:
        def _builder(box: Gtk.Box):
            lbl_name = Gtk.Label(label="Routine Name:")
            lbl_name.set_halign(Gtk.Align.START)
            box.append(lbl_name)

            name_entry = Gtk.Entry()
            name_entry.set_text(routine.name)
            name_entry.add_css_class("sayri-settings-entry")
            box.append(name_entry)

            lbl_prompt = Gtk.Label(label="Instructions / Prompt for Sayri:")
            lbl_prompt.set_halign(Gtk.Align.START)
            box.append(lbl_prompt)

            prompt_entry = Gtk.Entry()
            prompt_entry.set_text(routine.prompt)
            prompt_entry.add_css_class("sayri-settings-entry")
            box.append(prompt_entry)

            lbl_spec = Gtk.Label(label=f"Time / Parameter (Trigger: {routine.trigger}):")
            lbl_spec.set_halign(Gtk.Align.START)
            box.append(lbl_spec)

            spec_entry = Gtk.Entry()
            spec_entry.set_text(routine.time_spec)
            spec_entry.add_css_class("sayri-settings-entry")
            box.append(spec_entry)

            voice_check = Gtk.CheckButton(label="🔊 Read response aloud with Voice (TTS)")
            voice_check.set_active(routine.speak_tts)
            box.append(voice_check)

            notify_check = Gtk.CheckButton(label="🔔 Show Desktop Notification")
            notify_check.set_active(routine.notify_desktop)
            box.append(notify_check)

            save_btn = Gtk.Button(label="Save Changes")
            save_btn.add_css_class("sayri-action-btn")
            save_btn.add_css_class("primary")

            def _save(_b):
                from sayri.domain.cron_scheduler import cron_scheduler
                routine.name = name_entry.get_text().strip() or routine.name
                routine.prompt = prompt_entry.get_text().strip() or routine.prompt
                routine.time_spec = spec_entry.get_text().strip() or routine.time_spec
                routine.speak_tts = voice_check.get_active()
                routine.notify_desktop = notify_check.get_active()
                cron_scheduler.save_routine(routine)
                self._populate_routines()
                self.switch_tab("routines")

            save_btn.connect("clicked", _save)
            box.append(save_btn)

        self.open_subview(f"Edit Routine: {routine.name}", _builder, on_back_tab="routines")

    # ── Zero-Plaintext Secrets Vault Logic ──
    def _populate_secrets(self) -> None:
        while True:
            child = self.secrets_box.get_first_child()
            if not child:
                break
            self.secrets_box.remove(child)

        secrets = secrets_manager.list_secrets()
        if not secrets:
            lbl = Gtk.Label(label="No secret credentials stored yet. Click (+ Add Secret) to add tokens with zero plaintext leakage.")
            lbl.set_halign(Gtk.Align.START)
            lbl.set_wrap(True)
            lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            self.secrets_box.append(lbl)
            return

        for sec in secrets:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.add_css_class("sayri-card-item")

            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            v.set_hexpand(True)

            t = Gtk.Label()
            t.set_markup(f"<span foreground='#38bdf8' weight='700' size='10500'>${GLib.markup_escape_text(sec['key'])}</span>")
            t.set_halign(Gtk.Align.START)

            sub = Gtk.Label()
            desc = sec['description'] or 'Injected into sandbox environment variables at runtime'
            sub.set_markup(f"<span foreground='#94a3b8' size='9000'>{GLib.markup_escape_text(desc)} • Value: <tt>{GLib.markup_escape_text(sec['masked'])}</tt></span>")
            sub.set_halign(Gtk.Align.START)

            handle_lbl = Gtk.Label()
            handle_lbl.set_markup(f"<span foreground='#1e74fb' size='8500'>Prompt handle: <tt>$SECRET:{GLib.markup_escape_text(sec['key'])}</tt></span>")
            handle_lbl.set_halign(Gtk.Align.START)

            v.append(t)
            v.append(sub)
            v.append(handle_lbl)
            row.append(v)

            # Copy handle button
            copy_b = Gtk.Button()
            copy_b.set_child(_svg_icon(SVG_COPY))
            copy_b.set_has_frame(False)
            copy_b.add_css_class("sayri-icon-btn")
            copy_b.set_tooltip_text("Copy $SECRET handle to clipboard")
            def _copy(_b, k=sec['key']):
                clipboard = Gdk.Display.get_default().get_clipboard()
                clipboard.set(f"$SECRET:{k}")
            copy_b.connect("clicked", _copy)
            row.append(copy_b)

            # Delete button
            del_b = Gtk.Button()
            del_b.set_child(_svg_icon(SVG_TRASH))
            del_b.set_has_frame(False)
            del_b.add_css_class("sayri-icon-btn")
            del_b.set_tooltip_text("Delete secret")
            del_b.connect("clicked", lambda _b, k=sec['key']: (secrets_manager.delete_secret(k), self._populate_secrets()))
            row.append(del_b)

            self.secrets_box.append(row)

    # ── In-Card Settings & Full Model Downloader with Live Progress ──
    def _populate_settings(self) -> None:
        while True:
            child = self.settings_box.get_first_child()
            if not child:
                break
            self.settings_box.remove(child)

        cfg = getattr(self.app, "cfg", None)
        cur_url = cfg.get_string("provider", "base_url") if cfg else "https://api.groq.com/openai/v1"
        cur_key = cfg.get_string("provider", "api_key") if cfg else ""
        cur_model = cfg.get_string("provider", "model") if cfg else "llama-3.3-70b-versatile"
        cur_wakeword = cfg.get_string("stt", "wake_word") if cfg else "hey sayri"
        cur_stt_model = cfg.get_string("stt", "model_size") if cfg else "base"
        cur_stt_lang = cfg.get_string("stt", "language") if cfg else "es"
        cur_tts_voice = cfg.get_string("tts", "voice") if cfg else "sharvard"
        cur_tts_lang = cfg.get_string("tts", "language") if cfg else "es_ES"

        def _field(label_text, default_val, is_secret=False):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            l = Gtk.Label()
            l.set_halign(Gtk.Align.START)
            l.set_markup(f"<span foreground='#94a3b8' size='9000'><b>{label_text}</b></span>")
            e = Gtk.Entry()
            e.add_css_class("sayri-settings-entry")
            e.set_text(default_val or "")
            if is_secret:
                e.set_visibility(False)
            box.append(l)
            box.append(e)
            return box, e

        # Section 1: LLM
        sec1_title = Gtk.Label()
        sec1_title.set_markup("<span weight='700' size='9500' foreground='#1e74fb'>LLM PROVIDER CONFIGURATION</span>")
        sec1_title.set_halign(Gtk.Align.START)
        self.settings_box.append(sec1_title)

        b1, url_entry = _field("Base URL", cur_url)
        b2, key_entry = _field("API Key (Token Shield Protected)", cur_key, is_secret=True)
        b3, model_entry = _field("Model Name", cur_model)
        b4, wake_entry = _field("Wakeword Trigger", cur_wakeword)

        self.settings_box.append(b1)
        self.settings_box.append(b2)
        self.settings_box.append(b3)
        self.settings_box.append(b4)

        save_btn = Gtk.Button(label="Save LLM Settings")
        save_btn.add_css_class("sayri-action-btn")
        save_btn.add_css_class("primary")
        save_btn.set_halign(Gtk.Align.END)
        save_btn.set_margin_top(4)

        def _on_save(_b):
            if cfg:
                cfg.set("provider", "base_url", url_entry.get_text().strip())
                cfg.set("provider", "api_key", key_entry.get_text().strip())
                cfg.set("provider", "model", model_entry.get_text().strip())
                cfg.set("stt", "wake_word", wake_entry.get_text().strip())
                save_btn.set_label("✓ Settings Saved!")
                GLib.timeout_add(1500, lambda: (save_btn.set_label("Save LLM Settings"), False))

        save_btn.connect("clicked", _on_save)
        self.settings_box.append(save_btn)

        # Section 2: Speech-to-Text (STT Whisper)
        sep1 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep1.set_margin_top(6)
        sep1.set_margin_bottom(6)
        self.settings_box.append(sep1)

        sec2_title = Gtk.Label()
        sec2_title.set_markup("<span weight='700' size='9500' foreground='#1e74fb'>SPEECH-TO-TEXT (WHISPER ALL MODELS &amp; LANGUAGES)</span>")
        sec2_title.set_halign(Gtk.Align.START)
        self.settings_box.append(sec2_title)

        stt_config_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        stt_config_box.add_css_class("sayri-card-item")

        # STT Mode Selector (Enable / Disable / Wakeword / Manual)
        stt_mode_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        stt_enable_lbl = Gtk.Label()
        stt_enable_lbl.set_markup("<span size='9500' foreground='#ffffff'><b>Speech Recognition (STT):</b></span>")
        stt_enable_lbl.set_halign(Gtk.Align.START)
        stt_enable_lbl.set_hexpand(True)
        stt_mode_row.append(stt_enable_lbl)

        stt_modes = ["wakeword", "always", "manual", "disabled"]
        stt_mode_labels = [
            "Wakeword ('Hey Sayri')",
            "Always Listening",
            "Manual (Push to Talk)",
            "Disabled (No Voice Input)"
        ]
        stt_mode_model = Gtk.StringList.new(stt_mode_labels)
        stt_mode_drop = Gtk.DropDown.new(stt_mode_model, None)
        cur_stt_mode = cfg.get_string("stt", "mode") if cfg else "wakeword"
        try:
            stt_mode_drop.set_selected(stt_modes.index(cur_stt_mode))
        except Exception:
            stt_mode_drop.set_selected(0)

        def _on_stt_mode_changed(*_):
            m = stt_modes[stt_mode_drop.get_selected()]
            if cfg:
                cfg.set("stt", "mode", m)
            if hasattr(self.app, "_apply_mode"):
                self.app._apply_mode()
        stt_mode_drop.connect("notify::selected", _on_stt_mode_changed)
        stt_mode_row.append(stt_mode_drop)
        stt_config_box.append(stt_mode_row)

        # Full Whisper Models & Languages Dropdowns
        stt_ctrl_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        stt_sizes = ["tiny", "tiny.en", "base", "base.en", "small", "small.en", "medium", "medium.en", "large-v3"]
        stt_size_labels = [
            "tiny (~75MB)", "tiny.en (English, ~75MB)",
            "base (~142MB)", "base.en (English, ~142MB)",
            "small (~466MB)", "small.en (English, ~466MB)",
            "medium (~1.5GB)", "medium.en (English, ~1.5GB)",
            "large-v3 (~3.1GB, Best Quality)"
        ]
        stt_size_model = Gtk.StringList.new(stt_size_labels)
        stt_size_drop = Gtk.DropDown.new(stt_size_model, None)
        try:
            stt_size_drop.set_selected(stt_sizes.index(cur_stt_model))
        except Exception:
            stt_size_drop.set_selected(2)

        stt_langs = ["auto", "es", "en", "fr", "de", "it", "pt", "ca", "gl", "eu", "nl", "pl", "ru", "uk", "sv", "tr", "el", "ar", "zh", "ja", "ko", "hi"]
        stt_lang_labels = [
            "auto (Auto Detect)", "es (Spanish - Español)", "en (English)",
            "fr (French - Français)", "de (German - Deutsch)", "it (Italian - Italiano)",
            "pt (Portuguese - Português)", "ca (Catalan - Català)", "gl (Galician - Galego)",
            "eu (Basque - Euskara)", "nl (Dutch - Nederlands)", "pl (Polish - Polski)",
            "ru (Russian - Русский)", "uk (Ukrainian - Українська)", "sv (Swedish - Svenska)",
            "tr (Turkish - Türkçe)", "el (Greek - Ελληνικά)", "ar (Arabic - العربية)",
            "zh (Chinese - 中文)", "ja (Japanese - 日本語)", "ko (Korean - 한국어)", "hi (Hindi - हिन्दी)"
        ]
        stt_lang_model = Gtk.StringList.new(stt_lang_labels)
        stt_lang_drop = Gtk.DropDown.new(stt_lang_model, None)
        try:
            stt_lang_drop.set_selected(stt_langs.index(cur_stt_lang))
        except Exception:
            stt_lang_drop.set_selected(1)

        stt_ctrl_row.append(stt_size_drop)
        stt_ctrl_row.append(stt_lang_drop)
        stt_config_box.append(stt_ctrl_row)

        # Status & Action Row
        stt_act_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        stt_act_row.set_margin_top(2)

        stt_status_lbl = Gtk.Label()
        stt_status_lbl.set_halign(Gtk.Align.START)
        stt_status_lbl.set_hexpand(True)
        stt_act_row.append(stt_status_lbl)

        dl_stt_btn = Gtk.Button()
        dl_stt_btn.add_css_class("sayri-action-btn")
        stt_act_row.append(dl_stt_btn)

        # Live Progress Bar
        stt_prog_bar = Gtk.ProgressBar()
        stt_prog_bar.add_css_class("sayri-progress")
        stt_prog_bar.set_visible(False)

        def _update_stt_status():
            sz = stt_sizes[stt_size_drop.get_selected()]
            lg = stt_langs[stt_lang_drop.get_selected()]
            has_m = downloads.has_whisper_model(sz, lg)
            stt_status_lbl.set_markup(f"<span foreground='{'#22c55e' if has_m else '#eab308'}' size='9000'>{'✓ Ready &amp; Downloaded' if has_m else 'Not downloaded'}</span>")
            dl_stt_btn.set_label("Downloaded" if has_m else "Download Model")
            if has_m:
                dl_stt_btn.remove_css_class("primary")
            else:
                dl_stt_btn.add_css_class("primary")
            if cfg:
                cfg.set("stt", "model_size", sz)
                cfg.set("stt", "language", lg)

        stt_size_drop.connect("notify::selected", lambda *_: _update_stt_status())
        stt_lang_drop.connect("notify::selected", lambda *_: _update_stt_status())
        _update_stt_status()

        def _do_dl_stt(_b):
            sz = stt_sizes[stt_size_drop.get_selected()]
            lg = stt_langs[stt_lang_drop.get_selected()]
            dl_stt_btn.set_sensitive(False)
            dl_stt_btn.set_label("Downloading…")
            stt_prog_bar.set_fraction(0.0)
            stt_prog_bar.set_visible(True)

            def _on_prog(frac: float):
                GLib.idle_add(lambda: (
                    stt_prog_bar.set_fraction(frac),
                    stt_status_lbl.set_markup(f"<span foreground='#1e74fb' size='9000'>Downloading model… <b>{int(frac*100)}%</b></span>")
                ))

            def _thread():
                try:
                    downloads.download_whisper_model(sz, lg, progress=_on_prog)
                    GLib.idle_add(lambda: (
                        stt_prog_bar.set_visible(False),
                        _update_stt_status(),
                        dl_stt_btn.set_sensitive(True)
                    ))
                except Exception as exc:
                    GLib.idle_add(lambda: (
                        stt_prog_bar.set_visible(False),
                        dl_stt_btn.set_label("Retry"),
                        dl_stt_btn.set_sensitive(True),
                        stt_status_lbl.set_markup(f"<span foreground='#ef4444' size='9000'>Error: {exc}</span>")
                    ))
            threading.Thread(target=_thread, daemon=True).start()

        dl_stt_btn.connect("clicked", _do_dl_stt)
        stt_config_box.append(stt_act_row)
        stt_config_box.append(stt_prog_bar)

        # Whisper-cli Binary Row
        has_w_bin = paths.find_binary("whisper-cli") is not None
        w_bin_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        w_bin_row.set_margin_top(4)

        w_bin_lbl = Gtk.Label()
        w_bin_lbl.set_markup(f"<span size='9000' foreground='{'#22c55e' if has_w_bin else '#94a3b8'}'>whisper-cli binary: {'✓ Installed' if has_w_bin else 'Not installed'}</span>")
        w_bin_lbl.set_halign(Gtk.Align.START)
        w_bin_lbl.set_hexpand(True)
        w_bin_row.append(w_bin_lbl)

        dl_w_bin_btn = Gtk.Button(label="Installed" if has_w_bin else "Download Binary")
        dl_w_bin_btn.add_css_class("sayri-action-btn")
        if not has_w_bin:
            dl_w_bin_btn.add_css_class("primary")

        w_bin_prog = Gtk.ProgressBar()
        w_bin_prog.add_css_class("sayri-progress")
        w_bin_prog.set_visible(False)

        def _do_dl_w_bin(_b):
            dl_w_bin_btn.set_sensitive(False)
            dl_w_bin_btn.set_label("Downloading…")
            w_bin_prog.set_fraction(0.0)
            w_bin_prog.set_visible(True)

            def _on_w_prog(frac: float):
                GLib.idle_add(lambda: (
                    w_bin_prog.set_fraction(frac),
                    w_bin_lbl.set_markup(f"<span size='9000' foreground='#1e74fb'>Downloading binary… <b>{int(frac*100)}%</b></span>")
                ))

            def _thread():
                try:
                    downloads.install_whisper_cli(progress=_on_w_prog)
                    GLib.idle_add(lambda: (
                        w_bin_prog.set_visible(False),
                        dl_w_bin_btn.set_label("Installed"),
                        dl_w_bin_btn.remove_css_class("primary"),
                        w_bin_lbl.set_markup("<span size='9000' foreground='#22c55e'>whisper-cli binary: ✓ Installed</span>")
                    ))
                except Exception as exc:
                    GLib.idle_add(lambda: (
                        w_bin_prog.set_visible(False),
                        dl_w_bin_btn.set_label("Retry"),
                        dl_w_bin_btn.set_sensitive(True),
                        w_bin_lbl.set_markup(f"<span size='9000' foreground='#ef4444'>Error: {exc}</span>")
                    ))
            threading.Thread(target=_thread, daemon=True).start()

        dl_w_bin_btn.connect("clicked", _do_dl_w_bin)
        w_bin_row.append(dl_w_bin_btn)
        stt_config_box.append(w_bin_row)
        stt_config_box.append(w_bin_prog)

        self.settings_box.append(stt_config_box)

        # Section 3: Text-to-Speech (TTS Piper - Exhaustive Voice Catalog & Progress)
        sep2 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep2.set_margin_top(6)
        sep2.set_margin_bottom(6)
        self.settings_box.append(sep2)

        sec3_title = Gtk.Label()
        sec3_title.set_markup("<span weight='700' size='9500' foreground='#1e74fb'>TEXT-TO-SPEECH (PIPER ALL VOICES &amp; LANGUAGES)</span>")
        sec3_title.set_halign(Gtk.Align.START)
        self.settings_box.append(sec3_title)

        tts_config_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        tts_config_box.add_css_class("sayri-card-item")

        # TTS Enable Switch
        tts_toggle_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        tts_enable_lbl = Gtk.Label()
        tts_enable_lbl.set_markup("<span size='9500' foreground='#ffffff'><b>Voice Audio Playback (TTS):</b></span>")
        tts_enable_lbl.set_halign(Gtk.Align.START)
        tts_enable_lbl.set_hexpand(True)
        tts_toggle_row.append(tts_enable_lbl)

        tts_sw = Gtk.Switch()
        tts_sw.set_valign(Gtk.Align.CENTER)
        tts_sw.set_active(cfg.get_bool("tts", "enabled") if cfg else True)
        def _on_tts_toggled(sw, _gparam):
            if cfg:
                cfg.set("tts", "enabled", sw.get_active())
        tts_sw.connect("notify::active", _on_tts_toggled)
        tts_toggle_row.append(tts_sw)
        tts_config_box.append(tts_toggle_row)

        voice_options = [
            ("es_ES", "sharvard", "medium", "es_ES: sharvard (medium, ~63MB)"),
            ("es_ES", "davefx", "medium", "es_ES: davefx (medium, ~63MB)"),
            ("es_ES", "carlfm", "x_low", "es_ES: carlfm (fast, ~16MB)"),
            ("es_MX", "ald", "medium", "es_MX: ald (medium, ~63MB)"),
            ("es_MX", "claude", "high", "es_MX: claude (high quality, ~120MB)"),
            ("ca_ES", "upc_ona", "medium", "ca_ES: upc_ona (medium, ~63MB)"),
            ("ca_ES", "upc_pau", "medium", "ca_ES: upc_pau (medium, ~63MB)"),
            ("en_US", "amy", "medium", "en_US: amy (medium, ~63MB)"),
            ("en_US", "lessac", "medium", "en_US: lessac (medium, ~63MB)"),
            ("en_US", "ryan", "high", "en_US: ryan (high quality, ~120MB)"),
            ("en_US", "joe", "medium", "en_US: joe (medium, ~62MB)"),
            ("en_US", "kathleen", "low", "en_US: kathleen (low, ~41MB)"),
            ("en_GB", "alan", "medium", "en_GB: alan (medium, ~63MB)"),
            ("en_GB", "alba", "medium", "en_GB: alba (medium, ~63MB)"),
            ("en_GB", "northern_english_male", "medium", "en_GB: northern_english (medium, ~63MB)"),
            ("en_GB", "cori", "medium", "en_GB: cori (medium, ~63MB)"),
            ("fr_FR", "siwis", "medium", "fr_FR: siwis (medium, ~62MB)"),
            ("fr_FR", "upmc", "medium", "fr_FR: upmc (medium, ~62MB)"),
            ("de_DE", "thorsten", "medium", "de_DE: thorsten (medium, ~63MB)"),
            ("de_DE", "ramona", "low", "de_DE: ramona (low, ~19MB)"),
            ("it_IT", "paola", "medium", "it_IT: paola (medium, ~63MB)"),
            ("pt_BR", "faber", "medium", "pt_BR: faber (medium, ~63MB)"),
            ("pt_BR", "edresson", "low", "pt_BR: edresson (low, ~28MB)"),
            ("nl_NL", "mls_7432", "low", "nl_NL: mls_7432 (low, ~28MB)"),
            ("ru_RU", "irina", "medium", "ru_RU: irina (medium, ~62MB)"),
            ("pl_PL", "darkman", "medium", "pl_PL: darkman (medium, ~62MB)"),
            ("zh_CN", "huayan", "medium", "zh_CN: huayan (medium, ~63MB)"),
            ("sv_SE", "nst", "medium", "sv_SE: nst (medium, ~63MB)"),
            ("uk_UA", "lada", "medium", "uk_UA: lada (medium, ~63MB)"),
            ("tr_TR", "fdf", "medium", "tr_TR: fdf (medium, ~63MB)"),
            ("el_GR", "rapunzelina", "low", "el_GR: rapunzelina (low, ~28MB)"),
            ("ar_JO", "kareem", "medium", "ar_JO: kareem (medium, ~63MB)"),
        ]

        voice_labels = [opt[3] for opt in voice_options]
        voice_model = Gtk.StringList.new(voice_labels)
        voice_drop = Gtk.DropDown.new(voice_model, None)

        selected_voice_idx = 0
        for i, opt in enumerate(voice_options):
            if opt[0] == cur_tts_lang and opt[1] == cur_tts_voice:
                selected_voice_idx = i
                break
        voice_drop.set_selected(selected_voice_idx)
        tts_config_box.append(voice_drop)

        # Voice Status & Action Row
        tts_act_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        tts_act_row.set_margin_top(2)

        tts_status_lbl = Gtk.Label()
        tts_status_lbl.set_halign(Gtk.Align.START)
        tts_status_lbl.set_hexpand(True)
        tts_act_row.append(tts_status_lbl)

        dl_tts_btn = Gtk.Button()
        dl_tts_btn.add_css_class("sayri-action-btn")
        tts_act_row.append(dl_tts_btn)

        # Live Progress Bar for Voice
        tts_prog_bar = Gtk.ProgressBar()
        tts_prog_bar.add_css_class("sayri-progress")
        tts_prog_bar.set_visible(False)

        def _update_tts_status():
            idx = voice_drop.get_selected()
            opt = voice_options[idx]
            has_v = downloads.has_piper_voice(opt[0], opt[1], opt[2])
            tts_status_lbl.set_markup(f"<span foreground='{'#22c55e' if has_v else '#eab308'}' size='9000'>{'✓ Ready &amp; Downloaded' if has_v else 'Not downloaded'}</span>")
            dl_tts_btn.set_label("Downloaded" if has_v else "Download Voice")
            if has_v:
                dl_tts_btn.remove_css_class("primary")
            else:
                dl_tts_btn.add_css_class("primary")
            if cfg:
                cfg.set("tts", "language", opt[0])
                cfg.set("tts", "voice", opt[1])
                cfg.set("tts", "quality", opt[2])

        voice_drop.connect("notify::selected", lambda *_: _update_tts_status())
        _update_tts_status()

        def _do_dl_tts(_b):
            idx = voice_drop.get_selected()
            opt = voice_options[idx]
            dl_tts_btn.set_sensitive(False)
            dl_tts_btn.set_label("Downloading…")
            tts_prog_bar.set_fraction(0.0)
            tts_prog_bar.set_visible(True)

            def _on_tts_prog(frac: float):
                GLib.idle_add(lambda: (
                    tts_prog_bar.set_fraction(frac),
                    tts_status_lbl.set_markup(f"<span foreground='#1e74fb' size='9000'>Downloading voice… <b>{int(frac*100)}%</b></span>")
                ))

            def _thread():
                try:
                    downloads.download_piper_voice(opt[0], opt[1], opt[2], progress=_on_tts_prog)
                    GLib.idle_add(lambda: (
                        tts_prog_bar.set_visible(False),
                        _update_tts_status(),
                        dl_tts_btn.set_sensitive(True)
                    ))
                except Exception as exc:
                    GLib.idle_add(lambda: (
                        tts_prog_bar.set_visible(False),
                        dl_tts_btn.set_label("Retry"),
                        dl_tts_btn.set_sensitive(True),
                        tts_status_lbl.set_markup(f"<span foreground='#ef4444' size='9000'>Error: {exc}</span>")
                    ))
            threading.Thread(target=_thread, daemon=True).start()

        dl_tts_btn.connect("clicked", _do_dl_tts)
        tts_config_box.append(tts_act_row)
        tts_config_box.append(tts_prog_bar)

        # Piper Binary Row
        has_p_bin = paths.find_binary("piper") is not None
        p_bin_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        p_bin_row.set_margin_top(4)

        p_bin_lbl = Gtk.Label()
        p_bin_lbl.set_markup(f"<span size='9000' foreground='{'#22c55e' if has_p_bin else '#94a3b8'}'>piper binary: {'✓ Installed' if has_p_bin else 'Not installed'}</span>")
        p_bin_lbl.set_halign(Gtk.Align.START)
        p_bin_lbl.set_hexpand(True)
        p_bin_row.append(p_bin_lbl)

        dl_p_bin_btn = Gtk.Button(label="Installed" if has_p_bin else "Download Binary")
        dl_p_bin_btn.add_css_class("sayri-action-btn")
        if not has_p_bin:
            dl_p_bin_btn.add_css_class("primary")

        p_bin_prog = Gtk.ProgressBar()
        p_bin_prog.add_css_class("sayri-progress")
        p_bin_prog.set_visible(False)

        def _do_dl_p_bin(_b):
            dl_p_bin_btn.set_sensitive(False)
            dl_p_bin_btn.set_label("Downloading…")
            p_bin_prog.set_fraction(0.0)
            p_bin_prog.set_visible(True)

            def _on_p_prog(frac: float):
                GLib.idle_add(lambda: (
                    p_bin_prog.set_fraction(frac),
                    p_bin_lbl.set_markup(f"<span size='9000' foreground='#1e74fb'>Downloading binary… <b>{int(frac*100)}%</b></span>")
                ))

            def _thread():
                try:
                    downloads.install_piper(progress=_on_p_prog)
                    GLib.idle_add(lambda: (
                        p_bin_prog.set_visible(False),
                        dl_p_bin_btn.set_label("Installed"),
                        dl_p_bin_btn.remove_css_class("primary"),
                        p_bin_lbl.set_markup("<span size='9000' foreground='#22c55e'>piper binary: ✓ Installed</span>")
                    ))
                except Exception as exc:
                    GLib.idle_add(lambda: (
                        p_bin_prog.set_visible(False),
                        dl_p_bin_btn.set_label("Retry"),
                        dl_p_bin_btn.set_sensitive(True),
                        p_bin_lbl.set_markup(f"<span size='9000' foreground='#ef4444'>Error: {exc}</span>")
                    ))
            threading.Thread(target=_thread, daemon=True).start()

        dl_p_bin_btn.connect("clicked", _do_dl_p_bin)
        tts_config_box.append(p_bin_row)
        tts_config_box.append(p_bin_prog)

        self.settings_box.append(tts_config_box)

        # Section 4: System Daemons & Process Management
        sys_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        sys_box.add_css_class("sayri-card-item")
        sys_box.set_margin_top(8)

        sys_title = Gtk.Label()
        sys_title.set_markup("<span weight='700' size='9500' foreground='#ef4444'>PROCESS MANAGEMENT</span>")
        sys_title.set_halign(Gtk.Align.START)
        sys_box.append(sys_title)

        sys_desc = Gtk.Label()
        sys_desc.set_markup("<span size='8500' foreground='#94a3b8'>Stop and terminate all running Sayri background daemons, gateways, and processes.</span>")
        sys_desc.set_halign(Gtk.Align.START)
        sys_desc.set_wrap(True)
        sys_box.append(sys_desc)

        kill_btn = Gtk.Button(label="Terminate All Sayri Processes")
        kill_btn.add_css_class("sayri-action-btn")
        kill_btn.set_margin_top(4)

        def _kill_all_procs(_b):
            try:
                subprocess.run(["pkill", "-9", "-f", "gateway.py"], capture_output=True)
                subprocess.run(["pkill", "-9", "-f", "sayri.indicator"], capture_output=True)
                subprocess.run(["pkill", "-9", "-f", "python3 -m sayri"], capture_output=True)
            except Exception:
                pass
            os._exit(0)

        kill_btn.connect("clicked", _kill_all_procs)
        sys_box.append(kill_btn)
        self.settings_box.append(sys_box)

    # ── Generic Dynamic In-Card Subviews (Zero Popups API) ──
    def open_subview(self, title: str, build_content_cb, on_back_tab: str = "plugins") -> None:
        """Generic in-card drawer/sub-view for plugins, skills, and extensions."""
        self.subview_title_lbl.set_markup(f"<span weight='700' size='10500' foreground='#f8fafc'>{GLib.markup_escape_text(title.upper())}</span>")

        # Disconnect old handler if present
        if hasattr(self, "_subview_back_conn") and self._subview_back_conn:
            try:
                self.subview_back_btn.disconnect(self._subview_back_conn)
            except Exception:
                pass
        self._subview_back_conn = self.subview_back_btn.connect("clicked", lambda _b: self.switch_tab(on_back_tab))

        # Clear previous content
        while True:
            child = self.subview_body.get_first_child()
            if not child:
                break
            self.subview_body.remove(child)

        build_content_cb(self.subview_body)
        self.switch_tab("subview")

    def show_skill_details_view(self, skill_name: str) -> None:
        def _builder(box: Gtk.Box):
            from sayri import skills as skills_mod
            md_content = skills_mod.read_skill(skill_name) or "No documentation found for this skill."
            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroll.set_propagate_natural_height(True)
            scroll.set_max_content_height(240)
            lbl = Gtk.Label()
            lbl.set_markup(f"<tt>{GLib.markup_escape_text(md_content)}</tt>")
            lbl.set_wrap(True)
            lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            lbl.set_selectable(True)
            lbl.set_halign(Gtk.Align.START)
            scroll.set_child(lbl)
            box.append(scroll)
        self.open_subview(f"Skill: {skill_name}", _builder, on_back_tab="skills")

    def show_install_skill_view(self) -> None:
        def _builder(box: Gtk.Box):
            lbl = Gtk.Label(label="Package name (e.g. sayri-skill-web-search):")
            lbl.set_halign(Gtk.Align.START)
            box.append(lbl)

            entry = Gtk.Entry()
            entry.set_placeholder_text("sayri-package-name…")
            entry.add_css_class("sayri-settings-entry")
            box.append(entry)

            status_lbl = Gtk.Label()
            status_lbl.set_halign(Gtk.Align.START)
            box.append(status_lbl)

            btn = Gtk.Button(label="Download & Install")
            btn.add_css_class("sayri-action-btn")
            btn.add_css_class("primary")

            def _do_install(_):
                val = entry.get_text().strip()
                if not val:
                    return
                btn.set_sensitive(False)
                status_lbl.set_markup("<span foreground='#38bdf8' size='9000'>Fetching &amp; installing from Store…</span>")
                def _thread():
                    from sayri import skills as skills_mod
                    ok = skills_mod.install_skill(val)
                    GLib.idle_add(lambda: (
                        self._populate_skills(),
                        self._populate_plugins(),
                        self.switch_tab("skills") if ok else status_lbl.set_markup("<span foreground='#ef4444' size='9000'>Failed to install package.</span>"),
                        btn.set_sensitive(True)
                    ))
                threading.Thread(target=_thread, daemon=True).start()

            btn.connect("clicked", _do_install)
            box.append(btn)
        self.open_subview("Install from Store", _builder, on_back_tab="skills")

    def show_create_gateway_view(self) -> None:
        def _builder(box: Gtk.Box):
            from sayri.gateway_supervisor import gateway_supervisor
            from sayri.domain.agent_creator import AgentCreator

            installed_plugins = gateway_supervisor.list_installed_plugins()
            agents = AgentCreator.list_agents()

            if not installed_plugins:
                lbl = Gtk.Label(label="No gateway plugins found. Please install Telegram or Discord gateway from the Pulsar Store.")
                lbl.set_wrap(True)
                box.append(lbl)
                return

            # 1. Platform Plugin selection
            p_lbl = Gtk.Label(label="Platform / Gateway Plugin:")
            p_lbl.set_halign(Gtk.Align.START)
            box.append(p_lbl)

            plugin_labels = [f"{p['name']} ({p['id']})" for p in installed_plugins]
            plugin_model = Gtk.StringList.new(plugin_labels)
            plugin_drop = Gtk.DropDown.new(plugin_model, None)
            box.append(plugin_drop)

            # 2. Instance Name
            n_lbl = Gtk.Label(label="Gateway Instance Name:")
            n_lbl.set_halign(Gtk.Align.START)
            box.append(n_lbl)

            name_entry = Gtk.Entry()
            name_entry.set_placeholder_text("e.g. My Bot Gateway")
            name_entry.add_css_class("sayri-settings-entry")
            name_entry.set_text(installed_plugins[0]["name"] if installed_plugins else "Gateway Bot")
            box.append(name_entry)

            # 3. Bound AI Agent
            ag_lbl = Gtk.Label(label="Bound AI Agent (Receiver):")
            ag_lbl.set_halign(Gtk.Align.START)
            box.append(ag_lbl)

            agent_labels = [f"{a.name} ({a.id})" for a in agents]
            agent_model = Gtk.StringList.new(agent_labels)
            agent_drop = Gtk.DropDown.new(agent_model, None)
            box.append(agent_drop)

            # 4. Sandbox Level
            sb_lbl = Gtk.Label(label="Sandbox Security Level:")
            sb_lbl.set_halign(Gtk.Align.START)
            box.append(sb_lbl)

            sb_levels = [
                "LEVEL_1_READONLY (Read-Only Host + Temp)",
                "LEVEL_0_NO_EXEC (Pure Conversation / No Exec)",
                "LEVEL_2_ISOLATED_DEV (Read-Only Root + Workspace)",
                "LEVEL_3_HOST_USER (Full User Host Execution)",
            ]
            sb_keys = [
                "LEVEL_1_READONLY",
                "LEVEL_0_NO_EXEC",
                "LEVEL_2_ISOLATED_DEV",
                "LEVEL_3_HOST_USER",
            ]
            sb_model = Gtk.StringList.new(sb_levels)
            sb_drop = Gtk.DropDown.new(sb_model, None)
            box.append(sb_drop)

            # 5. Bot Token / Secret
            tok_lbl = Gtk.Label(label="Bot Token / API Secret:")
            tok_lbl.set_halign(Gtk.Align.START)
            box.append(tok_lbl)

            tok_entry = Gtk.Entry()
            tok_entry.set_visibility(False)
            tok_entry.set_placeholder_text("Paste required bot token or API key…")
            # 6. Session Continuity & Standby Inactivity Timeout
            resume_check = Gtk.CheckButton(label="🔄 Allow resuming / continuing previous conversations (/resume)")
            resume_check.set_active(True)
            resume_check.set_margin_top(4)
            box.append(resume_check)

            timeout_lbl = Gtk.Label(label="End conversation after inactivity (Standby Timeout):")
            timeout_lbl.set_halign(Gtk.Align.START)
            timeout_lbl.set_margin_top(4)
            box.append(timeout_lbl)

            timeout_options = ["15 minutes", "30 minutes (Default)", "60 minutes (1 hour)", "120 minutes (2 hours)"]
            timeout_values = [15, 30, 60, 120]
            timeout_model = Gtk.StringList.new(timeout_options)
            timeout_drop = Gtk.DropDown.new(timeout_model, None)
            timeout_drop.set_selected(1)  # Default 30 min
            box.append(timeout_drop)

            def _on_plugin_selected(_drop, _param):
                idx = plugin_drop.get_selected()
                if 0 <= idx < len(installed_plugins):
                    sel = installed_plugins[idx]
                    name_entry.set_text(sel["name"])
                    req_sec = ", ".join(sel.get("required_secrets", [])) or "API Secret"
                    tok_lbl.set_label(f"Token / Secret ({req_sec}):")
                    tok_entry.set_placeholder_text(f"Paste token for {sel['name']}…")

            plugin_drop.connect("notify::selected", _on_plugin_selected)

            # Create Button
            create_btn = Gtk.Button(label="Create Gateway Instance")
            create_btn.add_css_class("sayri-action-btn")
            create_btn.add_css_class("primary")
            create_btn.set_margin_top(6)

            def _create(_b):
                p_idx = plugin_drop.get_selected()
                ag_idx = agent_drop.get_selected()
                sb_idx = sb_drop.get_selected()
                t_idx = timeout_drop.get_selected()

                selected_plugin = installed_plugins[p_idx]
                selected_agent = agents[ag_idx]
                selected_sb = sb_keys[sb_idx]
                selected_timeout = timeout_values[t_idx] if 0 <= t_idx < len(timeout_values) else 30

                import re, time
                raw_name = name_entry.get_text().strip() or f"{selected_plugin['name']} ({selected_agent.name})"
                inst_slug = re.sub(r'[^a-zA-Z0-9_-]', '-', raw_name.lower()).strip('-')
                inst_id = f"{selected_plugin['id'][:12]}-{inst_slug[:16]}-{int(time.time()) % 10000}"
                sec_key = f"TOKEN_{inst_id.upper().replace('-', '_')}"

                # Save token if provided
                token_val = tok_entry.get_text().strip()
                if token_val:
                    secrets_manager.set_secret(sec_key, token_val, f"Token for {raw_name}")
                else:
                    sec_key = selected_plugin["required_secrets"][0] if selected_plugin["required_secrets"] else sec_key

                inst_data = {
                    "id": inst_id,
                    "name": raw_name,
                    "plugin_id": selected_plugin["id"],
                    "agent_id": selected_agent.id,
                    "sandbox_level": selected_sb,
                    "secret_key": sec_key,
                    "auth_mode": selected_plugin.get("auth_mode", "pairing_otp"),
                    "allow_resume_previous": resume_check.get_active(),
                    "inactivity_timeout_minutes": selected_timeout,
                    "enabled": True,
                    "created_at": time.time(),
                }
                gateway_supervisor.save_instance(inst_data)
                if token_val:
                    gateway_supervisor.start_instance(inst_id)
                self._populate_plugins()
                self.switch_tab("plugins")

            create_btn.connect("clicked", _create)
            box.append(create_btn)

        self.open_subview("Add Gateway Instance", _builder, on_back_tab="plugins")

    def show_edit_gateway_view(self, instance_data: dict) -> None:
        def _builder(box: Gtk.Box):
            from sayri.gateway_supervisor import gateway_supervisor
            from sayri.domain.agent_creator import AgentCreator

            agents = AgentCreator.list_agents()
            inst_id = instance_data["id"]

            # 1. Instance Name
            n_lbl = Gtk.Label(label="Instance Name:")
            n_lbl.set_halign(Gtk.Align.START)
            box.append(n_lbl)

            name_entry = Gtk.Entry()
            name_entry.set_text(instance_data.get("name", inst_id))
            name_entry.add_css_class("sayri-settings-entry")
            box.append(name_entry)

            # 2. Bound AI Agent
            ag_lbl = Gtk.Label(label="Bound AI Agent (Receiver):")
            ag_lbl.set_halign(Gtk.Align.START)
            box.append(ag_lbl)

            agent_labels = [f"{a.name} ({a.id})" for a in agents]
            agent_model = Gtk.StringList.new(agent_labels)
            agent_drop = Gtk.DropDown.new(agent_model, None)
            cur_ag_id = instance_data.get("agent_id", "default")
            cur_ag_idx = next((i for i, a in enumerate(agents) if a.id == cur_ag_id), 0)
            agent_drop.set_selected(cur_ag_idx)
            box.append(agent_drop)

            # 3. Sandbox Level
            sb_lbl = Gtk.Label(label="Sandbox Security Level:")
            sb_lbl.set_halign(Gtk.Align.START)
            box.append(sb_lbl)

            sb_levels = [
                "LEVEL_1_READONLY (Read-Only Host + Temp)",
                "LEVEL_0_NO_EXEC (Pure Conversation / No Exec)",
                "LEVEL_2_ISOLATED_DEV (Read-Only Root + Workspace)",
                "LEVEL_3_HOST_USER (Full User Host Execution)",
            ]
            sb_keys = [
                "LEVEL_1_READONLY",
                "LEVEL_0_NO_EXEC",
                "LEVEL_2_ISOLATED_DEV",
                "LEVEL_3_HOST_USER",
            ]
            sb_model = Gtk.StringList.new(sb_levels)
            sb_drop = Gtk.DropDown.new(sb_model, None)
            cur_sb = instance_data.get("sandbox_level", "LEVEL_1_READONLY")
            cur_sb_idx = next((i for i, k in enumerate(sb_keys) if k == cur_sb), 0)
            sb_drop.set_selected(cur_sb_idx)
            box.append(sb_drop)

            # 4. Token
            sec_key = instance_data.get("secret_key") or f"TOKEN_{inst_id.upper().replace('-', '_')}"
            cur_tok = secrets_manager.get_secret(sec_key) or ""
            tok_lbl = Gtk.Label(label=f"Bot Token ({sec_key}):")
            tok_lbl.set_halign(Gtk.Align.START)
            box.append(tok_lbl)

            tok_entry = Gtk.Entry()
            tok_entry.set_visibility(False)
            tok_entry.set_text(cur_tok)
            tok_entry.set_placeholder_text("Enter or update bot token…")
            tok_entry.add_css_class("sayri-settings-entry")
            box.append(tok_entry)

            # 5. Channel Guests Access (Kill Switch)
            guests_check = Gtk.CheckButton(label="👥 Allow channel members to interact (Guest Access)")
            guests_val = instance_data.get("allow_channel_guests", False)
            auth_f = Path.home() / ".config" / "sayri" / f"authorizations_{inst_id}.json"
            if auth_f.is_file():
                try:
                    auth_json_d = json.loads(auth_f.read_text(encoding="utf-8"))
                    if "allow_channel_guests" in auth_json_d:
                        guests_val = bool(auth_json_d["allow_channel_guests"])
                except Exception:
                    pass
            guests_check.set_active(guests_val)
            guests_check.set_margin_top(4)
            box.append(guests_check)

            # 6. Session Continuity & Standby Inactivity Timeout
            resume_check = Gtk.CheckButton(label="🔄 Allow resuming / continuing previous conversations (/resume)")
            resume_check.set_active(instance_data.get("allow_resume_previous", True))
            resume_check.set_margin_top(4)
            box.append(resume_check)

            timeout_lbl = Gtk.Label(label="End conversation after inactivity (Standby Timeout):")
            timeout_lbl.set_halign(Gtk.Align.START)
            timeout_lbl.set_margin_top(4)
            box.append(timeout_lbl)

            timeout_options = ["15 minutes", "30 minutes (Default)", "60 minutes (1 hour)", "120 minutes (2 hours)"]
            timeout_values = [15, 30, 60, 120]
            cur_timeout = instance_data.get("inactivity_timeout_minutes", 30)
            cur_t_idx = timeout_values.index(cur_timeout) if cur_timeout in timeout_values else 1

            timeout_model = Gtk.StringList.new(timeout_options)
            timeout_drop = Gtk.DropDown.new(timeout_model, None)
            timeout_drop.set_selected(cur_t_idx)
            box.append(timeout_drop)

            # Save Button
            save_btn = Gtk.Button(label="Save Configuration")
            save_btn.add_css_class("sayri-action-btn")
            save_btn.add_css_class("primary")
            save_btn.set_margin_top(6)

            def _save(_b):
                ag_idx = agent_drop.get_selected()
                sb_idx = sb_drop.get_selected()
                t_idx = timeout_drop.get_selected()

                selected_agent = agents[ag_idx]
                selected_sb = sb_keys[sb_idx]
                selected_timeout = timeout_values[t_idx] if 0 <= t_idx < len(timeout_values) else 30
                new_tok = tok_entry.get_text().strip()

                if new_tok:
                    secrets_manager.set_secret(sec_key, new_tok, f"Token for {instance_data.get('name')}")

                instance_data["name"] = name_entry.get_text().strip() or instance_data.get("name")
                instance_data["agent_id"] = selected_agent.id
                instance_data["sandbox_level"] = selected_sb
                instance_data["secret_key"] = sec_key
                instance_data["allow_channel_guests"] = guests_check.get_active()
                instance_data["allow_resume_previous"] = resume_check.get_active()
                instance_data["inactivity_timeout_minutes"] = selected_timeout
                gateway_supervisor.save_instance(instance_data)

                # Sync to authorizations file
                if auth_f.is_file():
                    try:
                        auth_json_d = json.loads(auth_f.read_text(encoding="utf-8"))
                        auth_json_d["allow_channel_guests"] = guests_check.get_active()
                        auth_f.write_text(json.dumps(auth_json_d, indent=2), encoding="utf-8")
                    except Exception:
                        pass

                # Restart instance if already running
                if gateway_supervisor.is_instance_running(inst_id):
                    gateway_supervisor.start_instance(inst_id)

                self._populate_plugins()
                self.switch_tab("plugins")

            save_btn.connect("clicked", _save)
            box.append(save_btn)

        self.open_subview(f"Edit {instance_data.get('name')}", _builder, on_back_tab="plugins")

    def show_generic_otp_pairing_view(self, plugin_meta: dict, instance_id: str = "default") -> None:
        def _builder(box: Gtk.Box):
            pin_file = Path.home() / ".config" / "sayri" / f"pairing_pin_{instance_id}.json"
            if not pin_file.is_file() and (Path.home() / ".config" / "sayri" / "pairing_pin.json").is_file():
                pin_file = Path.home() / ".config" / "sayri" / "pairing_pin.json"

            raw_pin = None
            if pin_file.is_file():
                try:
                    data = json.loads(pin_file.read_text(encoding="utf-8"))
                    saved_pin = str(data.get("pin", "")).strip()
                    expires_at = data.get("expires_at", 0)
                    if saved_pin and time.time() <= expires_at:
                        raw_pin = saved_pin
                except Exception:
                    pass

            if not raw_pin:
                import random
                raw_pin = f"{random.randint(100000, 999999)}"
                try:
                    pin_file.parent.mkdir(parents=True, exist_ok=True)
                    pin_file.write_text(json.dumps({
                        "pin": raw_pin,
                        "created_at": time.time(),
                        "expires_at": time.time() + 86400
                    }, indent=2), encoding="utf-8")
                except Exception as e:
                    print(f"[Cajita] Error writing pairing pin file: {e}")

            formatted_pin = f"{raw_pin[:3]} {raw_pin[3:]}"
            pair_cmd = f"/pair {raw_pin}"

            pin_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            pin_card.add_css_class("sayri-card-item")
            pin_lbl = Gtk.Label()
            pin_lbl.set_markup(f"<span font_family='monospace' weight='800' size='22000' foreground='#38bdf8'>{formatted_pin}</span>")
            pin_lbl.set_halign(Gtk.Align.CENTER)
            pin_card.append(pin_lbl)
            box.append(pin_card)

            cmd_entry = Gtk.Entry()
            cmd_entry.set_text(pair_cmd)
            cmd_entry.set_editable(False)
            cmd_entry.add_css_class("sayri-settings-entry")
            box.append(cmd_entry)

            copy_btn = Gtk.Button(label=f"📋 Copy Command ({pair_cmd})")
            copy_btn.add_css_class("sayri-action-btn")
            copy_btn.add_css_class("primary")

            def _copy(_b):
                try:
                    subprocess.Popen(["wl-copy", pair_cmd])
                except Exception:
                    pass
                try:
                    cb = Gdk.Display.get_default().get_clipboard()
                    cb.set(pair_cmd)
                except Exception:
                    pass
                copy_btn.set_label("✓ Copied to Clipboard!")
                copy_btn.remove_css_class("primary")

            copy_btn.connect("clicked", _copy)
            box.append(copy_btn)

            chat_url = plugin_meta.get("ui", {}).get("chat_url") or plugin_meta.get("chat_url", "")
            if chat_url:
                open_btn = Gtk.Button(label="💬 Open Channel ↗")
                open_btn.add_css_class("sayri-action-btn")
                open_btn.connect("clicked", lambda _: os.system(f"xdg-open {chat_url} &"))
                box.append(open_btn)

            hint_text = plugin_meta.get("ui", {}).get("sync_instructions") or plugin_meta.get("sync_instructions") or "Send this command to authorize your account."
            hint_lbl = Gtk.Label()
            hint_lbl.set_markup(f"<span size='8500' foreground='#94a3b8'>{GLib.markup_escape_text(hint_text)}</span>")
            hint_lbl.set_halign(Gtk.Align.CENTER)
            hint_lbl.set_wrap(True)
            box.append(hint_lbl)

        p_name = plugin_meta.get("name", "Gateway")
        self.open_subview(f"{p_name} Pairing ({instance_id})", _builder, on_back_tab="plugins")

    def show_generic_secret_config_view(self, plugin_id: str, plugin_name: str, secret_key: str) -> None:
        def _builder(box: Gtk.Box):
            info = Gtk.Label()
            info.set_markup(f"<span size='8500' foreground='#94a3b8'>Set zero-plaintext secret <tt>${GLib.markup_escape_text(secret_key)}</tt>:</span>")
            info.set_halign(Gtk.Align.START)
            box.append(info)

            val_entry = Gtk.Entry()
            val_entry.set_visibility(False)
            val_entry.set_placeholder_text(f"Enter {secret_key} token…")
            val_entry.add_css_class("sayri-settings-entry")
            box.append(val_entry)

            save_btn = Gtk.Button(label="Save in Zero-Plaintext Vault")
            save_btn.add_css_class("sayri-action-btn")
            save_btn.add_css_class("primary")

            def _save(_b):
                val = val_entry.get_text().strip()
                if val:
                    secrets_manager.set_secret(secret_key, val, f"Token for {plugin_id}")
                    try:
                        from sayri.gateway_supervisor import gateway_supervisor
                        gateway_supervisor.start_gateway(plugin_id)
                    except Exception as e:
                        print(f"[Cajita] Error starting gateway {plugin_id}: {e}")
                    self._populate_secrets()
                    self._populate_plugins()
                self.switch_tab("plugins")

            save_btn.connect("clicked", _save)
            box.append(save_btn)

        self.open_subview(f"Configure {plugin_name}", _builder, on_back_tab="plugins")

    def show_add_secret_view(self) -> None:
        def _builder(box: Gtk.Box):
            lbl_k = Gtk.Label(label="Key Identifier (e.g. API_KEY):")
            lbl_k.set_halign(Gtk.Align.START)
            box.append(lbl_k)

            sec_k_entry = Gtk.Entry()
            sec_k_entry.set_placeholder_text("API_KEY…")
            sec_k_entry.add_css_class("sayri-settings-entry")
            box.append(sec_k_entry)

            lbl_v = Gtk.Label(label="Secret Value / API Token:")
            lbl_v.set_halign(Gtk.Align.START)
            box.append(lbl_v)

            sec_v_entry = Gtk.Entry()
            sec_v_entry.set_visibility(False)
            sec_v_entry.set_placeholder_text("Paste token…")
            sec_v_entry.add_css_class("sayri-settings-entry")
            box.append(sec_v_entry)

            lbl_d = Gtk.Label(label="Description (optional):")
            lbl_d.set_halign(Gtk.Align.START)
            box.append(lbl_d)

            sec_d_entry = Gtk.Entry()
            sec_d_entry.set_placeholder_text("Description…")
            sec_d_entry.add_css_class("sayri-settings-entry")
            box.append(sec_d_entry)

            save_btn = Gtk.Button(label="Save in Zero-Plaintext Vault")
            save_btn.add_css_class("sayri-action-btn")
            save_btn.add_css_class("primary")

            def _save(_):
                k = sec_k_entry.get_text().strip()
                v = sec_v_entry.get_text().strip()
                d = sec_d_entry.get_text().strip()
                if k and v:
                    secrets_manager.set_secret(k, v, d)
                    self._populate_secrets()
                    self.switch_tab("secrets")

            save_btn.connect("clicked", _save)
            box.append(save_btn)

        self.open_subview("Add Vault Secret", _builder, on_back_tab="secrets")

    def _prompt_create_agent(self) -> None:
        dialog = Gtk.Window(title="Create Subagent")
        dialog.set_default_size(360, 320)
        dialog.set_modal(True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(14)
        box.set_margin_end(14)

        # Name
        lbl = Gtk.Label(label="Subagent Name:")
        lbl.set_halign(Gtk.Align.START)
        box.append(lbl)
        name_entry = Gtk.Entry()
        name_entry.add_css_class("sayri-settings-entry")
        name_entry.set_placeholder_text("e.g. Code Reviewer, Research Assistant")
        box.append(name_entry)

        # Role
        lbl2 = Gtk.Label(label="Instructions / System Prompt:")
        lbl2.set_halign(Gtk.Align.START)
        box.append(lbl2)
        prompt_entry = Gtk.Entry()
        prompt_entry.add_css_class("sayri-settings-entry")
        prompt_entry.set_placeholder_text("e.g. You are a senior software engineer assisting with code analysis.")
        box.append(prompt_entry)

        # Sandbox DropDown & Dynamic Security Indicator
        lbl_sb = Gtk.Label(label="Sandbox Security Policy:")
        lbl_sb.set_halign(Gtk.Align.START)
        box.append(lbl_sb)

        sb_options = [
            ("LEVEL_0_NO_EXEC", "LEVEL_0: Pure Chat (No Execution / Maximum Safety)"),
            ("LEVEL_1_READONLY", "LEVEL_1: Sandbox L1 (Read-Only FS / Ephemeral Tmp)"),
            ("LEVEL_2_ISOLATED_DEV", "LEVEL_2: Sandbox L2 (Isolated Workspace)"),
            ("LEVEL_3_HOST_USER", "LEVEL_3: Host L3 (Active Desktop User)"),
            ("LEVEL_4_HOST_ROOT", "LEVEL_4: Host L4 (Elevated / Root)"),
        ]
        sb_model = Gtk.StringList.new([opt[1] for opt in sb_options])
        sb_drop = Gtk.DropDown.new(sb_model, None)
        sb_drop.set_selected(0)
        box.append(sb_drop)

        # Dynamic Sandbox Feedback Banner
        sb_banner = Gtk.Label()
        sb_banner.add_css_class("sayri-info-banner")
        sb_banner.set_wrap(True)
        sb_banner.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        box.append(sb_banner)

        def _update_sb_banner(*_):
            idx = sb_drop.get_selected()
            lvl = sb_options[idx][0]
            if lvl == "LEVEL_0_NO_EXEC":
                sb_banner.set_markup("<span foreground='#22c55e'>🔒 <b>Zero-Execution Sandbox:</b> Pure conversational assistant. All plugin execution and bash commands are strictly blocked to eliminate escape risks.</span>")
            elif lvl == "LEVEL_1_READONLY":
                sb_banner.set_markup("<span foreground='#38bdf8'>🛡️ <b>Read-Only Sandbox:</b> File system is read-only. Plugins cannot modify user files or launch host desktop apps.</span>")
            elif lvl == "LEVEL_2_ISOLATED_DEV":
                sb_banner.set_markup("<span foreground='#38bdf8'>📦 <b>Isolated Workspace:</b> Agent can only create files inside its designated workspace directory.</span>")
            else:
                sb_banner.set_markup("<span foreground='#f59e0b'>⚠️ <b>Host Execution:</b> Agent has access to the user's host environment.</span>")

        sb_drop.connect("notify::selected", _update_sb_banner)
        _update_sb_banner()

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_box.set_halign(Gtk.Align.END)
        btn_box.set_margin_top(8)

        cancel_b = Gtk.Button(label="Cancel")
        cancel_b.add_css_class("sayri-action-btn")
        cancel_b.connect("clicked", lambda _: dialog.close())
        btn_box.append(cancel_b)

        save_b = Gtk.Button(label="Create Subagent")
        save_b.add_css_class("sayri-action-btn")
        save_b.add_css_class("primary")

        def _save(_):
            n = name_entry.get_text().strip()
            p = prompt_entry.get_text().strip()
            if n:
                sel_sb_idx = sb_drop.get_selected()
                sb_level_enum = getattr(SandboxLevel, sb_options[sel_sb_idx][0], SandboxLevel.LEVEL_0_NO_EXEC)
                aid = re.sub(r"[^a-zA-Z0-9_\-]", "-", n.lower()).strip("-") or f"agent-{int(time.time())}"
                profile = AgentProfile(
                    id=aid,
                    name=n,
                    description=f"Created via Sayri Subagent Manager",
                    system_prompt=p or f"You are {n}, an autonomous assistant for Sayri.",
                    model=AgentModelConfig(model_name="default"),
                    sandbox=SandboxConfig(level=sb_level_enum),
                )
                AgentCreator.save_agent(profile)
                self._populate_agents()
            dialog.close()

        save_b.connect("clicked", _save)
        btn_box.append(save_b)

        box.append(btn_box)
        dialog.set_child(box)
        dialog.present()

    def _prompt_rename_session(self, session_id: str, current_title: str) -> None:
        dialog = Gtk.Window(title="Rename Conversation")
        dialog.set_default_size(320, 120)
        dialog.set_modal(True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(14)
        box.set_margin_end(14)

        lbl = Gtk.Label(label="New Title:")
        lbl.set_halign(Gtk.Align.START)
        box.append(lbl)

        entry = Gtk.Entry()
        entry.add_css_class("sayri-settings-entry")
        entry.set_text(current_title)
        box.append(entry)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_box.set_halign(Gtk.Align.END)
        btn_box.set_margin_top(6)

        cancel_b = Gtk.Button(label="Cancel")
        cancel_b.add_css_class("sayri-action-btn")
        cancel_b.connect("clicked", lambda _: dialog.close())
        btn_box.append(cancel_b)

        save_b = Gtk.Button(label="Save")
        save_b.add_css_class("sayri-action-btn")
        save_b.add_css_class("primary")
        def _save(_):
            new_t = entry.get_text().strip()
            if new_t and hasattr(self.app, "storage") and self.app.storage:
                self.app.storage.update_session_title(session_id, new_t)
                self._populate_history()
            dialog.close()
        save_b.connect("clicked", _save)
        entry.connect("activate", _save)
        btn_box.append(save_b)

        box.append(btn_box)
        dialog.set_child(box)
        dialog.present()

    def render_session_history(self, title: str, messages: list) -> None:
        """Shows historical thread transcript."""
        self.thread_title_lbl.set_markup(f"<span weight='600' size='10000' foreground='#f8fafc'>{GLib.markup_escape_text(title)}</span>")
        while True:
            child = self.thread_box.get_first_child()
            if not child:
                break
            self.thread_box.remove(child)

        for m in messages:
            role = getattr(m, "role", "") if hasattr(m, "role") else m.get("role", "")
            content = getattr(m, "content", "") if hasattr(m, "content") else m.get("content", "")
            if role == "user" and content:
                u_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                u_lbl = Gtk.Label()
                u_lbl.set_markup(f"<span foreground='#38bdf8' weight='600'>You:</span> {GLib.markup_escape_text(content)}")
                u_lbl.set_wrap(True)
                u_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                u_box.append(u_lbl)
                self.thread_box.append(u_box)
            elif role == "assistant" and content:
                a_lbl = Gtk.Label()
                a_lbl.set_wrap(True)
                a_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                _safe_set_markup(a_lbl, content)
                self.thread_box.append(a_lbl)

        self.switch_tab("thread")

    def update_agent_badge(self, agent_name: str, sandbox_level: str) -> None:
        short_sb = {
            "LEVEL_0_NO_EXEC": "No Exec",
            "LEVEL_1_READONLY": "Sandbox L1 (Read-Only)",
            "LEVEL_2_ISOLATED_DEV": "Sandbox L2 (Isolated Dev)",
            "LEVEL_3_HOST_USER": "Host L3 (User)",
            "LEVEL_4_HOST_ROOT": "Host L4 (Elevated)",
        }.get(sandbox_level, sandbox_level)
        self.agent_badge.set_markup(f"<span size='9500' weight='600' foreground='#38bdf8'>{GLib.markup_escape_text(agent_name)}</span>")
        self.sandbox_badge.set_markup(f"<span size='9000' foreground='#94a3b8'>• {GLib.markup_escape_text(short_sb)}</span>")

    def append_token(self, token: str) -> None:
        self._live_text += token
        _safe_set_markup(self.response_label, self._live_text)
        if self.card_stack.get_visible_child_name() != "chat":
            self.switch_tab("chat", trigger_effect=False)
        self.card_overlay.set_visible(True)
        # Settle pill animation and transfer to card
        self.pill_bg.set_mode("idle")
        self.card_bg.set_mode("speaking")

    def set_response(self, text: str) -> None:
        self._live_text = text
        self.entry.set_text("")
        _safe_set_markup(self.response_label, text)
        if self.card_stack.get_visible_child_name() != "chat":
            self.switch_tab("chat", trigger_effect=False)
        self.card_overlay.set_visible(True)
        # Settle pill animation and transfer to card
        self.pill_bg.set_mode("idle")
        self.card_bg.set_mode("speaking")

    def set_command_output(self, cmd: str, output: str, exit_code: int = 0) -> None:
        self.cmd_expander.set_label(f"Command: {cmd[:28]}… (code {exit_code})")
        self.cmd_label.set_text(f"$ {cmd}\n\n{output}")
        self.cmd_expander.set_visible(True)
        self.card_overlay.set_visible(True)

    def set_tool_output(self, cmd: str, output: str) -> None:
        self.set_command_output(cmd, output, exit_code=0)

    def set_busy(self, busy: bool) -> None:
        if busy:
            self.entry.set_text("")
            self.pill_bg.set_mode("thinking")
            self.card_bg.set_mode("idle")
            self.switch_tab("chat", trigger_effect=False)
        else:
            self.entry.set_sensitive(True)
            self.pill_bg.set_mode("idle")

    def set_speaking(self, speaking: bool) -> None:
        if speaking:
            self.card_bg.set_mode("speaking")
        else:
            self.card_bg.set_mode("idle")

    def set_mic(self, active: bool) -> None:
        self._mic_is_active = active
        if active:
            self.mic_btn.set_child(_svg_icon(SVG_MIC_ACTIVE))
            self.pill_bg.set_mode("active")
        else:
            self.mic_btn.set_child(_svg_icon(SVG_MIC))
            if not self.entry.get_text().strip() and self.pill_bg.mode == "active":
                self.pill_bg.set_mode("idle")

    def set_content(self, kind: str, text: str) -> None:
        if kind in ("transcription", "user", "partial"):
            self.entry.set_text(text)
            self.entry.set_position(-1)
            if text:
                self.pill_bg.set_mode("active")
        elif kind == "hint":
            if not self._live_text:
                self.response_label.set_markup(f"<span foreground='#94a3b8'><i>{GLib.markup_escape_text(text)}</i></span>")
                self.card_overlay.set_visible(True)
        elif kind == "error":
            self.response_label.set_markup(f"<span foreground='#ef4444' size='10500'><b>Error:</b> {GLib.markup_escape_text(text)}</span>")
            self.card_overlay.set_visible(True)
        elif kind in ("assistant", "answer"):
            self.set_response(text)

    def clear(self) -> None:
        self._live_text = ""
        self.response_label.set_attributes(Pango.AttrList())
        self.response_label.set_text("")
        self.cmd_label.set_text("")
        self.cmd_expander.set_visible(False)
        self.card_bg.set_mode("idle")


Cajita = SayriCajita
