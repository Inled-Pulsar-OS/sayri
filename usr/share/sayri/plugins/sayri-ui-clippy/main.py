#!/usr/bin/env python3
"""Sayri Clippy Retro Companion UI Plugin.

A floating desktop character assistant that responds to voice/chat and
renders Sayri responses & XUI declarative UI skeletons in classic speech bubbles.
"""

from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
from typing import Any, Optional

# Ensure sayri lib is available in sys.path
script_dir = os.path.dirname(os.path.abspath(__file__))
sayri_lib = os.path.normpath(os.path.join(script_dir, "..", "..", "lib"))
if os.path.isdir(sayri_lib) and sayri_lib not in sys.path:
    sys.path.insert(0, sayri_lib)

try:
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GLib, Gtk, Pango
    import cairo
except Exception as err:
    print(f"[clippy-ui] GTK3/Cairo not available: {err}", file=sys.stderr)
    sys.exit(1)

from sayri import config as sayri_config
from sayri import ipc, paths, sysinfo


class ClippyAvatar(Gtk.DrawingArea):
    """Draws an animated retro paperclip companion with responsive states."""

    def __init__(self, on_click=None) -> None:
        super().__init__()
        self.set_size_request(80, 100)
        self.on_click = on_click
        self.state = "idle"  # idle | listening | thinking | speaking | alert
        self.blink = False
        self.eye_offset_x = 0.0
        self.eye_offset_y = 0.0
        self.mouth_open = 0.0
        self.angle = 0.0
        self.audio_lvl = 0.0

        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect("button-press-event", self._on_button_press)
        self.connect("draw", self._on_draw)

        # Periodic animation loop
        GLib.timeout_add(60, self._animate_step)
        GLib.timeout_add(3200, self._trigger_blink)

    def _on_button_press(self, widget, event):
        if self.on_click:
            self.on_click(event)
        return True

    def _trigger_blink(self) -> bool:
        self.blink = True
        self.queue_draw()
        GLib.timeout_add(150, self._end_blink)
        return True

    def _end_blink(self) -> bool:
        self.blink = False
        self.queue_draw()
        return False

    def _animate_step(self) -> bool:
        t = time.time()
        if self.state == "listening":
            self.angle = math.sin(t * 8) * 0.12
            self.eye_offset_y = -2.0
            self.eye_offset_x = math.sin(t * 3) * 3.0
            self.mouth_open = 0.2 + self.audio_lvl * 0.6
        elif self.state == "thinking":
            self.angle = math.sin(t * 4) * 0.08
            self.eye_offset_y = -3.0
            self.eye_offset_x = 3.0
            self.mouth_open = 0.0
        elif self.state == "speaking":
            self.angle = math.sin(t * 5) * 0.05
            self.mouth_open = (math.sin(t * 14) + 1.0) * 0.4
            self.eye_offset_x = math.sin(t * 2) * 2.0
            self.eye_offset_y = 0.0
        else:  # idle
            self.angle = math.sin(t * 1.5) * 0.03
            self.eye_offset_x = math.sin(t * 0.8) * 2.0
            self.eye_offset_y = 0.0
            self.mouth_open = 0.0

        self.queue_draw()
        return True

    def set_state(self, state: str, audio_lvl: float = 0.0) -> None:
        self.state = state
        self.audio_lvl = audio_lvl
        self.queue_draw()

    def _on_draw(self, widget, cr: cairo.Context) -> bool:
        w = self.get_allocated_width()
        h = self.get_allocated_height()

        cr.save()
        cr.translate(w / 2, h / 2 + 10)
        cr.rotate(self.angle)

        # Paperclip metallic body
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)

        # Outer Shadow
        cr.set_source_rgba(0, 0, 0, 0.18)
        cr.set_line_width(8)
        self._draw_clip_path(cr, dx=2, dy=3)
        cr.stroke()

        # Base metal wire
        cr.set_source_rgb(0.72, 0.75, 0.78)
        cr.set_line_width(7)
        self._draw_clip_path(cr)
        cr.stroke()

        # Highlight sheen
        cr.set_source_rgb(0.92, 0.94, 0.96)
        cr.set_line_width(3)
        self._draw_clip_path(cr, dx=-0.8, dy=-0.8)
        cr.stroke()

        # Big expressive eyes
        eye_y = -18
        eye_spacing = 9

        for ex in [-eye_spacing, eye_spacing]:
            # White sclera
            cr.set_source_rgb(1.0, 1.0, 1.0)
            cr.arc(ex, eye_y, 7.5, 0, 2 * math.pi)
            cr.fill_preserve()
            cr.set_source_rgb(0.2, 0.2, 0.2)
            cr.set_line_width(1.2)
            cr.stroke()

            # Pupil (or closed eyelid if blinking)
            if self.blink:
                cr.set_source_rgb(0.2, 0.2, 0.2)
                cr.set_line_width(2)
                cr.move_to(ex - 6, eye_y)
                cr.line_to(ex + 6, eye_y)
                cr.stroke()
            else:
                cr.set_source_rgb(0.1, 0.15, 0.2)
                px = ex + self.eye_offset_x
                py = eye_y + self.eye_offset_y
                cr.arc(px, py, 3.8, 0, 2 * math.pi)
                cr.fill()

                # Pupil white catchlight
                cr.set_source_rgb(1.0, 1.0, 1.0)
                cr.arc(px - 1.2, py - 1.2, 1.2, 0, 2 * math.pi)
                cr.fill()

            # Expressive Eyebrows
            cr.set_source_rgb(0.2, 0.2, 0.2)
            cr.set_line_width(1.8)
            if self.state == "listening":
                cr.move_to(ex - 5, eye_y - 11)
                cr.line_to(ex + 5, eye_y - 13)
            elif self.state == "thinking":
                cr.move_to(ex - 5, eye_y - 13 if ex < 0 else eye_y - 9)
                cr.line_to(ex + 5, eye_y - 11 if ex < 0 else eye_y - 12)
            else:
                cr.move_to(ex - 5, eye_y - 11)
                cr.line_to(ex + 5, eye_y - 10)
            cr.stroke()

        # Mouth
        cr.set_source_rgb(0.2, 0.2, 0.2)
        cr.set_line_width(2)
        if self.mouth_open > 0.05:
            cr.arc(0, eye_y + 16, 4 + self.mouth_open * 4, 0.1, math.pi - 0.1)
            cr.stroke()
        else:
            cr.arc(0, eye_y + 15, 6, 0.2 * math.pi, 0.8 * math.pi)
            cr.stroke()

        cr.restore()
        return True

    def _draw_clip_path(self, cr: cairo.Context, dx: float = 0, dy: float = 0) -> None:
        # Standard paperclip loops
        cr.move_to(10 + dx, 25 + dy)
        cr.line_to(10 + dx, -20 + dy)
        cr.arc(-2 + dx, -20 + dy, 12, 0, -math.pi)
        cr.line_to(-14 + dx, 20 + dy)
        cr.arc(2 + dx, 20 + dy, 16, math.pi, 0)
        cr.line_to(18 + dx, -24 + dy)
        cr.arc(2 + dx, -24 + dy, 16, 0, -math.pi)
        cr.line_to(-14 + dx, 10 + dy)


class SpeechBubble(Gtk.Frame):
    """Speech balloon rendering Markdown text and XUI Declarative Skeletons."""

    def __init__(self, on_action=None, on_submit=None) -> None:
        super().__init__()
        self.on_action = on_action
        self.on_submit = on_submit

        self.set_shadow_type(Gtk.ShadowType.NONE)
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.box.set_margin_start(14)
        self.box.set_margin_end(14)
        self.box.set_margin_top(12)
        self.box.set_margin_bottom(12)
        self.add(self.box)

        # Title / Status
        self.header_lbl = Gtk.Label()
        self.header_lbl.set_halign(Gtk.Align.START)
        self.header_lbl.set_markup("<span size='small' weight='bold' foreground='#b8860b'>💡 Sayri Assistant</span>")
        self.box.pack_start(self.header_lbl, False, False, 0)

        # Message text label
        self.msg_lbl = Gtk.Label()
        self.msg_lbl.set_halign(Gtk.Align.START)
        self.msg_lbl.set_line_wrap(True)
        self.msg_lbl.set_max_width_chars(36)
        self.msg_lbl.set_selectable(True)
        self.box.pack_start(self.msg_lbl, False, False, 0)

        # Dynamic XUI container
        self.xui_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.box.pack_start(self.xui_box, True, True, 0)

        # User quick input box
        self.input_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("Escribe una pregunta o petición...")
        self.entry.connect("activate", self._on_entry_activate)
        self.send_btn = Gtk.Button(label="➤")
        self.send_btn.connect("clicked", lambda b: self._on_entry_activate(self.entry))
        self.input_box.pack_start(self.entry, True, True, 0)
        self.input_box.pack_start(self.send_btn, False, False, 0)
        self.box.pack_start(self.input_box, False, False, 0)

        self._apply_styles()

    def _apply_styles(self) -> None:
        css = """
        frame {
            background-color: #ffffe1;
            border: 1px solid #c0b878;
            border-radius: 8px;
            box-shadow: 2px 3px 6px rgba(0,0,0,0.18);
        }
        label {
            color: #222222;
        }
        entry {
            background-color: #ffffff;
            border: 1px solid #cca;
            border-radius: 4px;
            color: #111;
            padding: 4px;
        }
        button {
            background-color: #f7f5d0;
            border: 1px solid #998855;
            border-radius: 4px;
            color: #222;
            padding: 4px 8px;
        }
        button:hover {
            background-color: #fff9b0;
        }
        .xui-note {
            background-color: #f0eed0;
            border-left: 3px solid #b8860b;
            padding: 4px;
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode("utf-8"))
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def set_message(self, text: str, header: str = "💡 Sayri Assistant") -> None:
        self.header_lbl.set_markup(f"<span size='small' weight='bold' foreground='#8a6d10'>{GLib.markup_escape_text(header)}</span>")
        self.msg_lbl.set_text(text)
        self.show_all()

    def clear_xui(self) -> None:
        for child in self.xui_box.get_children():
            self.xui_box.remove(child)

    def render_xui_skeleton(self, screen_data: dict) -> None:
        """Renders an XUI declarative skeleton inside the speech bubble."""
        self.clear_xui()
        if not screen_data or not isinstance(screen_data, dict):
            return

        title = screen_data.get("title", "")
        if title:
            t_lbl = Gtk.Label()
            t_lbl.set_markup(f"<b>{GLib.markup_escape_text(title)}</b>")
            t_lbl.set_halign(Gtk.Align.START)
            self.xui_box.pack_start(t_lbl, False, False, 0)

        body = screen_data.get("body", [])
        for node in body:
            if not isinstance(node, dict):
                continue
            w = self._build_xui_node(node)
            if w:
                self.xui_box.pack_start(w, False, False, 2)

        footer = screen_data.get("footer", [])
        if footer:
            f_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            for btn_node in footer:
                b = self._build_xui_button(btn_node)
                if b:
                    f_box.pack_start(b, True, True, 0)
            self.xui_box.pack_start(f_box, False, False, 4)

        self.xui_box.show_all()

    def _build_xui_node(self, node: dict) -> Optional[Gtk.Widget]:
        ntype = node.get("t", "")
        if ntype == "text":
            lbl = Gtk.Label(label=node.get("text", ""))
            lbl.set_halign(Gtk.Align.START)
            lbl.set_line_wrap(True)
            return lbl
        elif ntype == "note":
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            box.get_style_context().add_class("xui-note")
            lbl = Gtk.Label(label=node.get("text", ""))
            lbl.set_line_wrap(True)
            box.pack_start(lbl, False, False, 2)
            return box
        elif ntype == "progress":
            pbar = Gtk.ProgressBar()
            pct = node.get("pct")
            if pct is not None:
                pbar.set_fraction(float(pct))
            else:
                pbar.pulse()
            if node.get("label"):
                pbar.set_text(node.get("label"))
                pbar.set_show_text(True)
            return pbar
        elif ntype == "button":
            return self._build_xui_button(node)
        return None

    def _build_xui_button(self, node: dict) -> Gtk.Button:
        btn = Gtk.Button(label=node.get("label", node.get("id", "OK")))
        action_id = node.get("id", "")
        btn.connect("clicked", lambda b: self._on_btn_clicked(action_id))
        return btn

    def _on_btn_clicked(self, action_id: str) -> None:
        if self.on_action:
            self.on_action(action_id)

    def _on_entry_activate(self, entry: Gtk.Entry) -> None:
        text = entry.get_text().strip()
        if text and self.on_submit:
            entry.set_text("")
            self.on_submit(text)


class ClippyWindow(Gtk.Window):
    """Floating desktop companion window."""

    def __init__(self) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_title("Sayri Clippy")
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_app_paintable(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)

        # Transparent RGBA screen support
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual and screen.is_composited():
            self.set_visual(visual)

        main_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        main_box.set_margin_start(8)
        main_box.set_margin_end(8)
        main_box.set_margin_top(8)
        main_box.set_margin_bottom(8)
        self.add(main_box)

        # Mascot Avatar
        self.avatar = ClippyAvatar(on_click=self._on_avatar_clicked)
        main_box.pack_start(self.avatar, False, False, 0)

        # Speech Balloon
        self.bubble = SpeechBubble(
            on_action=self._on_xui_action,
            on_submit=self._on_user_submit,
        )
        main_box.pack_start(self.bubble, True, True, 0)

        self.bubble.set_message("¡Hola! Soy Sayri (modo Clippy). ¿En qué puedo ayudarte hoy?")

        # IPC Client connection
        self.client = ipc.SayriClient()
        self.ipc_thread = threading.Thread(target=self._ipc_listener_loop, daemon=True)
        self.ipc_thread.start()

        self._position_bottom_right()

    def _position_bottom_right(self) -> None:
        screen = Gdk.Screen.get_default()
        if screen:
            geom = screen.get_monitor_geometry(screen.get_primary_monitor())
            self.move(geom.x + geom.width - 420, geom.y + geom.height - 240)

    def _on_avatar_clicked(self, event) -> None:
        if event.button == 3:  # Right-click context menu
            self._show_context_menu(event)
        else:  # Left click toggles voice push-to-talk
            self._toggle_mic()

    def _toggle_mic(self) -> None:
        try:
            res = self.client.call("toggle_listening")
            state = res.get("state", "listening") if isinstance(res, dict) else "listening"
            self.avatar.set_state(state)
        except Exception:
            self.bubble.set_message("Iniciando escucha por voz...", "🎙️ Micrófono")

    def _on_user_submit(self, text: str) -> None:
        self.avatar.set_state("thinking")
        self.bubble.set_message(f"«{text}»", "🤔 Pensando...")
        def _send():
            try:
                res = self.client.call("ask", {"text": text})
                ans = res.get("text") if isinstance(res, dict) else str(res)
                GLib.idle_add(self._on_assistant_response, ans)
            except Exception as err:
                GLib.idle_add(self._on_assistant_response, f"Error al procesar: {err}")
        threading.Thread(target=_send, daemon=True).start()

    def _on_assistant_response(self, text: str) -> None:
        self.avatar.set_state("speaking")
        self.bubble.set_message(text, "💬 Sayri")
        GLib.timeout_add(4000, lambda: self.avatar.set_state("idle") or False)

    def _on_xui_action(self, action_id: str) -> None:
        self.bubble.set_message(f"Acción seleccionada: {action_id}", "⚙️ Ejecutando")
        try:
            self.client.call("xui_action", {"action": action_id})
        except Exception:
            pass

    def _show_context_menu(self, event) -> None:
        menu = Gtk.Menu()

        item_talk = Gtk.MenuItem(label="🎙️ Escuchar / Push-to-Talk")
        item_talk.connect("activate", lambda m: self._toggle_mic())
        menu.append(item_talk)

        item_orb = Gtk.MenuItem(label="🔮 Cambiar a UI Orb (Predeterminada)")
        item_orb.connect("activate", lambda m: self._switch_to_orb())
        menu.append(item_orb)

        item_settings = Gtk.MenuItem(label="⚙️ Configuración de Sayri")
        item_settings.connect("activate", lambda m: os.system("sayri-settings &"))
        menu.append(item_settings)

        menu.append(Gtk.SeparatorMenuItem())

        item_quit = Gtk.MenuItem(label="❌ Cerrar Clippy")
        item_quit.connect("activate", lambda m: Gtk.main_quit())
        menu.append(item_quit)

        menu.show_all()
        menu.popup_at_pointer(event)

    def _switch_to_orb(self) -> None:
        sayri_config.config.set_string("ui", "default_ui", "desktop")
        sayri_config.config.save()
        os.system("sayri ui orb &")
        Gtk.main_quit()

    def _ipc_listener_loop(self) -> None:
        """Connects and listens to broadcast events from Sayri daemon."""
        while True:
            try:
                if not self.client.connected:
                    self.client.connect()
                # Read stream events
                for event in self.client.events():
                    ev_type = event.get("event")
                    if ev_type == "state":
                        st = event.get("state", "idle")
                        GLib.idle_add(self.avatar.set_state, st)
                    elif ev_type == "audio_level":
                        lvl = float(event.get("level", 0.0))
                        GLib.idle_add(self.avatar.set_state, self.avatar.state, lvl)
                    elif ev_type == "utterance":
                        txt = event.get("text", "")
                        GLib.idle_add(self.bubble.set_message, txt, "🎙️ Transcripción")
                    elif ev_type == "assistant_delta":
                        txt = event.get("text", "")
                        GLib.idle_add(self.bubble.set_message, txt, "💬 Sayri")
                    elif ev_type == "render_xui":
                        skeleton = event.get("screen", {})
                        GLib.idle_add(self.bubble.render_xui_skeleton, skeleton)
            except Exception:
                time.sleep(2.0)


def main() -> int:
    win = ClippyWindow()
    win.show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
