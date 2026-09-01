"""Sayri overlay: single transparent window containing the WebKit Siri Orb
and animated Chroma-Ring Cajita, pinned to the TOP-RIGHT corner of the monitor.
"""

import json
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("WebKit", "6.0")
from gi.repository import Gdk, GLib, Gtk, WebKit  # noqa: E402

from .cajita import SayriCajita
from .webkit import pin_window, webview_setup, SCHEME, ensure_scheme

BUBBLE_WIDTH = 440
BUBBLE_HEIGHT = 580
GAP = 22
MARGIN = 16
TOP_MARGIN = 44


class SayriOverlay:
    """Single transparent window with both the native orb and the animated chroma cajita."""

    def __init__(self, app) -> None:
        self.app = app
        self.cfg = app.cfg

        orb_size = self.cfg.get_int("ui", "orb_size")
        if orb_size < 100 or orb_size > 300:
            orb_size = 140

        width = BUBBLE_WIDTH + GAP + orb_size + 24
        height = BUBBLE_HEIGHT

        # ── window ──────────────────────────────────────────────────
        self.win = Gtk.ApplicationWindow(application=app)
        self.win.set_default_size(width, height)
        self.win.set_resizable(False)
        self.win.set_decorated(False)
        self.win.add_css_class("sayri-overlay")

        try:
            css = Gtk.CssProvider()
            css.load_from_data(
                b"window.sayri-overlay, "
                b"window.sayri-overlay.background, "
                b"window.sayri-overlay.csd, "
                b"window.sayri-overlay decoration, "
                b"window.sayri-overlay > contents, "
                b"window.sayri-overlay > box, "
                b".sayri-overlay, "
                b".sayri-overlay.background { "
                b"    background: none; "
                b"    background-color: transparent; "
                b"    background-image: none; "
                b"    border: none; "
                b"    box-shadow: none; "
                b"    outline: none; "
                b"}"
            )
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), css,
                Gtk.STYLE_PROVIDER_PRIORITY_USER)
        except Exception:  # noqa: BLE001
            pass

        self.win.connect("close-request", lambda *_: (self.win.set_visible(False), True)[-1])

        # ── pin to top-right (always on top, fixed) ───────────────────
        pin_window(self.win, top_margin=TOP_MARGIN, right_margin=MARGIN,
                   width=width, height=height)

        # ── layout: horizontal box [ cajita · orb ] ─────────────────
        self.hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=GAP)
        self.hbox.set_halign(Gtk.Align.END)
        self.hbox.set_valign(Gtk.Align.START)
        self.hbox.set_hexpand(False)
        self.hbox.set_vexpand(False)

        # 1. Cajita with Chroma-Ring animated border
        self.cajita = SayriCajita(app)
        self.cajita.set_valign(Gtk.Align.START)
        self.cajita.set_hexpand(False)
        self.hbox.append(self.cajita)

        # 2. WebKit-based animated Siri Orb (React Native/Skia)
        self._orb_size = orb_size
        self._orb_ready = False
        self._orb_pending: list[tuple[str, dict]] = []
        self.web = WebKit.WebView.new()
        ensure_scheme(self.web.get_context())
        ucm = webview_setup(self.web, on_message=self._on_orb_message)
        # Wrap WebKit in a fixed-size container (WebKit ignores set_size_request)
        self._orb_box = Gtk.Box()
        self._orb_box.set_size_request(orb_size, orb_size)
        self._orb_box.set_halign(Gtk.Align.END)
        self._orb_box.set_valign(Gtk.Align.START)
        self._orb_box.set_hexpand(False)
        self._orb_box.set_vexpand(False)
        self._orb_box.append(self.web)
        self.web.set_hexpand(True)
        self.web.set_vexpand(True)
        self.web.load_uri(f"{SCHEME}://app/index.html?mode=orb")
        self.hbox.append(self._orb_box)

        self.win.set_child(self.hbox)

    def _on_orb_message(self, text: str) -> None:
        try:
            msg = json.loads(text)
        except Exception:  # noqa: BLE001
            return
        if msg.get("type") == "ready":
            self._orb_ready = True
            for s, o in self._orb_pending:
                self._orb_set_state(s, o)
            self._orb_pending = []
        elif msg.get("type") == "click":
            self.app.on_orb_click()

    def _orb_set_state(self, state: str, opts: dict | None = None) -> None:
        opts = opts or {}
        if not self._orb_ready:
            self._orb_pending.append((state, opts))
            return
        js = (f"window.sayriBridge && window.sayriBridge.setState("
              f"{json.dumps(state)}, {json.dumps(opts)})")
        self.web.evaluate_javascript(js, len(js), None, f"{SCHEME}://app", None, None, None)

    def set_state_sync(self, state: str, _opts: dict | None = None) -> None:
        self._orb_set_state(state, {"size": self._orb_size})
        if state == "speaking":
            self.cajita.set_speaking(True)
        else:
            self.cajita.set_speaking(False)
        if state in ("listening", "activated"):
            self.cajita.pill_bg.set_mode("active")
        elif state == "thinking":
            self.cajita.pill_bg.set_mode("rotating")
        elif state == "idle":
            if not self.cajita.entry.get_text().strip():
                self.cajita.pill_bg.set_mode("idle")

    def set_audio_level(self, level: float) -> None:
        if not self._orb_ready:
            return
        lvl = max(0.0, min(1.0, float(level)))
        js = f"window.sayriBridge && window.sayriBridge.setAudioLevel && window.sayriBridge.setAudioLevel({lvl:.3f})"
        self.web.evaluate_javascript(js, len(js), None, f"{SCHEME}://app", None, None, None)

    def set_content(self, kind: str, text: str) -> None:
        self.cajita.set_content(kind, text)

    def set_mic(self, active: bool) -> None:
        self.cajita.set_mic(active)

    def set_busy(self, busy: bool) -> None:
        self.cajita.set_busy(busy)

    def clear(self) -> None:
        self.cajita.clear()

    @property
    def is_visible(self) -> bool:
        return getattr(self, "_is_visible", False) or bool(self.win.get_visible())

    def reassert_position(self) -> bool:
        fn = getattr(self.win, "_reassert_x11_position", None)
        if fn:
            fn()
        return GLib.SOURCE_REMOVE

    def show(self) -> None:
        self._is_visible = True
        self._just_shown = time.monotonic()
        self._was_active = False
        self._orb_set_state("idle", {"size": self._orb_size})
        self.win.set_visible(True)
        try:
            self.win.present_with_time(0)
        except Exception:
            self.win.present()
        self.reassert_position()
        GLib.timeout_add(50, self.reassert_position)
        GLib.timeout_add(150, self.reassert_position)
        def _focus():
            self.cajita.entry.grab_focus()
            self.cajita.entry.set_position(-1)
        GLib.idle_add(_focus)
        if hasattr(self, "app") and self.app and hasattr(self.app, "on_shown"):
            self.app.on_shown()

    def hide(self) -> None:
        self._is_visible = False
        self._was_active = False
        self.win.set_visible(False)
        if hasattr(self, "app") and self.app and hasattr(self.app, "on_hidden"):
            self.app.on_hidden()

    def toggle(self) -> None:
        if self.is_visible:
            self.hide()
        else:
            self.show()

    def apply_config(self) -> None:
        orb_size = self.cfg.get_int("ui", "orb_size")
        if orb_size < 100 or orb_size > 300:
            orb_size = 140
        self._orb_size = orb_size
        width = BUBBLE_WIDTH + GAP + orb_size
        height = max(BUBBLE_HEIGHT, orb_size)
        self._orb_box.set_size_request(orb_size, orb_size)
        self.win.set_default_size(width, height)
        pin_window(self.win, top_margin=TOP_MARGIN, right_margin=MARGIN,
                   width=width, height=height)
        self.reassert_position()
