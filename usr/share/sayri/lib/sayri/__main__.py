import os
import sys


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


def _entry() -> int:
    try:
        return main()
    except Exception:  # noqa: BLE001 - print a clear marker for crashes
        print("\n===== Sayri crash ====", file=sys.stderr)
        traceback.print_exc()
        print("=======================", file=sys.stderr)
        return 1


sys.exit(_entry())