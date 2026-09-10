import os
import sys

# Any plain word hands off to the headless CLI (daemon/CLI/UI-plugin commands).
# GUI flags (and no-args startup) keep going to the GTK app as before.
_GUI_FLAGS = {
    "-t", "--toggle", "--show", "--hide", "-s", "--settings",
    "-q", "--quit", "--autostart", "--alert", "--orb", "--launch-ui",
}


def _run_cli(args: list[str]) -> int:
    import faulthandler
    import traceback

    from .cli import main

    faulthandler.enable(all_threads=True)
    try:
        return main(args)
    except Exception:  # noqa: BLE001 - print a clear marker for crashes
        print("\n===== Sayri CLI crash ====", file=sys.stderr)
        traceback.print_exc()
        print("==========================", file=sys.stderr)
        return 1


def _run_gui(args: list[str]) -> int:
    """Legacy GUI entrypoint (GTK orb, WebKit, single-instance forwarding)."""
    def _preload_layer_shell() -> None:
        """Ensure gtk4-layer-shell is linked before WebKit's libwayland-client.

        Sayri pins its overlay windows with gtk4-layer-shell (GTK4 has no window
        positioning / always-on-top). On Wayland, WebKit's own libwayland-client
        can mask gtk4-layer-shell unless layer-shell is loaded first, which makes
        the WebKit Windows flaky (the window falls back to the screen center, no
        could be pinned, and WebKit may never load). The wrapper (usr/bin/sayri)
        sets LD_PRELOAD; running `python3 -m sayri` directly needs the same, so we
        re-exec ourselves with LD_PRELOAD set.

        Detection is filesystem-only (no GTK import) so it can't interfere with
        which libraries GTK/WebKit resolve later.
        """
        if os.environ.get("SAYRI_SKIP_PRELOAD") == "1":
            return
        if "gtk4-layer-shell" in os.environ.get("LD_PRELOAD", ""):
            return
        if os.environ.get("SAYRI_PRELOAD_REEXEC") == "1":
            return  # already re-executed; only try once

        candidates = (
            "/usr/lib/libgtk4-layer-shell.so",
            "/usr/lib64/libgtk4-layer-shell.so",
            "/usr/lib/x86_64-linux-gnu/libgtk4-layer-shell.so",
            "/usr/lib/aarch64-linux-gnu/libgtk4-layer-shell.so",
        )
        lib = next((p for p in candidates if os.path.exists(p)), None)
        if lib is None:
            # fall back to the soname from ldconfig
            try:
                import subprocess

                out = subprocess.run(
                    ["ldconfig", "-p"], capture_output=True, text=True).stdout
                for line in out.splitlines():
                    if "libgtk4-layer-shell.so." in line:
                        lib = line.split()[-1]
                        break
            except Exception:  # noqa: BLE001
                lib = None
        if lib is None:
            return

        os.environ["LD_PRELOAD"] = lib + (":" + os.environ["LD_PRELOAD"]
                                          if os.environ.get("LD_PRELOAD") else "")
        os.environ["SAYRI_PRELOAD_REEXEC"] = "1"
        try:
            os.execv(sys.executable, [sys.executable, "-m", "sayri", *sys.argv[1:]])
        except Exception:  # noqa: BLE001
            pass  # fall through to normal startup if re-exec fails

    _preload_layer_shell()

    if os.environ.get("DISPLAY") and os.environ.get("SAYRI_FORCE_WAYLAND") != "1":
        os.environ["GDK_BACKEND"] = "x11,wayland"

    import faulthandler  # noqa: E402
    import traceback  # noqa: E402

    from .app import main  # noqa: E402

    # Print the faulting stack on native crashes (segfaults) automatically.
    faulthandler.enable(all_threads=True)

    try:
        return main()
    except Exception:  # noqa: BLE001 - print a clear marker for crashes
        print("\n===== Sayri crash ====", file=sys.stderr)
        traceback.print_exc()
        print("=======================", file=sys.stderr)
        return 1


def _ensure_daemon() -> None:
    from .ipc import daemon_is_running
    if daemon_is_running():
        return
    import os
    import subprocess
    import sys
    from . import paths
    log = os.path.join(paths.state_dir(), "daemon.log")
    os.makedirs(os.path.dirname(log), exist_ok=True)
    with open(log, "ab", buffering=0) as f:
        subprocess.Popen(
            [sys.executable, "-c", "import sys; from sayri.daemon import main; sys.exit(main())"],
            stdin=subprocess.DEVNULL, stdout=f, stderr=f,
            start_new_session=True, close_fds=True,
        )


def _run_launch_ui(argv: list[str]) -> int:
    """Launch the configured default UI (ui.default_ui), daemon first."""
    try:
        from . import config as cfg
        default_ui = cfg.config.get_string("ui", "default_ui").strip()
    except Exception:  # noqa: BLE001
        default_ui = "orb"
    if not default_ui or default_ui == "orb":
        _ensure_daemon()
        return _run_gui(argv)
    from .cli import _ensure_daemon as cli_ensure
    cli_ensure()
    return _run_cli(["ui", "start", default_ui])


def _entry() -> int:
    first = sys.argv[1] if len(sys.argv) > 1 else ""
    if first in ("--launch-ui", "-lu"):
        return _run_launch_ui(sys.argv[1:])
    if not first:  # nothing → launch the GUI as always
        return _run_gui(sys.argv[1:])
    if first in _GUI_FLAGS or first.startswith("-"):
        return _run_gui(sys.argv[1:])
    return _run_cli(sys.argv[1:])


sys.exit(_entry())