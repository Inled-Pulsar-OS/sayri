"""Modern GTK4 Cajita Settings Window for Sayri.

Opens the full-featured Cajita Preferences & Extensions manager (Settings,
Plugins/Gateways/UIs, Subagents, Secrets Vault, Whisper/Piper Downloaders, History).
"""

from __future__ import annotations

import os
import socket
import sys
import threading
from typing import Any, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")

from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from sayri import blur_exclusion, config, paths, sound  # noqa: E402
from sayri.adapters.storage.sqlite_sessions import SQLiteSessionRepository  # noqa: E402
from sayri.cajita import SayriCajita  # noqa: E402
from sayri.domain.agent_creator import AgentCreator  # noqa: E402
from sayri.domain.models import AgentProfile  # noqa: E402
from sayri.ipc import SayriClient  # noqa: E402


class StandaloneAppBridge:
    """Provides the runtime environment and daemon bridge for SayriCajita in standalone window."""

    def __init__(self, window: Gtk.Window) -> None:
        self.win = window
        self.cfg = config.config
        self.storage = SQLiteSessionRepository()
        self.active_agent: AgentProfile = AgentCreator.get_agent("default") or AgentProfile(
            id="default",
            name="Main Sayri",
            description="Personal AI assistant and autonomous agent",
            system_prompt="You are Sayri, the personal AI assistant of the user.",
        )
        self.overlay = None
        self._client: Optional[SayriClient] = None
        self._connect_daemon()

    def _connect_daemon(self) -> None:
        try:
            client = SayriClient()
            if client.connect():
                self._client = client
        except Exception:
            self._client = None

    def listening_now(self) -> bool:
        return False

    def toggle_listening(self) -> None:
        if self._client:
            try:
                self._client.call("toggle_listening")
            except Exception:
                pass

    def new_conversation(self) -> None:
        self.storage.create_session(agent_id=self.active_agent.id)

    def switch_session(self, session_id: str) -> None:
        sess = self.storage.get_session(session_id)
        if sess:
            agent = AgentCreator.get_agent(sess.agent_id) or self.active_agent
            self.active_agent = agent

    def _apply_mode(self, mode: str) -> None:
        pass

    def send_text(self, text: str, image_path: Optional[str] = None) -> None:
        if self._client:
            try:
                self._client.call("ask", {"text": text, "image_path": image_path})
            except Exception:
                pass


SETTINGS_CSS = b"""
window.sayri-settings-cajita-window {
    background-color: #0b1120;
}

.sayri-standalone-frame {
    background: transparent;
    padding: 12px;
}
"""


class CajitaSettingsWindow(Gtk.ApplicationWindow):
    """Standalone window embedding the Apple-Intelligence Cajita settings interface."""

    def __init__(self, app: Gtk.Application, initial_tab: str = "settings") -> None:
        super().__init__(application=app)
        self.set_title("Sayri - Ajustes y Configuración")
        self.set_default_size(480, 680)
        self.set_resizable(True)
        self.add_css_class("sayri-settings-cajita-window")

        # Apply CSS
        try:
            provider = Gtk.CssProvider()
            provider.load_from_data(SETTINGS_CSS)
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(),
                provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )
        except Exception:
            pass

        # Apply blur exclusion
        try:
            blur_exclusion.apply_blur_exclusion()
        except Exception:
            pass

        # Build bridge and Cajita widget
        self.bridge = StandaloneAppBridge(self)
        self.cajita = SayriCajita(self.bridge)

        # Ensure card view is open and expands to fill window
        self.cajita.card_overlay.set_visible(True)
        self.cajita.card_overlay.set_hexpand(True)
        self.cajita.card_overlay.set_vexpand(True)
        self.cajita.set_hexpand(True)
        self.cajita.set_vexpand(True)

        # Switch to requested tab
        self.cajita.switch_tab(initial_tab, trigger_effect=False)

        # Root box
        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        container.add_css_class("sayri-standalone-frame")
        container.set_hexpand(True)
        container.set_vexpand(True)
        container.append(self.cajita)

        self.set_child(container)


def _forward_to_running_overlay() -> bool:
    """If Sayri GTK overlay is already running, send IPC signal and return True."""
    sock_path = os.path.join(paths.state_dir(), "sayri.sock")
    if not os.path.exists(sock_path):
        return False

    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(0.5)
        s.connect(sock_path)
        s.sendall(b"sayri-gui-ping\n")
        reply = s.recv(64).strip().upper()
        s.close()
        if reply.startswith(b"GUI"):
            s2 = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s2.settimeout(1.0)
            s2.connect(sock_path)
            s2.sendall(b"settings\n")
            s2.recv(1024)
            s2.close()
            return True
    except OSError:
        pass
    return False


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    # Check if we should switch to a specific tab
    initial_tab = "settings"
    if "--plugins" in argv or "-p" in argv:
        initial_tab = "plugins"
    elif "--gateways" in argv or "-g" in argv:
        initial_tab = "plugins"
    elif "--subagents" in argv or "-a" in argv:
        initial_tab = "subagents"
    elif "--vault" in argv or "--secrets" in argv:
        initial_tab = "secrets"
    elif "--history" in argv:
        initial_tab = "history"

    # If the main Sayri overlay is already active on screen, focus its settings
    if _forward_to_running_overlay():
        return 0

    # Ensure blur exclusion rules are registered with GNOME Shell
    blur_exclusion.apply_blur_exclusion()

    app = Gtk.Application(application_id="es.inled.sayri.settings")

    def _on_activate(gtk_app: Gtk.Application) -> None:
        win = CajitaSettingsWindow(gtk_app, initial_tab=initial_tab)
        win.present()

    app.connect("activate", _on_activate)
    return app.run([])


if __name__ == "__main__":
    sys.exit(main())
