#!/usr/bin/env python3
"""Sayri Classic Retro Companions UI Plugin (All-in-One).

Supports Clippy, Bonzi Buddy, Merlin, Rover, Peedy, Genie, Links, Rocky, and F1
with interactive animations, sound effects, voice/chat, and XUI skeleton rendering.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

# Ensure sayri lib is available in sys.path
script_dir = os.path.dirname(os.path.abspath(__file__))
sayri_lib = os.path.normpath(os.path.join(script_dir, "..", "..", "lib"))
if os.path.isdir(sayri_lib) and sayri_lib not in sys.path:
    sys.path.insert(0, sayri_lib)

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("WebKit", "6.0")
    from gi.repository import Gdk, GLib, Gtk, WebKit
    _GTK_VERSION = 4
except Exception:
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("WebKit2", "4.1")
        from gi.repository import Gdk, GLib, Gtk
        from gi.repository import WebKit2 as WebKit
        _GTK_VERSION = 3
    except Exception as err:
        print(f"[retro-ui] GTK/WebKit not available: {err}", file=sys.stderr)
        sys.exit(1)

from sayri import config as sayri_config
from sayri import ipc, paths, sysinfo

AVAILABLE_CHARACTERS = [
    ("Clippy", "📎 Clippy (Paperclip)"),
    ("Bonzi", "🐒 Bonzi Buddy (Purple Gorilla)"),
    ("Merlin", "🧙 Merlin (Wizard)"),
    ("Rover", "🐕 Rover (Dog)"),
    ("Peedy", "🦜 Peedy (Green Parrot)"),
    ("Genie", "🧞 Genie"),
    ("Links", "🐈 Links (Cat)"),
    ("Rocky", "🐶 Rocky"),
    ("F1", "🤖 F1 (Robot)"),
]


class RetroCompanionWindow:
    """Floating transparent desktop assistant supporting all classic characters."""

    def __init__(self) -> None:
        self.pid_file = Path(paths.state_dir()) / "sayri-ui-clippy.pid"
        self._write_pid()

        manifest_p = Path(script_dir) / "manifest.json"
        p_cfg = {}
        if manifest_p.is_file():
            try:
                from sayri import plugin_settings as ps
                manifest_d = json.loads(manifest_p.read_text(encoding="utf-8"))
                p_cfg = ps.read_values(manifest_d)
            except Exception:
                p_cfg = {}
        self.current_char = p_cfg.get("character") or sayri_config.config.get_string("ui.clippy", "character", "Clippy")
        self.client = ipc.SayriClient()

        if _GTK_VERSION == 4:
            self.win = Gtk.Window()
            self.win.set_title("Sayri Companion")
            self.win.set_decorated(False)
            self.win.set_default_size(440, 360)
            self.webview = WebKit.WebView()
            self.win.set_child(self.webview)
        else:
            self.win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
            self.win.set_title("Sayri Companion")
            self.win.set_decorated(False)
            self.win.set_keep_above(True)
            self.win.set_app_paintable(True)
            self.win.set_skip_taskbar_hint(True)
            self.win.set_skip_pager_hint(True)
            self.win.set_default_size(440, 360)
            self.webview = WebKit.WebView()
            self.win.add(self.webview)

        # Transparent background settings
        bg = Gdk.RGBA()
        bg.parse("rgba(0,0,0,0)")
        self.webview.set_background_color(bg)

        # Setup WebKit Message Handlers
        ucm = self.webview.get_user_content_manager()
        ucm.register_script_message_handler("sayriPrompt")
        ucm.register_script_message_handler("sayriAction")
        ucm.connect("script-message-received::sayriPrompt", self._on_js_prompt)
        ucm.connect("script-message-received::sayriAction", self._on_js_action)

        def _on_load_changed(webview, load_event):
            if load_event == WebKit.LoadEvent.FINISHED:
                self._dispatch_js(f"window.switchAgent({json.dumps(self.current_char)})")
        self.webview.connect("load-changed", _on_load_changed)

        # Right click gesture / click
        if _GTK_VERSION == 4:
            click = Gtk.GestureClick()
            click.set_button(3)  # Right-click
            click.connect("pressed", self._on_right_click_gtk4)
            self.win.add_controller(click)
        else:
            self.win.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            self.win.connect("button-press-event", self._on_button_press_gtk3)

        # Load web assets
        html_path = os.path.join(script_dir, "web", "index.html")
        self.webview.load_uri(f"file://{os.path.abspath(html_path)}")

        # Connect Daemon IPC thread
        self.ipc_thread = threading.Thread(target=self._ipc_listener_loop, daemon=True)
        self.ipc_thread.start()

        self._position_bottom_right()

    def _write_pid(self) -> None:
        try:
            self.pid_file.parent.mkdir(parents=True, exist_ok=True)
            self.pid_file.write_text(str(os.getpid()), encoding="utf-8")
        except Exception:
            pass

    def _position_bottom_right(self) -> None:
        try:
            if _GTK_VERSION == 3:
                screen = Gdk.Screen.get_default()
                if screen:
                    geom = screen.get_monitor_geometry(screen.get_primary_monitor())
                    self.win.move(geom.x + geom.width - 460, geom.y + geom.height - 380)
        except Exception:
            pass

    def _on_js_prompt(self, _ucm, msg) -> None:
        """User submitted text in the speech balloon."""
        try:
            if _GTK_VERSION == 4:
                text = msg.get_js_value().to_string()
            else:
                text = msg.get_value().to_string()
        except Exception:
            text = str(msg)

        if not text:
            return

        def _send():
            try:
                res = self.client.call("ask", {"text": text})
                ans = res.get("text") if isinstance(res, dict) else str(res)
                self._dispatch_js(f"window.showBalloon({json.dumps(ans)}, '💬 Sayri')")
            except Exception as exc:
                self._dispatch_js(f"window.showBalloon('Error: {exc}', '❌ Error')")
        threading.Thread(target=_send, daemon=True).start()

    def _on_js_action(self, _ucm, msg) -> None:
        """User clicked an XUI action button."""
        try:
            if _GTK_VERSION == 4:
                action_id = msg.get_js_value().to_string()
            else:
                action_id = msg.get_value().to_string()
        except Exception:
            action_id = str(msg)
        try:
            self.client.call("xui_action", {"action": action_id})
        except Exception:
            pass

    def _dispatch_js(self, js_code: str) -> None:
        def _exec():
            try:
                if _GTK_VERSION == 4:
                    self.webview.evaluate_javascript(js_code, -1, None, None, None, None, None)
                else:
                    self.webview.run_javascript(js_code, None, None, None)
            except Exception:
                pass
        GLib.idle_add(_exec)

    def set_character(self, char_name: str) -> None:
        self.current_char = char_name
        manifest_p = Path(script_dir) / "manifest.json"
        if manifest_p.is_file():
            try:
                from sayri import plugin_settings as ps
                manifest_d = json.loads(manifest_p.read_text(encoding="utf-8"))
                ps.write_setting(manifest_d, "character", char_name)
            except Exception:
                pass
        sayri_config.config.set_string("ui.clippy", "character", char_name)
        sayri_config.config.save()
        self._dispatch_js(f"window.switchAgent({json.dumps(char_name)})")

    def _toggle_mic(self) -> None:
        try:
            res = self.client.call("toggle_listening")
            state = res.get("state", "listening") if isinstance(res, dict) else "listening"
            self._dispatch_js(f"window.onDaemonEvent({json.dumps({'event': 'state', 'state': state})})")
        except Exception:
            self._dispatch_js("window.showBalloon('Iniciando escucha...', '🎙️ Micrófono')")

    def _switch_to_orb(self) -> None:
        sayri_config.config.set_string("ui", "default_ui", "desktop")
        sayri_config.config.save()
        os.system("sayri ui orb &")
        self._quit()

    def _quit(self) -> None:
        try:
            self.pid_file.unlink(missing_ok=True)
        except Exception:
            pass
        if _GTK_VERSION == 3:
            Gtk.main_quit()
        else:
            self.win.close()
        sys.exit(0)

    def _on_button_press_gtk3(self, widget, event):
        if event.button == 3:
            self._show_menu_gtk3(event)
            return True
        return False

    def _on_right_click_gtk4(self, gesture, n_press, x, y):
        # GTK4 context menu
        pass

    def _show_menu_gtk3(self, event):
        menu = Gtk.Menu()

        # Character sub-menu
        char_item = Gtk.MenuItem(label="🎭 Cambiar Personaje...")
        char_menu = Gtk.Menu()
        for char_id, char_title in AVAILABLE_CHARACTERS:
            sub_item = Gtk.MenuItem(label=char_title)
            sub_item.connect("activate", lambda m, c=char_id: self.set_character(c))
            char_menu.append(sub_item)
        char_item.set_submenu(char_menu)
        menu.append(char_item)

        item_talk = Gtk.MenuItem(label="🎙️ Escuchar / Push-to-Talk")
        item_talk.connect("activate", lambda m: self._toggle_mic())
        menu.append(item_talk)

        item_orb = Gtk.MenuItem(label="🔮 Cambiar a UI Orb (Cajita)")
        item_orb.connect("activate", lambda m: self._switch_to_orb())
        menu.append(item_orb)

        item_settings = Gtk.MenuItem(label="⚙️ Ajustes de Sayri")
        item_settings.connect("activate", lambda m: os.system("sayri-settings &"))
        menu.append(item_settings)

        menu.append(Gtk.SeparatorMenuItem())

        item_quit = Gtk.MenuItem(label="❌ Cerrar Companion")
        item_quit.connect("activate", lambda m: self._quit())
        menu.append(item_quit)

        menu.show_all()
        menu.popup_at_pointer(event)

    def _ipc_listener_loop(self) -> None:
        while True:
            try:
                if not self.client.connected:
                    self.client.connect()
                for event in self.client.events():
                    js_event = json.dumps(event)
                    self._dispatch_js(f"window.onDaemonEvent({js_event})")
            except Exception:
                time.sleep(2.0)

    def run(self) -> int:
        if _GTK_VERSION == 4:
            self.win.present()
            # GTK4 main loop if not using Gtk.Application
            loop = GLib.MainLoop()
            loop.run()
        else:
            self.win.show_all()
            Gtk.main()
        return 0


def main() -> int:
    app = RetroCompanionWindow()
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
