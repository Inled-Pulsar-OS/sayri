"""Shared GTK4 / WebKitGTK 6.0 plumbing for Sayri's web windows.

- `webview_setup()` creates a WebKit6 WebView with the custom "sayri://"
  scheme, the script-message bridge and JS error forwarding.
- `WebWindow` is a small base class that positions a GTK4 window on screen and
  keeps it always-on-top *via gtk4-layer-shell* when available (GTK4 removed
  gtk_window_move/keep_above/type-hints). If the typelib isn't installed the
  window is still created, just not pinned to the screen (a clear warning is
  printed).

JS <-> Python bridge (WebKitGTK 6.0):
  - Python -> JS : window.sayriBridge.<fn>(...) via webview.evaluate_javascript
  - JS -> Python : window.webkit.messageHandlers.sayri.postMessage(json)
    Handler: script-message-received::sayri, arg is a JavaScriptCore.Value;
    call .to_string() to read the JSON.
"""

from __future__ import annotations

import ctypes
import json
import mimetypes
import os
import urllib.parse

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("WebKit", "6.0")

from gi.repository import Gdk, Gio, GLib, Gtk, WebKit  # noqa: E402

from . import paths  # noqa: E402

SCHEME = "sayri"


# gtk4-layer-shell: pins a toplevel to a screen edge with a margin, keeps it
# above other windows and makes the surface override-redirect (not window
# managed, so compositor blur is skipped). The GI namespace is Gtk4LayerShell.
try:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402

    LAYER_OK = True
except Exception:  # noqa: BLE001
    LayerShell = None  # type: ignore[assignment]
    LAYER_OK = False


def layer_supported() -> bool:
    return LAYER_OK and (LayerShell is not None and LayerShell.is_supported())


def pin_layer_shell(win: Gtk.Window, *, top_margin: int = 12,
                    left_margin: int | None = None,
                    right_margin: int | None = None,
                    width: int, height: int) -> bool:
    """Pin `win` via gtk4-layer-shell to the top edge, anchored to the LEFT
    (if `left_margin` given) or RIGHT (if `right_margin` given) of the screen.
    Returns True on success."""
    if not LAYER_OK:
        return False
    try:
        if not LayerShell.is_supported():
            return False
        ok = LayerShell.init_for_window(win)
        if not ok:
            return False
        LayerShell.set_layer(win, LayerShell.Layer.OVERLAY)
        LayerShell.set_anchor(win, LayerShell.Edge.TOP, True)
        if left_margin is not None:
            LayerShell.set_anchor(win, LayerShell.Edge.LEFT, True)
            LayerShell.set_margin(win, LayerShell.Edge.LEFT, left_margin)
        elif right_margin is not None:
            LayerShell.set_anchor(win, LayerShell.Edge.RIGHT, True)
            LayerShell.set_margin(win, LayerShell.Edge.RIGHT, right_margin)
        LayerShell.set_margin(win, LayerShell.Edge.TOP, top_margin)
        LayerShell.set_exclusive_zone(win, -1)
        LayerShell.set_keyboard_mode(win, LayerShell.KeyboardMode.ON_DEMAND)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[sayri] layering no aplicable: {exc}")
        return False


class _XSizeHints(ctypes.Structure):
    _fields_ = [
        ("flags", ctypes.c_long),
        ("x", ctypes.c_int),
        ("y", ctypes.c_int),
        ("width", ctypes.c_int),
        ("height", ctypes.c_int),
        ("min_width", ctypes.c_int),
        ("min_height", ctypes.c_int),
        ("max_width", ctypes.c_int),
        ("max_height", ctypes.c_int),
        ("width_inc", ctypes.c_int),
        ("height_inc", ctypes.c_int),
        ("min_aspect_x", ctypes.c_int),
        ("min_aspect_y", ctypes.c_int),
        ("max_aspect_x", ctypes.c_int),
        ("max_aspect_y", ctypes.c_int),
        ("base_width", ctypes.c_int),
        ("base_height", ctypes.c_int),
        ("win_gravity", ctypes.c_int),
    ]


class _XClientMessageEvent(ctypes.Structure):
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


class _XEvent(ctypes.Union):
    _fields_ = [
        ("type", ctypes.c_int),
        ("xclient", _XClientMessageEvent),
        ("pad", ctypes.c_long * 24),
    ]


def pin_x11_window(win: Gtk.Window, *, top_margin: int = 44,
                   right_margin: int = 16, width: int, height: int) -> bool:
    """Position an X11/XWayland window at the top-right corner, always on top."""
    try:
        libx11 = ctypes.cdll.LoadLibrary("libX11.so.6")
        libx11.XInitThreads()
        libx11.XOpenDisplay.restype = ctypes.c_void_p
        libx11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        libx11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        libx11.XFlush.argtypes = [ctypes.c_void_p]
        libx11.XDefaultRootWindow.restype = ctypes.c_ulong
        libx11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        libx11.XMoveResizeWindow.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint
        ]
        libx11.XRaiseWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        libx11.XInternAtom.restype = ctypes.c_ulong
        libx11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        libx11.XChangeProperty.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong,
            ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int
        ]
        libx11.XSetWMNormalHints.restype = None
        libx11.XSetWMNormalHints.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_XSizeHints)
        ]
        libx11.XSendEvent.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long, ctypes.POINTER(_XEvent)
        ]
        libx11.XSendEvent.restype = ctypes.c_int
    except Exception as exc:  # noqa: BLE001
        print(f"[sayri] aviso: libX11 no disponible: {exc}")
        return False

    def _apply_position(*_args) -> None:
        try:
            native = win.get_native()
            if not native:
                return
            surface = native.get_surface()
            if surface is None:
                return

            try:
                from gi.repository import GdkX11
                if not isinstance(surface, GdkX11.X11Surface):
                    return
                xid = surface.get_xid()
            except Exception:
                return

            dpy = libx11.XOpenDisplay(None)
            if not dpy:
                return

            display = Gdk.Display.get_default()
            monitors = display.get_monitors() if display else None
            mon = monitors.get_item(0) if (monitors and monitors.get_n_items() > 0) else None
            if mon:
                geom = mon.get_geometry()
                screen_x = geom.x
                screen_y = geom.y
                screen_w = geom.width
            else:
                screen_x = 0
                screen_y = 0
                screen_w = 1920

            x = max(screen_x, screen_x + screen_w - width - right_margin)
            y = screen_y + top_margin

            # 1. Set WM_NORMAL_HINTS with USPosition (1) | USSize (2) | PPosition (4) | PSize (8)
            # This tells Mutter / EWMH that the position is user-defined and prevents auto-centering.
            hints = _XSizeHints()
            hints.flags = (1 << 0) | (1 << 1) | (1 << 2) | (1 << 3)
            hints.x = x
            hints.y = y
            hints.width = width
            hints.height = height
            libx11.XSetWMNormalHints(dpy, xid, ctypes.byref(hints))

            # 2. Direct MoveResize
            libx11.XMoveResizeWindow(dpy, xid, x, y, width, height)

            XA_ATOM = 4
            net_wm_state = libx11.XInternAtom(dpy, b"_NET_WM_STATE", 0)
            state_above = libx11.XInternAtom(dpy, b"_NET_WM_STATE_ABOVE", 0)
            state_sticky = libx11.XInternAtom(dpy, b"_NET_WM_STATE_STICKY", 0)
            state_skip_tb = libx11.XInternAtom(dpy, b"_NET_WM_STATE_SKIP_TASKBAR", 0)
            state_skip_pg = libx11.XInternAtom(dpy, b"_NET_WM_STATE_SKIP_PAGER", 0)

            states = (ctypes.c_ulong * 4)(state_above, state_sticky, state_skip_tb, state_skip_pg)
            libx11.XChangeProperty(
                dpy, xid, net_wm_state, XA_ATOM, 32, 2,
                ctypes.cast(states, ctypes.c_void_p), 4
            )

            # Set DOCK / NOTIFICATION window type so Mutter pins it in the top Overlay layer
            net_wm_wtype = libx11.XInternAtom(dpy, b"_NET_WM_WINDOW_TYPE", 0)
            wtype_dock = libx11.XInternAtom(dpy, b"_NET_WM_WINDOW_TYPE_DOCK", 0)
            wtype_notification = libx11.XInternAtom(dpy, b"_NET_WM_WINDOW_TYPE_NOTIFICATION", 0)
            wtype_utility = libx11.XInternAtom(dpy, b"_NET_WM_WINDOW_TYPE_UTILITY", 0)
            wtypes = (ctypes.c_ulong * 3)(wtype_dock, wtype_notification, wtype_utility)
            libx11.XChangeProperty(
                dpy, xid, net_wm_wtype, XA_ATOM, 32, 2,
                ctypes.cast(wtypes, ctypes.c_void_p), 3
            )

            # Send EWMH ClientMessage to Root Window to notify Mutter window manager
            root = libx11.XDefaultRootWindow(dpy)
            mask = 0x00100000 | 0x00080000  # SubstructureRedirectMask | SubstructureNotifyMask

            ev = _XEvent()
            ev.type = 33  # ClientMessage
            ev.xclient.type = 33
            ev.xclient.serial = 0
            ev.xclient.send_event = 1
            ev.xclient.display = dpy
            ev.xclient.window = xid
            ev.xclient.message_type = net_wm_state
            ev.xclient.format = 32
            ev.xclient.data_l[0] = 1  # _NET_WM_STATE_ADD
            ev.xclient.data_l[1] = state_above
            ev.xclient.data_l[2] = state_sticky
            ev.xclient.data_l[3] = 1
            ev.xclient.data_l[4] = 0
            libx11.XSendEvent(dpy, root, 0, mask, ctypes.byref(ev))

            # Send _NET_MOVERESIZE_WINDOW message
            net_moveresize = libx11.XInternAtom(dpy, b"_NET_MOVERESIZE_WINDOW", 0)
            ev_mr = _XEvent()
            ev_mr.type = 33
            ev_mr.xclient.type = 33
            ev_mr.xclient.serial = 0
            ev_mr.xclient.send_event = 1
            ev_mr.xclient.display = dpy
            ev_mr.xclient.window = xid
            ev_mr.xclient.message_type = net_moveresize
            ev_mr.xclient.format = 32
            # flags: 0x0F00 = x, y, width, height specified, source = 1 (normal application)
            ev_mr.xclient.data_l[0] = 0x0F00 | (1 << 12)
            ev_mr.xclient.data_l[1] = x
            ev_mr.xclient.data_l[2] = y
            ev_mr.xclient.data_l[3] = width
            ev_mr.xclient.data_l[4] = height
            libx11.XSendEvent(dpy, root, 0, mask, ctypes.byref(ev_mr))

            libx11.XRaiseWindow(dpy, xid)
            libx11.XFlush(dpy)
            libx11.XCloseDisplay(dpy)
        except Exception as exc:  # noqa: BLE001
            print(f"[sayri] aviso al posicionar ventana X11: {exc}")

    win.connect("realize", lambda *_: GLib.idle_add(_apply_position))
    win.connect("map", lambda *_: (GLib.idle_add(_apply_position), GLib.timeout_add(100, _apply_position)))
    setattr(win, "_reassert_x11_position", _apply_position)
    return True


def pin_window(win: Gtk.Window, *, top_margin: int = 44,
               right_margin: int = 16, width: int, height: int) -> bool:
    """Pin `win` to the top-right corner via layer-shell or X11/XWayland."""
    display = Gdk.Display.get_default()
    if display and "Wayland" in type(display).__name__:
        if layer_supported():
            return pin_layer_shell(win, top_margin=top_margin, right_margin=right_margin,
                                   width=width, height=height)
    return pin_x11_window(win, top_margin=top_margin, right_margin=right_margin,
                          width=width, height=height)


_scheme_registered = False


def ensure_scheme(ctx: WebKit.WebContext) -> None:
    """Register the sayri:// scheme on a WebKit context (once only)."""
    global _scheme_registered
    if not _scheme_registered:
        ctx.register_uri_scheme(SCHEME, _serve_uri)
        _scheme_registered = True


def webview_setup(web: WebKit.WebView, on_message=None) -> WebKit.UserContentManager:
    """Configure the WebKit6 view: bridge, JS error capture.
    If `on_message(text)` is given, all JS->Python messages go there.
    Otherwise falls back to the _ACTIVE dict routing.
    Returns the UserContentManager.
    """
    ensure_scheme(web.get_context())

    ucm = web.get_user_content_manager()
    ucm.register_script_message_handler("sayri")

    def _handler(_ucm, value):
        try:
            text = value.to_string()
        except Exception:  # noqa: BLE001
            return
        if on_message:
            on_message(text)
        else:
            owner = _ACTIVE.get(_ucm)
            if owner is not None:
                owner._on_host_message(text)

    ucm.connect("script-message-received::sayri", _handler)

    err_script = WebKit.UserScript.new(
        """
        window.addEventListener('error', function (e) {
          try {
            window.webkit.messageHandlers.sayri.postMessage(JSON.stringify(
              {type:'error', message: String(e.message || e.error || 'unknown')}));
          } catch (_) {}
        });
        window.addEventListener('unhandledrejection', function (e) {
          try {
            window.webkit.messageHandlers.sayri.postMessage(JSON.stringify(
              {type:'error', message: String(e.reason)}));
          } catch (_) {}
        });
        """,
        WebKit.UserContentInjectedFrames.ALL_FRAMES,
        WebKit.UserScriptInjectionTime.START,
        None,
    )
    ucm.add_script(err_script)

    web.set_background_color(Gdk.RGBA(0, 0, 0, 0))
    return ucm


def _serve_uri(request: WebKit.URISchemeRequest) -> None:
    uri = request.get_uri()
    path = urllib.parse.urlparse(uri).path.lstrip("/")
    if not path:
        path = "index.html"
    full = os.path.join(paths.data_dir(), path)
    if os.path.isfile(full):
        try:
            with open(full, "rb") as fh:
                body = fh.read()
            if full.endswith(".wasm"):
                ctype = "application/wasm"
            elif full.endswith(".js"):
                ctype = "application/javascript"
            elif full.endswith(".json"):
                ctype = "application/json"
            elif full.endswith(".html"):
                ctype = "text/html; charset=utf-8"
            elif full.endswith(".svg"):
                ctype = "image/svg+xml"
            else:
                ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(body))
            request.finish(stream, len(body), ctype)
            return
        except Exception as exc:  # noqa: BLE001
            print(f"[sayri] error sirviendo {uri}: {exc}")
    print(f"[sayri] 404 No encontrado: {uri} (buscado en {full})")
    request.finish_error(
        GLib.Error.new_literal(GLib.quark_from_string("sayri"), f"not found: {path}", 404)
    )


def _on_script_message(_ucm, value) -> None:
    """Bridges a JS message up to the owning window.

    The registered handler routes to the currently-active WebWindow via the
    module registry set up in WebWindow.__init__. Uses the UCM object itself
    as the dict key (GI objects hash by C pointer, so different Python wrappers
    for the same GObject resolve to the same key).
    """
    try:
        text = value.to_string()
    except Exception:  # noqa: BLE001
        return
    owner = _ACTIVE.get(_ucm)
    if owner is not None:
        owner._on_host_message(text)


_ACTIVE: dict = {}


class WebWindow:
    """Base GTK4 + WebKit6 + optional layer-shell window."""

    #: routing key for which window / mode the bundle was loaded in
    mode: str = "orb"
    size: tuple[int, int] = (220, 220)

    def __init__(self, app) -> None:
        self.app = app
        self.cfg = app.cfg

        # GTK4/WebKit on Wayland needs a Gtk.ApplicationWindow to realize the
        # surface (a plain Gtk.Window never maps). The real SayriApp is a
        # Gtk.Application; tests pass a plain stub, which falls back to Window.
        if isinstance(app, Gtk.Application):
            self.win = Gtk.ApplicationWindow(application=app)
        else:
            self.win = Gtk.Window()
        self.win.set_default_size(*self.size)
        self.win.set_resizable(False)
        self.win.set_decorated(False)
        # CSS so the window stays fully transparent.
        self.win.add_css_class("sayri-window")

        self.web = WebKit.WebView.new()
        webview_setup(self.web)
        self.win.set_child(self.web)

        # route incoming JS messages to this instance
        ucm = self.web.get_user_content_manager()
        _ACTIVE[id(ucm)] = self

        self.web.connect("web-process-terminated", self._on_crash)
        self.web.connect("load-failed", self._on_load_failed)

        self._ready = False
        self._pending_state: list[tuple[str, dict]] = []

        GLib.timeout_add(15000, self._warn_if_not_ready)
        self._warned = False

    # ------------------------------------------------------------ utils
    def _load(self, mode: str) -> None:
        self.mode = mode
        self.web.load_uri(f"{SCHEME}://app/index.html?mode={mode}")

    def evaluate(self, js: str, cb=None) -> None:
        self.web.evaluate_javascript(js, len(js), None, f"{SCHEME}://app",
                                     None, cb, None)

    def evaluate_result_text(self, js: str, on_value) -> None:
        def _cb(web, result, _u=None):
            try:
                v = web.evaluate_javascript_finish(result).to_string()
            except Exception as exc:  # noqa: BLE001
                v = f"ERR:{exc}"
            on_value(v)
        self.evaluate(js, _cb)

    def call(self, method: str, *args) -> None:
        """Call a method on window.sayriBridge, JSON-encoding string args."""
        parts = [json.dumps(a) for a in args]
        self.evaluate(f"window.sayriBridge && window.sayriBridge.{method}({', '.join(parts)})")

    def set_state_sync(self, state: str, opts: dict | None = None) -> None:
        """Set the visual state on the JS bridge (queue until ready)."""
        opts = opts or {}
        if not self._ready:
            self._pending_state.append((state, opts))
            return
        js = (f"window.sayriBridge && window.sayriBridge.setState("
              f"{json.dumps(state)}, {json.dumps(opts)})")
        self.evaluate(js)

    def _on_host_message(self, text: str) -> None:
        try:
            msg = json.loads(text)
        except Exception:  # noqa: BLE001
            return
        kind = msg.get("type")
        if kind == "ready":
            self._ready = True
            self._warned = True
            for state, opts in self._pending_state:
                self.set_state_sync(state, opts)
            self._pending_state = []
            self.on_ready()
        elif kind == "error":
            print(f"[sayri] error JS en {self.mode}: {msg.get('message')}")
        else:
            self.on_host_message(msg)

    # --------------------------------------------------- hooks (overridable)
    def on_ready(self) -> None:
        pass

    def on_host_message(self, msg: dict) -> None:
        pass

    # ------------------------------------------------------------- misc
    _TERMINATION = {
        0: "crashed",
        1: "killed",
        2: "exceeded memory limit",
        3: "jailed/crashed by sandbox",
    }

    def _on_crash(self, _web, event) -> None:
        reason = self._TERMINATION.get(int(event), str(event))
        print(
            "[sayri] aviso: el proceso web de WebKit terminó "
            f"({reason}). Si {self.mode} no aparece, prueba a ejecutar con "
            "WEBKIT_DISABLE_DMABUF_RENDERER=1 o WEBKIT_DISABLE_COMPOSITING_MODE=1."
        )
        GLib.timeout_add(1000, self.web.reload)

    def _on_load_failed(self, _web, _event, uri, err) -> bool:
        print(f"[sayri] no se pudo cargar {uri}: {err.message}")
        return True

    def _warn_if_not_ready(self) -> bool:
        if not self._ready:
            print(
                "[sayri] aviso: la ventana " + self.mode +
                " no ha cargado en 15 s. Revisa que SAYRI_DATA_DIR apunte al "
                "build web y que WebKitGTK funcione "
                "(prueba WEBKIT_DISABLE_COMPOSITING_MODE=1)."
            )
            self._warned = True
        return False

    def show(self) -> None:
        self.win.present()

    def hide(self) -> None:
        self.win.set_visible(False)

    def is_visible(self) -> bool:
        return self.win.get_visible()


def primary_geometry() -> tuple[int, int, int, int]:
    """Return (x, y, w, h) of the primary/active monitor (first, on the
    display the app is on)."""
    try:
        display = Gdk.Display.get_default()
        if display is None:
            return (0, 0, 1920, 1080)
        monitors = display.get_monitors()
        mon = None
        if monitors and monitors.get_n_items() > 0:
            mon = monitors.get_item(0)
        if mon is None:
            return (0, 0, 1920, 1080)
        g = mon.get_geometry()
        return g.x, g.y, g.width, g.height
    except Exception:  # noqa: BLE001
        return (0, 0, 1920, 1080)