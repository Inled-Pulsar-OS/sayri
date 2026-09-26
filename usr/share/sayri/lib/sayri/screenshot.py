"""Screenshot utility for Sayri (Linux Wayland/X11, macOS and Windows)."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from typing import Optional

from . import paths, sysinfo


def take_screenshot(dest_path: Optional[str] = None) -> str:
    """Take a full desktop screenshot and save it to dest_path (or /tmp)."""
    if not dest_path:
        dest_path = os.path.join(paths.tmp_dir(), f"screenshot_{int(time.time())}.png")

    os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)

    # 1. macOS: built-in screencapture
    if sysinfo.is_macos():
        if shutil.which("screencapture"):
            try:
                res = subprocess.run(
                    ["screencapture", "-x", dest_path], capture_output=True, timeout=12
                )
                if res.returncode == 0 and os.path.isfile(dest_path) and os.path.getsize(dest_path) > 0:
                    print(f"[Screenshot] ✓ Saved screencapture screenshot: {dest_path}")
                    return dest_path
            except Exception:
                pass
        raise RuntimeError(
            f"Could not take screenshot on macOS: 'screencapture' unavailable for {dest_path}"
        )

    # 2. Windows: PowerShell + System.Drawing (built into .NET)
    if sysinfo.is_windows():
        ps_cmd = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "Add-Type -AssemblyName System.Drawing;"
            "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
            "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
            "$g=[System.Drawing.Graphics]::FromImage($bmp);"
            "$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size);"
            f"$bmp.Save('{dest_path}')"
        )
        try:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True, timeout=15,
            )
            if res.returncode == 0 and os.path.isfile(dest_path) and os.path.getsize(dest_path) > 0:
                print(f"[Screenshot] ✓ Saved PowerShell screenshot: {dest_path}")
                return dest_path
        except Exception:
            pass
        # Fallback: ffmpeg gdigrab
        if shutil.which("ffmpeg"):
            try:
                res = subprocess.run(
                    ["ffmpeg", "-y", "-f", "gdigrab", "-i", "desktop", "-frames:v", "1", dest_path],
                    capture_output=True, timeout=15,
                )
                if res.returncode == 0 and os.path.isfile(dest_path) and os.path.getsize(dest_path) > 0:
                    print(f"[Screenshot] ✓ Saved ffmpeg gdigrab screenshot: {dest_path}")
                    return dest_path
            except Exception:
                pass
        raise RuntimeError(
            f"Could not take screenshot on Windows: no supported tool found (PowerShell, ffmpeg) for {dest_path}"
        )

    # 3. Try GNOME Shell DBus method (native GNOME Wayland)
    try:
        cmd = [
            "gdbus", "call", "--session",
            "--dest", "org.gnome.Shell.Screenshot",
            "--object-path", "/org/gnome/Shell/Screenshot",
            "--method", "org.gnome.Shell.Screenshot.Screenshot",
            "true", "false", dest_path,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
        if res.returncode == 0 and os.path.isfile(dest_path) and os.path.getsize(dest_path) > 0:
            print(f"[Screenshot] ✓ Saved GNOME DBus screenshot: {dest_path}")
            return dest_path
    except Exception:
        pass

    # 2. Try gnome-screenshot
    if shutil.which("gnome-screenshot"):
        try:
            res = subprocess.run(["gnome-screenshot", "-f", dest_path], capture_output=True, timeout=8)
            if res.returncode == 0 and os.path.isfile(dest_path) and os.path.getsize(dest_path) > 0:
                print(f"[Screenshot] ✓ Saved gnome-screenshot: {dest_path}")
                return dest_path
        except Exception:
            pass

    # 4. Try grim (standard Wayland)
    if shutil.which("grim"):
        try:
            res = subprocess.run(["grim", dest_path], capture_output=True, timeout=8)
            if res.returncode == 0 and os.path.isfile(dest_path) and os.path.getsize(dest_path) > 0:
                print(f"[Screenshot] ✓ Saved grim screenshot: {dest_path}")
                return dest_path
        except Exception:
            pass

    # 5. Try spectacle (KDE)
    if shutil.which("spectacle"):
        try:
            res = subprocess.run(["spectacle", "-b", "-n", "-o", dest_path], capture_output=True, timeout=8)
            if res.returncode == 0 and os.path.isfile(dest_path) and os.path.getsize(dest_path) > 0:
                print(f"[Screenshot] ✓ Saved spectacle screenshot: {dest_path}")
                return dest_path
        except Exception:
            pass

    # 6. Try ImageMagick import (X11)
    if shutil.which("import") and os.environ.get("DISPLAY"):
        try:
            res = subprocess.run(["import", "-window", "root", dest_path], capture_output=True, timeout=8)
            if res.returncode == 0 and os.path.isfile(dest_path) and os.path.getsize(dest_path) > 0:
                print(f"[Screenshot] ✓ Saved ImageMagick screenshot: {dest_path}")
                return dest_path
        except Exception:
            pass

    raise RuntimeError(f"Could not take screenshot: no supported tool found (gnome-screenshot, grim, spectacle, import)")


def main() -> int:
    import sys
    args = sys.argv[1:]
    dest = args[0] if args else None
    try:
        out = take_screenshot(dest)
        print(f"Screenshot saved to: {out}")
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
