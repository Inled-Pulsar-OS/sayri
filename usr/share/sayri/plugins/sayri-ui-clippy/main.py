#!/usr/bin/env python3
"""Sayri Classic Retro Companions UI Plugin (All-in-One).

Supports Clippy, Bonzi Buddy, Merlin, Rover, Peedy, Genie, Links, Rocky, and F1
with interactive animations, sound effects, voice/chat, and XUI skeleton rendering.
"""

from __future__ import annotations

import ctypes
import json
import os
import signal
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


def _reexec_without_gtk4_preload() -> None:
    """Drop an inherited gtk4-layer-shell LD_PRELOAD before importing gi.

    This companion is a GTK3 app. Sayri's launcher exports
    LD_PRELOAD=libgtk4-layer-shell.so so the GTK4 orb's layer-shell is linked
    before WebKit's libwayland-client, but that library pulls in libgtk-4.so.1,
    which exports the same gdk_display_manager_get symbol as libgdk-3.so.0.
    Preloaded, it wins the global symbol lookup, so GDK3 calls GTK4's
    uninitialised implementation and the process dies with
      Gdk-ERROR **: gdk_display_manager_get() was called before gtk_init()
    before any window is mapped. Re-exec ourselves once without the preload so
    the plugin works no matter who launched it (CLI, Cajita button, autostart).
    """
    preload = os.environ.get("LD_PRELOAD", "")
    if "gtk4-layer-shell" not in preload:
        return
    if os.environ.get("SAYRI_CLIPPY_NO_PRELOAD") == "1":
        return  # already re-executed; do not loop

    kept = [p for p in preload.split(":") if p and "gtk4-layer-shell" not in p]
    env = dict(os.environ)
    if kept:
        env["LD_PRELOAD"] = ":".join(kept)
    else:
        env.pop("LD_PRELOAD", None)
    env["SAYRI_CLIPPY_NO_PRELOAD"] = "1"
    try:
        os.execve(sys.executable, [sys.executable, os.path.abspath(__file__), *sys.argv[1:]], env)
    except Exception:  # noqa: BLE001 - fall through and let GTK report the real problem
        pass


_reexec_without_gtk4_preload()

if os.environ.get("DISPLAY") and os.environ.get("SAYRI_FORCE_WAYLAND") != "1":
    os.environ["GDK_BACKEND"] = "x11"

import gi

try:
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("WebKit2", "4.1")
    from gi.repository import Gtk, Gdk, GLib
    Gtk.init_check()
    from gi.repository import WebKit2 as WebKit
    _GTK_VERSION = 3
except Exception:
    try:
        gi.require_version("Gtk", "4.0")
        gi.require_version("Gdk", "4.0")
        gi.require_version("WebKit", "6.0")
        from gi.repository import Gdk, GLib, Gtk, WebKit
        _GTK_VERSION = 4
    except Exception as err:
        print(f"[retro-ui] GTK/WebKit not available: {err}", file=sys.stderr)
        sys.exit(1)

try:
    from gi.repository import Gio
except Exception:  # Gio is present in practice; degrade gracefully if not.
    Gio = None

try:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell as LayerShell
    _LAYER_OK = True
except Exception:
    LayerShell = None
    _LAYER_OK = False

from sayri import config as sayri_config
from sayri import ipc, paths, sysinfo, blur_exclusion
import sayri
import subprocess

try:
    blur_exclusion.apply_blur_exclusion()
except Exception:
    pass

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

# Daemon commands the in-balloon Cajita panel is allowed to invoke. The webview
# only loads our own local index.html, but the panel still gets no blanket
# access: "quit" in particular belongs to the UI's own close control, not to a
# stray click on a list row.
CALL_ALLOWLIST = frozenset({
    # conversation
    "status", "talk", "ask", "listen", "toggle_listening", "stop_listening",
    "interrupt", "new_conversation", "switch_session",
    "sessions_list", "session_get", "session_rename", "session_delete",
    # configuration
    "config_get", "config_set", "config_list",
    # permissions: what an agent is waiting for you to decide, the answer, and
    # the standing rules a remembered answer created
    "permission_list", "permission_answer", "permission_forget",
    # settings surface: the panel renders whatever schema the daemon sends, so
    # it needs these to read and write the labelled sections
    "settings_schema", "settings_save", "plugin_settings_set", "asset_download",
    # agents
    "agents_list", "agent_switch", "agent_save", "agent_delete",
    # skills
    "skills_list", "skills_search", "skills_install", "skills_uninstall",
    # plugins and other UIs
    "plugins_list", "plugin_set_enabled",
    "ui_list", "ui_start", "ui_stop", "ui_status",
    # gateways
    "gateway_list", "gateway_start", "gateway_stop", "gateway_delete", "gateway_save",
    # routines
    "routines_list", "routines_run", "routine_save", "routine_delete",
    "routine_set_enabled",
    # vault
    "vault_list", "vault_set", "vault_delete",
    # misc helpers used by the panel
    "clipboard_copy", "version",
})


def _as_bool(value: Any, default: bool = False) -> bool:
    """Coerce a plugin setting that may arrive as bool, int or string."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return default


class RetroCompanionWindow:
    """Floating transparent desktop assistant supporting all classic characters."""

    def __init__(self) -> None:
        self.pid_file = Path(paths.state_dir()) / "sayri-ui-clippy.pid"
        self._indicator_proc: Optional[subprocess.Popen] = None
        self._write_pid()

        # Hook termination signals
        signal.signal(signal.SIGTERM, lambda *_: GLib.idle_add(self._quit))
        signal.signal(signal.SIGINT, lambda *_: GLib.idle_add(self._quit))

        manifest_p = Path(script_dir) / "manifest.json"
        self.manifest: dict = {}
        p_cfg: dict = {}
        if manifest_p.is_file():
            try:
                from sayri import plugin_settings as ps

                self.manifest = json.loads(manifest_p.read_text(encoding="utf-8"))
                p_cfg = ps.read_values(self.manifest)
            except Exception:
                self.manifest = {}
                p_cfg = {}
        self.current_char = p_cfg.get("character") or sayri_config.config.get_string("ui.clippy", "character", "Clippy")
        self.sound_effects = _as_bool(p_cfg.get("sound_effects", True))
        # -1, not 0: desktop 0 is a real workspace, and starting at it would
        # make the first tick write a value that is already correct.
        self.client = ipc.SayriClient()

        if _GTK_VERSION == 3:
            self.win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
            self.win.set_title("Sayri Companion")
            self.win.set_decorated(False)
            self.win.set_type_hint(Gdk.WindowTypeHint.UTILITY)
            self.win.set_keep_above(True)
            self.win.set_app_paintable(True)
            self.win.set_skip_taskbar_hint(True)
            self.win.set_skip_pager_hint(True)
            self.win.set_accept_focus(True)
            self.win.set_default_size(440, 380)

            screen = self.win.get_screen()
            visual = screen.get_rgba_visual()
            if visual:
                self.win.set_visual(visual)

            css_provider = Gtk.CssProvider()
            css_provider.load_from_data(b"window, .background { background-color: rgba(0,0,0,0); background-image: none; border: none; box-shadow: none; }")
            Gtk.StyleContext.add_provider_for_screen(
                screen,
                css_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

            self.webview = WebKit.WebView()
            self.win.add(self.webview)

            import cairo
            def _on_draw(w, cr):
                cr.set_source_rgba(0, 0, 0, 0)
                cr.set_operator(cairo.OPERATOR_SOURCE)
                cr.paint()
                return False
            self.win.connect("draw", _on_draw)
        else:
            self.win = Gtk.Window()
            self.win.set_title("Sayri Companion")
            self.win.set_decorated(False)
            self.win.set_default_size(440, 380)

            css_provider = Gtk.CssProvider()
            css_provider.load_from_data(b"window, .background, widget { background-color: transparent !important; background: none !important; }")
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(),
                css_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

            if _LAYER_OK and LayerShell.is_supported():
                LayerShell.init_for_window(self.win)
                LayerShell.set_layer(self.win, LayerShell.Layer.OVERLAY)
                LayerShell.set_anchor(self.win, LayerShell.Edge.BOTTOM, True)
                LayerShell.set_anchor(self.win, LayerShell.Edge.RIGHT, True)
                LayerShell.set_margin(self.win, LayerShell.Edge.BOTTOM, 24)
                LayerShell.set_margin(self.win, LayerShell.Edge.RIGHT, 24)
                LayerShell.set_exclusive_zone(self.win, -1)
                LayerShell.set_keyboard_mode(self.win, LayerShell.KeyboardMode.ON_DEMAND)

            self.webview = WebKit.WebView()
            self.win.set_child(self.webview)

        # Transparent background settings
        bg = Gdk.RGBA()
        bg.parse("rgba(0,0,0,0)")
        self.webview.set_background_color(bg)

        # WebKit settings
        settings = self.webview.get_settings()
        settings.set_enable_javascript(True)
        settings.set_allow_file_access_from_file_urls(True)
        settings.set_allow_universal_access_from_file_urls(True)
        if hasattr(settings, "set_enable_developer_extras"):
            settings.set_enable_developer_extras(True)
        if hasattr(settings, "set_enable_write_console_messages_to_stdout"):
            settings.set_enable_write_console_messages_to_stdout(True)

        # Setup WebKit Message Handlers
        ucm = self.webview.get_user_content_manager()
        ucm.register_script_message_handler("sayriPrompt")
        ucm.register_script_message_handler("sayriAction")
        ucm.register_script_message_handler("sayriCall")
        ucm.connect("script-message-received::sayriPrompt", self._on_js_prompt)
        ucm.connect("script-message-received::sayriAction", self._on_js_action)
        ucm.connect("script-message-received::sayriCall", self._on_js_call)

        def _on_load_changed(webview, load_event):
            if load_event == WebKit.LoadEvent.FINISHED:
                self._dispatch_js("window.applyPluginInfo && window.applyPluginInfo("
                                  + json.dumps(self.plugin_info()) + ")")
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

        # Connect Daemon IPC
        self._setup_ipc()

        self.win.connect("realize", lambda *_: self._position_bottom_right())
        self.win.connect("map", lambda *_: (self._position_bottom_right(), GLib.timeout_add(100, lambda: (self._position_bottom_right(), False))))
        self._position_bottom_right()

        # Last, because it labels the tray entry with the companion that was
        # chosen, and that is only settled once the config has been read.
        self._start_indicator()

    def _write_pid(self) -> None:
        try:
            self.pid_file.parent.mkdir(parents=True, exist_ok=True)
            self.pid_file.write_text(str(os.getpid()), encoding="utf-8")
        except Exception:
            pass

    def plugin_info(self) -> dict:
        """Settings the web panel needs, injected once the page has loaded.

        The character list and the sound toggle are plugin settings, not core
        config, so they only exist here. Handing them to JS keeps one list of
        companions instead of duplicating the names in the HTML.
        """
        return {
            "character": self.current_char,
            "characters": [{"id": cid, "label": label} for cid, label in AVAILABLE_CHARACTERS],
            "sound_effects": self.sound_effects,
            "version": getattr(sayri, "__version__", "") if sayri else "",
        }

    def _set_sound_effects(self, enabled: bool) -> None:
        self.sound_effects = bool(enabled)
        try:
            from sayri import plugin_settings as ps

            ps.write_setting(self.manifest, "sound_effects", bool(enabled))
        except Exception:
            pass

    def _setup_ipc(self) -> None:
        def _connect():
            try:
                if self.client.connect():
                    self.client.listen_events(self._on_daemon_event)
            except Exception as exc:
                print(f"[retro-ui] Daemon connect: {exc}", file=sys.stderr)
        threading.Thread(target=_connect, daemon=True).start()

    def _on_daemon_event(self, event: dict) -> None:
        # The daemon broadcasts "shutdown" when the user picks Exit in the
        # appindicator, `sayri killall` runs, or the daemon is stopped. This
        # window is a detached background app, so without this it would stay
        # floating on the desktop with no way to get rid of it.
        if isinstance(event, dict) and event.get("event") == "shutdown":
            GLib.idle_add(self._quit)
            return
        if isinstance(event, dict) and event.get("event") == "plugin_settings_changed":
            self._on_plugin_settings_changed(event)
            return
        try:
            self._emit_event(event)
        except Exception:
            pass

    def _emit_event(self, event: dict) -> None:
        """Hand one daemon event to the page, queueing it until the page is ready.

        This process starts listening to the daemon right after the webview is
        created, which can be before the page's own script has run, so
        `window.onDaemonEvent` may not exist yet. Some of the events that arrive
        in that window matter (settings_changed, asset_progress), so queue them
        in the page rather than dropping them or spamming the console.
        """
        payload = json.dumps(event)
        self._dispatch_js(
            "(function(e){if(typeof window.onDaemonEvent==='function')"
            "window.onDaemonEvent(e);"
            "else{var q=window.__sayriEarly=window.__sayriEarly||[];"
            "q.push(e);if(q.length>200)q.splice(0,q.length-200);}})("
            + payload + ")"
        )

    def _on_plugin_settings_changed(self, event: dict) -> None:
        """Apply one of our own plugin settings changed somewhere else.

        The settings surface (the Cajita panel) writes to the same plugin config
        file this process reads, so a change made there would otherwise only
        take effect on the next launch. Re-applying it here means picking a new
        character in the panel swaps the character on screen straight away.
        set_character() also writes the file, so re-applying is idempotent.
        """
        if event.get("plugin_id") != self.manifest.get("id", "sayri-ui-clippy"):
            return
        key = event.get("key")
        value = event.get("value")
        if key == "character" and value:
            self.set_character(str(value))
        elif key == "sound_effects":
            self._set_sound_effects(_as_bool(value))

    def _position_bottom_right(self) -> None:
        # Keep the window's bottom-right corner pinned to the monitor's, so
        # growing it for the Cajita grows it to the left and upwards instead
        # of drifting off the right edge of the screen.
        w, h = self._window_size()
        right, bottom = 6, 6
        self._pin_to_all_workspaces()
        try:
            geom = sysinfo.get_primary_geometry_gnome()
            if geom:
                gx, gy, gw, gh = geom
                self.win.move(gx + gw - w - right, gy + gh - h - bottom)
                return
        except Exception:
            pass
        try:
            if _GTK_VERSION == 3:
                screen = self.win.get_screen() or Gdk.Screen.get_default()
                if screen:
                    mon = screen.get_primary_monitor()
                    geom = screen.get_monitor_geometry(mon)
                    self.win.move(geom.x + geom.width - w - right,
                                  geom.y + geom.height - h - bottom)
        except Exception:
            pass

    def _pin_to_all_workspaces(self) -> bool:
        """Ask the window manager to keep this window on every desktop.

        The answer is the EWMH state ``_NET_WM_STATE_STICKY``, appended to the
        window's own state list together with the above/skip-taskbar hints the
        window already sets. This is the same call the main Sayri window makes
        in :mod:`sayri.webkit`, and it is the one that works: the panel was
        staying on a single desktop because nothing had ever asked for sticky.

        Two things that look right and are not, both tried here first:

        * ``_NET_WM_DESKTOP = 0xFFFFFFFF`` is the EWMH value for "every
          desktop", but that property is written by the window manager to
          *inform* clients. A client writing it makes Mutter drop the window
          from its client list, and the process dies.
        * Polling ``_NET_CURRENT_DESKTOP`` and moving the window to whichever
          desktop it read works, at the cost of a wakeup every few hundred
          milliseconds and a visible lag on every switch. Sticky asks once.

        Appended, not replaced (mode 2), because the existing states are
        already correct and replacing the list would drop them.
        """
        if _GTK_VERSION != 3:
            # The Wayland path builds the surface with gtk4-layer-shell, which
            # places it per-monitor and does not read X hints. There is no X
            # window here to hint.
            return False
        gdk_window = self.win.get_window()
        if gdk_window is None:
            # Not realized yet. The caller runs again on map, so this is a
            # normal early return rather than a failure.
            return False
        try:
            xlib = ctypes.CDLL("libX11.so.6")
        except OSError as exc:
            print(f"[retro-ui] sticky hint not applied: {exc}", file=sys.stderr)
            return False

        class _XAtom(ctypes.c_ulong):
            pass

        class XClientMessageEvent(ctypes.Structure):
            _fields_ = [
                ("type", ctypes.c_int),
                ("serial", ctypes.c_ulong),
                ("send_event", ctypes.c_int),
                ("display", ctypes.c_void_p),
                ("window", ctypes.c_ulong),
                ("message_type", ctypes.c_ulong),
                ("format", ctypes.c_int),
                ("data_l", ctypes.c_long * 5),
            ]

        class XEvent(ctypes.Union):
            _fields_ = [
                ("type", ctypes.c_int),
                ("xclient", XClientMessageEvent),
                ("pad", ctypes.c_long * 24),
            ]

        display = None
        try:
            xlib.XOpenDisplay.restype = ctypes.c_void_p
            xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
            xlib.XInternAtom.restype = ctypes.c_ulong
            xlib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
            xlib.XChangeProperty.argtypes = [
                ctypes.c_void_p, ctypes.c_ulong, _XAtom, _XAtom,
                ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int,
            ]
            xlib.XSendEvent.argtypes = [
                ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long,
                ctypes.POINTER(XEvent),
            ]
            xlib.XSendEvent.restype = ctypes.c_int
            xlib.XFlush.argtypes = [ctypes.c_void_p]
            xlib.XCloseDisplay.argtypes = [ctypes.c_void_p]

            display = xlib.XOpenDisplay(None)
            if not display:
                print("[retro-ui] sticky hint not applied: no X display",
                      file=sys.stderr)
                return False
            xid = gdk_window.get_xid()
            root = self.win.get_screen().get_root_window().get_xid()
            XA_ATOM = 4
            net_wm_state = xlib.XInternAtom(display, b"_NET_WM_STATE", 0)
            sticky = xlib.XInternAtom(display, b"_NET_WM_STATE_STICKY", 0)
            states = (ctypes.c_ulong * 1)(sticky)
            # Appended to the property as well as sent below. The property is
            # what a client reads for its own state, and the message is what
            # Mutter actually acts on: with only the property the state is set
            # and then never honoured, which looks exactly like no hint at all.
            xlib.XChangeProperty(display, xid, net_wm_state, XA_ATOM, 32, 2,
                                 ctypes.cast(states, ctypes.c_void_p), 1)

            # _NET_WM_STATE_ADD, sticky. The mask is the two structure events on
            # the root: this is a message to the window manager, not to a window.
            ev = XEvent()
            ev.type = 33                       # ClientMessage
            ev.xclient.type = 33
            ev.xclient.serial = 0
            ev.xclient.send_event = 1
            ev.xclient.display = display
            ev.xclient.window = xid
            ev.xclient.message_type = net_wm_state
            ev.xclient.format = 32
            ev.xclient.data_l[0] = 1           # _NET_WM_STATE_ADD
            ev.xclient.data_l[1] = sticky
            ev.xclient.data_l[2] = 0           # second atom: none
            ev.xclient.data_l[3] = 1           # source: normal application
            ev.xclient.data_l[4] = 0
            mask = 0x00100000 | 0x00080000     # Redirect | Notify on the root
            xlib.XSendEvent(display, root, 0, mask, ctypes.byref(ev))
            xlib.XFlush(display)
            return True
        except Exception as exc:
            print(f"[retro-ui] sticky hint not applied: {exc}", file=sys.stderr)
            return False
        finally:
            if display:
                xlib.XCloseDisplay(display)

    def _window_size(self) -> tuple[int, int]:
        try:
            if _GTK_VERSION == 3:
                return self.win.get_size()
            return self.win.get_default_size()
        except Exception:
            return (440, 380)

    def _on_js_prompt(self, _ucm, msg) -> None:
        """User submitted text in the speech balloon."""
        try:
            val = msg.get_js_value()
            text = val.to_string()
        except Exception:
            try:
                text = str(msg.get_value())
            except Exception:
                text = str(msg)

        if not text or not text.strip():
            return

        def _send():
            try:
                self.client.call("talk", {"text": text.strip()})
            except Exception as exc:
                self._dispatch_js(f"window.showBalloon('Error: {exc}', '❌ Sayri Error')")
        threading.Thread(target=_send, daemon=True).start()

    def _on_js_action(self, _ucm, msg) -> None:
        """User clicked an XUI action button or UI control."""
        try:
            val = msg.get_js_value()
            action_id = val.to_string()
        except Exception:
            try:
                action_id = str(msg.get_value())
            except Exception:
                action_id = str(msg)

        if action_id == "open_settings":
            subprocess.Popen(["sayri-settings"], **sysinfo.spawn_flags())
            return
        elif action_id == "toggle_mic":
            self._toggle_mic()
            return
        elif action_id == "toggle_cajita":
            self._dispatch_js("window.toggleCajita && window.toggleCajita()")
            return
        elif action_id == "cajita_open":
            self._resize_window(450, 640)
            return
        elif action_id == "cajita_close":
            self._resize_window(440, 380)
            return
        elif action_id == "kill_all":
            self._kill_all_processes()
            return
        elif action_id == "restart_companion":
            self._restart_companion()
            return
        elif action_id.startswith("open_url:"):
            self._open_url(action_id.split(":", 1)[1])
            return
        elif action_id.startswith("set_character:"):
            self.set_character(action_id.split(":", 1)[1])
            return
        elif action_id.startswith("set_sound_effects:"):
            self._set_sound_effects(action_id.split(":", 1)[1] == "1")
            return

        try:
            self.client.call("xui_action", {"action": action_id})
        except Exception:
            pass

    def _resize_window(self, width: int, height: int) -> None:
        """Grow the window for the Cajita panel, shrink it back to the balloon."""
        def _apply():
            try:
                if _GTK_VERSION == 3:
                    self.win.resize(width, height)
                else:
                    self.win.set_default_size(width, height)
            except Exception:
                pass
            # The companion sits to the left of the panel, so keep the window
            # pinned to the same bottom-right corner after it grows.
            self._position_bottom_right()
        GLib.idle_add(_apply)

    def _on_js_call(self, _ucm, msg) -> None:
        """Forward a Cajita request from the web panel to the daemon.

        The balloon panel is plain WebKit, so it has no database, no config
        file and no subprocess of its own. Rather than reimplementing any of
        that, the panel asks the daemon over the same IPC the GTK Cajita uses,
        which keeps one source of truth for every front-end. The reply comes
        back as ``window.onCallResult(req_id, ok, data)``.
        """
        try:
            val = msg.get_js_value()
            raw = val.to_string()
        except Exception:
            try:
                raw = str(msg.get_value())
            except Exception:
                raw = str(msg)

        try:
            request = json.loads(raw)
        except Exception:
            request = None
        if not isinstance(request, dict):
            self._reply_call("", False, error="malformed request")
            return

        req_id = str(request.get("req_id", ""))
        cmd = str(request.get("cmd", "")).strip()
        params = request.get("params") or {}
        if not isinstance(params, dict):
            params = {}

        if cmd not in CALL_ALLOWLIST:
            self._reply_call(req_id, False, error=f"command not allowed: {cmd}")
            return

        def _run():
            try:
                result = self.client.call(cmd, params)
                self._reply_call(req_id, True, data=result)
            except Exception as exc:
                self._reply_call(req_id, False, error=str(exc))

        threading.Thread(target=_run, daemon=True).start()

    def _open_url(self, url: str) -> None:
        """Open a web page in the user's browser.

        The panel has no browser of its own, so anything that needs the web
        (the Pulsar Store, documentation) is handed to the desktop. Only
        http(s) is accepted: the action comes from page content, and a
        ``file:`` or custom-scheme URL would be a way out of that.
        """
        url = str(url or "").strip()
        if not url.startswith(("http://", "https://")):
            return
        try:
            if Gio is not None:
                Gio.AppInfo.launch_default_for_uri(url, None)
            else:
                subprocess.Popen(["xdg-open", url], **sysinfo.spawn_flags())
        except Exception as exc:
            print(f"[retro-ui] could not open {url}: {exc}", file=sys.stderr)

    def _kill_all_processes(self) -> None:
        """Terminate every Sayri process, this companion included.

        The request goes to the daemon rather than to the CLI in a child
        process, because this plugin is one of the things being killed: a
        subprocess call would be cut off mid-request and could leave the daemon
        half-told. The daemon survives the sweep (its command line matches none
        of the patterns) and can still answer whatever comes next.
        """
        def _run() -> None:
            try:
                self.client.call("killall", {"include_daemon": False}, timeout=8.0)
            except Exception as exc:
                # No daemon to ask. Fall back to the CLI, detached, so this
                # process is not holding the cleanup hostage.
                print(f"[retro-ui] daemon killall failed ({exc}); using the CLI",
                      file=sys.stderr)
                try:
                    subprocess.Popen(["sayri", "killall"], **sysinfo.spawn_flags())
                except Exception as exc2:
                    print(f"[retro-ui] killall fallback failed: {exc2}", file=sys.stderr)
        threading.Thread(target=_run, daemon=True).start()

    def _restart_companion(self) -> None:
        """Stop and start this UI plugin, without touching the daemon."""
        # The plugin's own id is the name of the pid file it owns
        # (…/state/sayri-ui-clippy.pid), which is what the daemon's ui_* commands
        # key off. Deriving it keeps the two in step with no second source.
        ui_id = self.pid_file.stem

        def _run() -> None:
            time.sleep(0.6)
            for cmd, params in (("ui_stop", {"ui_id": ui_id}),
                                ("ui_start", {"ui_id": ui_id})):
                try:
                    self.client.call(cmd, params, timeout=10.0)
                except Exception as exc:
                    print(f"[retro-ui] {cmd} failed: {exc}", file=sys.stderr)
                    return
                time.sleep(1.0)
        threading.Thread(target=_run, daemon=True).start()

    def _reply_call(self, req_id: str, ok: bool, data: Any = None, error: str = "") -> None:
        payload = {"req_id": req_id, "ok": bool(ok), "data": data, "error": error}
        self._dispatch_js(
            "window.onCallResult && window.onCallResult(" + json.dumps(payload) + ")"
        )

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
        def _toggle():
            try:
                res = self.client.call("toggle_listening")
                state = res.get("state", "idle") if isinstance(res, dict) else "idle"
                self._dispatch_js(f"window.onDaemonEvent({json.dumps({'event': 'state', 'state': state})})")
            except Exception as exc:
                self._dispatch_js(f"window.showBalloon('Microphone error: {exc}', '🎙️ Microphone')")
        threading.Thread(target=_toggle, daemon=True).start()

    def _switch_to_orb(self) -> None:
        sayri_config.config.set_string("ui", "default_ui", "desktop")
        sayri_config.config.save()
        os.system("sayri ui orb &")
        self._quit()

    def _start_indicator(self) -> None:
        """Run the tray indicator for this companion, in its own process.

        The tray spec is GTK3 and this window is a GTK4 layer-shell webview, so
        the indicator cannot live in this process. It is started the same way
        the main app starts its own (app.py runs ``-m sayri.indicator``), which
        keeps one implementation of the menu instead of two.
        """
        env = dict(os.environ)
        lib_path = os.path.join(paths.lib_dir())
        env["PYTHONPATH"] = lib_path + (":" + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        # The orb's layer-shell preload has to go, or a GTK3 process starts with
        # GTK4 libraries already mapped and dies on the first window.
        env.pop("LD_PRELOAD", None)
        title = self.current_char or "Sayri"
        self._clear_stale_indicator()
        try:
            self._indicator_proc = subprocess.Popen(
                [sys.executable, "-m", "sayri.indicator",
                 "--for-ui", self.pid_file.stem, "--title", title],
                env=env, **sysinfo.spawn_flags())
        except Exception as exc:
            print(f"[retro-ui] tray indicator not started: {exc}", file=sys.stderr)

    def _clear_stale_indicator(self) -> None:
        """Remove an indicator left behind for this companion.

        A companion killed outright (SIGKILL, a crash) never runs its own
        teardown, so its indicator survives. Starting a second one would leave
        two identical tray entries, and the orphan would keep a menu that acts
        on a window that is not there. The pattern includes this companion's id
        so the main app's own indicator is left alone.
        """
        marker = f"sayri.indicator --for-ui {self.pid_file.stem}"
        try:
            import subprocess as _sp
            out = _sp.run(["pgrep", "-f", marker], capture_output=True, text=True,
                          check=False).stdout
        except OSError:
            return
        for line in out.split():
            try:
                pid = int(line.strip())
            except ValueError:
                continue
            if pid and pid != os.getpid():
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass

    def _stop_indicator(self) -> None:
        proc = getattr(self, "_indicator_proc", None)
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except Exception:
                    proc.kill()
            except Exception:
                pass

    def _quit(self) -> None:
        # The indicator has to go with the companion, or a tray entry is left
        # pointing at a window that no longer exists.
        self._stop_indicator()
        try:
            self.pid_file.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            if _GTK_VERSION == 3:
                Gtk.main_quit()
            else:
                self.win.close()
        except Exception:
            pass
        os._exit(0)

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
        char_item = Gtk.MenuItem(label="🎭 Change character...")
        char_menu = Gtk.Menu()
        for char_id, char_title in AVAILABLE_CHARACTERS:
            sub_item = Gtk.MenuItem(label=char_title)
            sub_item.connect("activate", lambda m, c=char_id: self.set_character(c))
            char_menu.append(sub_item)
        char_item.set_submenu(char_menu)
        menu.append(char_item)

        item_talk = Gtk.MenuItem(label="🎙️ Listen / push-to-talk")
        item_talk.connect("activate", lambda m: self._toggle_mic())
        menu.append(item_talk)

        item_cajita = Gtk.MenuItem(label="📦 Open the Cajita panel")
        item_cajita.connect("activate", lambda m: self._dispatch_js("window.toggleCajita && window.toggleCajita()"))
        menu.append(item_cajita)

        item_orb = Gtk.MenuItem(label="🔮 Switch to the Orb UI (Cajita)")
        item_orb.connect("activate", lambda m: self._switch_to_orb())
        menu.append(item_orb)

        item_settings = Gtk.MenuItem(label="⚙️ Sayri settings")
        item_settings.connect("activate", lambda m: subprocess.Popen(["sayri-settings"], **sysinfo.spawn_flags()))
        menu.append(item_settings)

        menu.append(Gtk.SeparatorMenuItem())

        item_quit = Gtk.MenuItem(label="❌ Close companion")
        item_quit.connect("activate", lambda m: self._quit())
        menu.append(item_quit)

        menu.show_all()
        menu.popup_at_pointer(event)

    def run(self) -> int:
        if _GTK_VERSION == 4:
            self.win.present()
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
