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
from typing import Optional

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

from sayri import config, paths, sysinfo

Gtk.init(sys.argv)


def _icon_source() -> Optional[str]:
    """Find the tray icon in the layouts Sayri is installed and run from.

    indicator.py lives at <root>/share/sayri/lib/sayri/indicator.py, and the
    icons sit in <root>/share/icons, so the directory to climb out to is the
    ``share`` one. Only one level too few was enough to make this silently find
    nothing, which left the tray entry with no icon at all and no error.
    Several candidates are tried because the same tree is used installed
    (/usr/share/sayri) and in a checkout (…/PKG/sayri/usr/share/sayri).
    """
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        # Installed: /usr/share/sayri/lib/sayri -> /usr/share/icons
        os.path.abspath(os.path.join(here, "..", "..", "..", "icons")),
        # Checked out: …/usr/share/sayri/lib/sayri -> …/usr/share/icons
        os.path.abspath(os.path.join(here, "..", "..", "icons")),
        # Already installed system-wide.
        "/usr/share/icons",
    ]
    for base in candidates:
        path = os.path.join(base, "hicolor", "256x256", "apps", "sayri-tray.png")
        if os.path.isfile(path):
            return path
        path = os.path.join(base, "hicolor", "scalable", "apps", "sayri-tray.png")
        if os.path.isfile(path):
            return path
    return None


def _ensure_local_icon() -> None:
    user_icons = os.path.expanduser("~/.local/share/icons/hicolor")
    src = _icon_source()
    if src:
        for sz in ["256x256", "scalable", "48x48", "32x32"]:
            d = os.path.join(user_icons, sz, "apps")
            os.makedirs(d, exist_ok=True)
            dest = os.path.join(d, "sayri-tray.png")
            if not os.path.exists(dest) or os.path.getsize(dest) != os.path.getsize(src):
                try:
                    shutil.copy2(src, dest)
                except OSError:
                    pass
    else:
        print("[Sayri] tray icon not found; the indicator will have no icon")


def is_gui_running() -> bool:
    sock_path = os.path.join(paths.state_dir(), "sayri.sock")
    if not os.path.exists(sock_path):
        return False
    try:
        import socket
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(0.5)
        s.connect(sock_path)
        s.sendall(b"sayri-gui-ping\n")
        reply = s.recv(64).strip().upper()
        s.close()
        return reply.startswith(b"GUI")
    except Exception:
        return False


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


def send_daemon_command(cmd: str) -> bool:
    """Talk to the daemon control socket (``sayri-daemon.sock``).

    ``send_sock_command`` only reaches the desktop UI process, which may not be
    running at all. Anything that must survive regardless of which front-end
    happens to be open (Exit, killall) has to go through the daemon instead.
    """
    try:
        from sayri import ipc
        client = ipc.SayriClient()
        try:
            if not client.connect(timeout=1.5):
                return False
            client.request(cmd, timeout=3.0)
            return True
        finally:
            client.close()
    except Exception:
        return False


def _daemon_call(cmd: str, params: Optional[dict] = None, timeout: float = 3.0) -> bool:
    """Send one command to the daemon, with parameters.

    send_daemon_command covers the argument-less cases; the ui_* and killall
    commands need a payload, so they go through here.
    """
    try:
        from sayri import ipc
        client = ipc.SayriClient()
        try:
            if not client.connect(timeout=1.5):
                return False
            client.request(cmd, params or {}, timeout=timeout)
            return True
        finally:
            client.close()
    except Exception:
        return False


class SayriIndicator:
    """The tray entry.

    With ``ui_id`` it belongs to a companion (Clippy, Bonzi, …) rather than to
    the main window, and the menu is about that companion: restart it, open its
    settings, or take the whole of Sayri down. The companion's window is a GTK4
    layer-shell webview and cannot host an AppIndicator itself, so the plugin
    runs this as a child process and the two agree to shut down together.
    """

    def __init__(self, ui_id: str = "", title: str = "") -> None:
        self.cfg = config.config
        self.ui_id = str(ui_id or "").strip()
        self.title = str(title or "").strip() or "Sayri"
        self._sayri_proc: subprocess.Popen | None = None
        self._settings_proc: subprocess.Popen | None = None
        _ensure_local_icon()

        self._create_menu()
        self._create_indicator()

    def _create_menu(self) -> None:
        self.menu = Gtk.Menu()

        if self.ui_id:
            restart = Gtk.MenuItem(label="Restart " + self.title)
            restart.connect("activate", lambda _i: self._on_restart_ui())
            self.menu.append(restart)
        else:
            self.toggle_item = Gtk.MenuItem(label="Sayri")
            self.toggle_item.connect("activate", self._on_toggle_sayri)
            self.menu.append(self.toggle_item)

        settings = Gtk.MenuItem(label="Settings")
        settings.connect("activate", self._on_open_settings)
        self.menu.append(settings)

        sep = Gtk.SeparatorMenuItem()
        self.menu.append(sep)

        # One click to take everything down, which is what people reach for
        # when the companion is stuck or in the way.
        kill = Gtk.MenuItem(label="Terminate all Sayri processes")
        kill.connect("activate", self._on_kill_all)
        self.menu.append(kill)

        self.quit_item = Gtk.MenuItem(label="Exit")
        self.quit_item.connect("activate", self._on_quit)
        self.menu.append(self.quit_item)

        self.menu.show_all()

    def _create_indicator(self) -> None:
        if AppIndicator is not None:
            self.indicator = AppIndicator.Indicator.new(
                "sayri-indicator" + ("-" + self.ui_id if self.ui_id else ""),
                "sayri-tray",
                AppIndicator.IndicatorCategory.APPLICATION_STATUS,
            )
            self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
            self.indicator.set_title(self.title)
            self.indicator.set_menu(self.menu)
            # A companion's menu has no "toggle" entry, so a left click just
            # opens the menu there instead of aiming at a missing item.
            if self.ui_id:
                try:
                    self.indicator.connect("activate", lambda i, _x, _y: i.props.menu.popup(None, None, None, None, 0, 0))
                except Exception:
                    pass
            else:
                self.indicator.set_secondary_activate_target(self.toggle_item)
                try:
                    self.indicator.connect("activate", lambda _i, _x, _y: self._on_toggle_sayri())
                except Exception:
                    pass
        else:
            self.status_icon = Gtk.StatusIcon.new_from_icon_name("sayri-tray")
            self.status_icon.set_tooltip_text(self.title)
            self.status_icon.connect("popup-menu", lambda _i, btn, time: self.menu.popup(None, None, None, None, btn, time))
            if not self.ui_id:
                self.status_icon.connect("activate", lambda _i: self._on_toggle_sayri(None))

    def _on_toggle_sayri(self, _item=None) -> None:
        if is_gui_running():
            send_sock_command("toggle")
        else:
            self._ensure_sayri()

    def _on_listen(self, _item=None) -> None:
        if is_gui_running():
            send_sock_command("listen")
        else:
            self._ensure_sayri()

    def _ensure_sayri(self) -> None:
        if self._sayri_proc and self._sayri_proc.poll() is None:
            if is_gui_running():
                send_sock_command("show")
                return
        env = dict(os.environ)
        lib_path = os.path.dirname(os.path.dirname(__file__))
        env["PYTHONPATH"] = lib_path + (":" + env["PYTHONPATH"] if "PYTHONPATH" in env else "")
        self._sayri_proc = subprocess.Popen([sys.executable, "-m", "sayri", "--toggle"], env=env)

    def _on_open_settings(self, _item=None) -> None:
        if self._settings_proc and self._settings_proc.poll() is None:
            return
        env = dict(os.environ)
        lib_path = os.path.dirname(os.path.dirname(__file__))
        env["PYTHONPATH"] = lib_path + (":" + env["PYTHONPATH"] if "PYTHONPATH" in env else "")
        env.pop("LD_PRELOAD", None)
        self._settings_proc = subprocess.Popen([sys.executable, "-m", "sayri.settings_cajita"], env=env)

    def _on_restart_ui(self) -> None:
        """Restart the companion this indicator belongs to.

        A stop followed by a start, both through the daemon so the plugin is
        launched the way it would be from the command line. The wait between
        them is the plugin's own shutdown window: starting while the old one
        still holds the pid file leaves two companions fighting over it.
        """
        import time

        def _run() -> None:
            for cmd in ("ui_stop", "ui_start"):
                if not _daemon_call(cmd, {"ui_id": self.ui_id}, 10.0):
                    return
                if cmd == "ui_stop":
                    time.sleep(1.5)
        threading.Thread(target=_run, daemon=True).start()

    def _on_kill_all(self) -> None:
        """Take every Sayri process down, the daemon excepted.

        The daemon is deliberately left running: it is the background service
        and it is what would start things again. The companion that owns this
        indicator goes with everything else, so this process has to go too.
        """
        def _run() -> None:
            _daemon_call("killall", {"include_daemon": False}, 8.0)
            if self._sayri_proc and self._sayri_proc.poll() is None:
                self._sayri_proc.terminate()
            if self._settings_proc and self._settings_proc.poll() is None:
                self._settings_proc.terminate()
            Gtk.main_quit()
        threading.Thread(target=_run, daemon=True).start()

    def _on_quit(self, _item=None) -> None:
        # "Exit" has to mean "Sayri is gone", not "the tray icon is gone".
        # The daemon owns the companion windows (its core.shutdown() stops
        # every UI plugin), so ask it first through its own control socket:
        # sayri.sock may belong to the desktop UI process instead of the
        # daemon, and a double "quit" would shut the daemon down twice.
        if send_daemon_command("quit"):
            pass
        elif not send_sock_command("quit"):
            # No daemon and no UI process answered, so nobody would clean up
            # the companions. Do it here rather than leave windows stranded.
            try:
                stopped = sysinfo.stop_ui_plugins()
                if stopped:
                    print(f"[Sayri] Stopped UI plugins: {', '.join(stopped)}")
            except Exception as exc:
                print(f"[Sayri] UI plugin shutdown notice: {exc}")
        if self._sayri_proc and self._sayri_proc.poll() is None:
            self._sayri_proc.terminate()
        if self._settings_proc and self._settings_proc.poll() is None:
            self._settings_proc.terminate()
        Gtk.main_quit()


def main(argv: Optional[list] = None) -> None:
    """Run the tray indicator.

    ``--for-ui <id>`` makes this the indicator of a companion rather than of
    the main window. A companion is a GTK4 layer-shell webview, which cannot
    host an AppIndicator (the tray spec is GTK3), so the plugin starts this
    process and stops it again on the way out.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    ui_id = ""
    title = ""
    while args:
        arg = args.pop(0)
        if arg in ("--for-ui", "-u") and args:
            ui_id = args.pop(0)
        elif arg in ("--title", "-t") and args:
            title = args.pop(0)
        elif arg in ("-h", "--help"):
            print(__doc__ or "")
            return

    app = SayriIndicator(ui_id=ui_id, title=title)
    Gtk.main()


if __name__ == "__main__":
    main()
