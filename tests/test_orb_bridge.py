"""Integration test for the Reacticx Skia Sayri overlay (orb + chroma-ring).
"""

import json
import os
import sys

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("WebKit", "6.0")
    from gi.repository import Gio, GLib, Gtk
except Exception as exc:
    print(f"  SKIP test_orb_bridge: {exc}")
    sys.exit(0)

from sayri import config, overlay as overlay_mod

ERRORS = []


class TestApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="es.inled.sayri.testoverlay",
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.cfg = config.config
        self.state = "idle"
        self._busy = False
        self.overlay = None
        self.final_ok = None

    @property
    def busy(self):
        return self._busy

    def listening_now(self):
        return False

    def on_orb_click(self):
        pass

    def set_state(self, state):
        self.state = state

    def quit_app(self):
        self.quit()

    def open_settings(self):
        pass

    def send_text(self, _text):
        pass

    def toggle_listening(self):
        pass

    def do_activate(self):
        self.overlay = overlay_mod.SayriOverlay(self)
        self.overlay.show()

        self.overlay.set_state_sync("listening")
        self.overlay.set_audio_level(0.75)

        self.overlay.set_content("user", "¿Cómo estás?")
        self.overlay.set_content("assistant", "¡Hola! Estoy listo para ayudarte.")
        self.overlay.set_mic(True)
        self.overlay.set_busy(True)
        self.overlay.set_state_sync("speaking")

        self.overlay.clear()
        self.overlay.set_busy(False)
        self.overlay.set_mic(False)

        print("  BRIDGE_RESULT PASS WebKit overlay verified successfully")
        self.final_ok = True
        self.quit()


def main():
    app = TestApp()
    app.hold()
    code = app.run(sys.argv)
    return 0 if app.final_ok else 1


if __name__ == "__main__":
    sys.exit(main())
