"""GTK4 + WebKit window that hosts a xui wizard over the ``sayri://`` bridge.

The welcome wizard (and any plugin wizard using the same xui framework) renders
in a normal GTK window: the HTML page is served from
``$SAYRI_STATE_DIR/xui/<id>.html`` via the ``sayri://`` scheme and the JS
posts the exact same xui events a terminal user would send. The host logic
lives once in :mod:`sayri.wizard` (or in the plugin's host class) and this
window is just another transport.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from . import wizard
from .webkit import WebWindow, SCHEME
from .wizard import WelcomeApp


class XuiWindow(WebWindow):
    """A decorated, resizable GTK window driving a xui host over WebKit."""

    mode = "welcome"
    size = (720, 620)
    page = "welcome.html"

    def __init__(self, app: Any = None, host: Optional[WelcomeApp] = None,
                 page: Optional[str] = None) -> None:
        super().__init__(app)
        self.host: Any = host if host is not None else WelcomeApp()
        self.page = page or self.page

        self.win.set_default_size(*self.size)
        self.win.set_resizable(True)
        self.win.set_decorated(True)
        self.win.set_title("Sayri · Welcome")

    def load(self) -> None:
        self.web.load_uri(f"{SCHEME}://app/xui/{self.page}")

    def present(self) -> None:
        self.win.present()

    # ------------------------------------------------------------ host hooks
    def on_ready(self) -> None:
        self._push()

    def on_host_message(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "xui_ready":
            self._push()
            return
        if kind == "error":
            print(f"[sayri] JS error in xui window: {msg.get('message')}")
            return
        # Any xui event (action / submit / change / poll) goes to the host.
        try:
            scr = self.host.dispatch(msg)
        except Exception as exc:  # noqa: BLE001 - never kill WebKit on a host bug
            import traceback
            traceback.print_exc()
            screen = {
                "id": "host_error", "title": "Error", "subtitle": "",
                "step": "", "busy": False, "done": True,
                "body": [{"t": "note", "text": f"{exc}", "level": "error"}],
                "footer": [{"t": "button", "id": "quit", "label": "Cerrar", "kind": "secondary"}],
            }
            self._push(screen)
            return
        if scr is None:  # host finished → close the window
            self.win.close()
            return
        self._push(scr)

    def _push(self, scr: Optional[dict] = None) -> None:
        if scr is None:
            try:
                scr = self.host.render()
            except Exception:  # noqa: BLE001
                return
        if scr is None:
            self.win.close()
            return
        js = "window.xui && window.xui.render(" + json.dumps(scr, ensure_ascii=False) + ")"
        self.evaluate(js)


def open_wizard(app: Any = None, host: Optional[WelcomeApp] = None) -> XuiWindow:
    """Ensure the wizard page is on disk, then open the wizard window."""
    try:
        wizard.install_html_to()
    except Exception as exc:  # noqa: BLE001 - a stale page still loads
        print(f"[sayri] warning: could not write wizard page: {exc}")
    win = XuiWindow(app, host=host)
    win.load()
    win.show()
    return win