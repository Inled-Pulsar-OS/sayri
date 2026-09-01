"""Pure GTK3 Tray AppIndicator for Sayri.

Provides a permanent system tray indicator with official Siri iOS 2021 icon,
fast toggle, settings launcher, and clean lifecycle management.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

try:
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator
except Exception:
    try:
        gi.require_version("AppIndicator3", "0.1")
        from gi.repository import AppIndicator3 as AppIndicator
    except Exception:
        AppIndicator = None

from sayri import config, paths

Gtk.init(sys.argv)


def _ensure_local_icon() -> None:
    user_icons = os.path.expanduser("~/.local/share/icons/hicolor")
    src = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "icons", "hicolor", "256x256", "apps", "sayri-tray.png"))
    if os.path.isfile(src):
        for sz in ["256x256", "scalable", "48x48", "32x32"]:
            d = os.path.join(user_icons, sz, "apps")
            os.makedirs(d, exist_ok=True)
            dest = os.path.join(d, "sayri-tray.png")
            if not os.path.exists(dest) or os.path.getsize(dest) != os.path.getsize(src):
                try:
                    shutil.copy2(src, dest)
                except OSError:
                    pass


def send_sock_command(cmd: str) -> bool:
    sock_path = os.path.join(paths.state_dir(), "sayri.sock")
    if not os.path.exists(sock_path):
        return False
    try:
        import socket
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(1.0)
        s.connect(sock_path)
        s.sendall(f"{cmd}\n".encode("utf-8"))
        s.recv(1024)
        s.close()
        return True
    except Exception:
        return False


class SayriIndicator:
    def __init__(self) -> None:
        self.cfg = config.config
        self._sayri_proc: subprocess.Popen | None = None
        self._settings_proc: subprocess.Popen | None = None
        _ensure_local_icon()

        self._create_menu()
        self._create_indicator()

    def _create_menu(self) -> None:
        self.menu = Gtk.Menu()

        # When the user clicks the tray icon, immediately toggle Sayri and dismiss the menu popup
        self.menu.connect("show", lambda m: (self._on_toggle_sayri(), GLib.idle_add(m.popdown)))

        self.toggle_item = Gtk.MenuItem(label="Sayri")
        self.toggle_item.connect("activate", self._on_toggle_sayri)
        self.menu.append(self.toggle_item)

        self.menu.show_all()

    def _create_indicator(self) -> None:
        if AppIndicator is not None:
            self.indicator = AppIndicator.Indicator.new(
                "sayri-indicator",
                "sayri-tray",
                AppIndicator.IndicatorCategory.APPLICATION_STATUS,
            )
            self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
            self.indicator.set_title("Sayri")
            self.indicator.set_menu(self.menu)
            self.indicator.set_secondary_activate_target(self.toggle_item)
        else:
            self.status_icon = Gtk.StatusIcon.new_from_icon_name("sayri-tray")
            self.status_icon.set_tooltip_text("Sayri Voice Assistant")
            self.status_icon.connect("popup-menu", lambda _i, btn, time: self.menu.popup(None, None, None, None, btn, time))
            self.status_icon.connect("activate", lambda _i: self._on_toggle_sayri(None))

    def _on_toggle_sayri(self, _item=None) -> None:
        if not send_sock_command("toggle"):
            self._ensure_sayri()

    def _on_listen(self, _item=None) -> None:
        if not send_sock_command("listen"):
            self._ensure_sayri()

    def _ensure_sayri(self) -> None:
        if self._sayri_proc and self._sayri_proc.poll() is None:
            return
        env = dict(os.environ)
        lib_path = os.path.dirname(os.path.dirname(__file__))
        env["PYTHONPATH"] = lib_path + (":" + env["PYTHONPATH"] if "PYTHONPATH" in env else "")
        self._sayri_proc = subprocess.Popen([sys.executable, "-m", "sayri"], env=env)

    def _on_open_settings(self, _item=None) -> None:
        if self._settings_proc and self._settings_proc.poll() is None:
            return
        env = dict(os.environ)
        lib_path = os.path.dirname(os.path.dirname(__file__))
        env["PYTHONPATH"] = lib_path + (":" + env["PYTHONPATH"] if "PYTHONPATH" in env else "")
        env.pop("LD_PRELOAD", None)
        self._settings_proc = subprocess.Popen([sys.executable, "-m", "sayri.settings_gtk3"], env=env)

    def _on_quit(self, _item=None) -> None:
        send_sock_command("quit")
        if self._sayri_proc and self._sayri_proc.poll() is None:
            self._sayri_proc.terminate()
        if self._settings_proc and self._settings_proc.poll() is None:
            self._settings_proc.terminate()
        Gtk.main_quit()


def main() -> None:
    app = SayriIndicator()
    Gtk.main()


if __name__ == "__main__":
    main()
