"""Native Siri Orb widget rendered with GTK4 and Cairo.

Fluid, animated glowing orb with multi-layer harmonic gradients that smoothly
transitions between states:
  - idle: calm breathing blue / cyan / purple glow
  - listening: energetic pulsing cyan / blue waves reacting to voice
  - activated: vibrant quick burst
  - thinking: swirling cosmic purple / magenta aurora
  - speaking: rhythmic teal / emerald / blue voice pulses
"""

from __future__ import annotations

import math
import time
from typing import Callable, Optional

import cairo
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

PALETTES = {
    "idle": [
        (0.22, 0.74, 0.98, 0.90),  # Apple Cyan
        (0.66, 0.33, 0.97, 0.85),  # Apple Purple
        (0.93, 0.28, 0.60, 0.75),  # Apple Magenta
        (0.40, 0.50, 0.98, 0.85),  # Indigo
    ],
    "listening": [
        (0.22, 0.74, 0.98, 0.98),  # Apple Cyan
        (0.93, 0.28, 0.60, 0.90),  # Apple Magenta
        (0.66, 0.33, 0.97, 0.95),  # Apple Purple
        (0.30, 0.85, 1.00, 0.90),  # Bright Cyan
    ],
    "activated": [
        (0.93, 0.28, 0.60, 1.00),  # Bright Magenta
        (0.22, 0.74, 0.98, 0.95),  # Apple Cyan
        (0.66, 0.33, 0.97, 0.95),  # Apple Purple
        (0.85, 0.40, 0.85, 0.85),  # Orchid
    ],
    "thinking": [
        (0.66, 0.33, 0.97, 0.95),  # Apple Purple
        (0.93, 0.28, 0.60, 0.90),  # Neon Magenta
        (0.45, 0.20, 0.95, 0.85),  # Deep Violet
        (0.22, 0.74, 0.98, 0.80),  # Cyan
    ],
    "speaking": [
        (0.22, 0.74, 0.98, 0.95),  # Apple Cyan
        (0.66, 0.33, 0.97, 0.90),  # Apple Purple
        (0.93, 0.28, 0.60, 0.85),  # Apple Magenta
        (0.40, 0.60, 1.00, 0.90),  # Electric Blue
    ],
}

SPEEDS = {
    "idle": 0.55,       # Serene, gentle breathing while waiting
    "listening": 1.8,
    "activated": 3.8,
    "thinking": 3.4,
    "speaking": 2.2,
}


class SiriOrb(Gtk.DrawingArea):
    """Animated Siri-style glowing orb."""

    def __init__(self, size: int = 140, on_click: Optional[Callable[[], None]] = None) -> None:
        super().__init__()
        self.size = size
        self.on_click = on_click
        self.state = "idle"
        self.phase = 0.0
        self.last_tick = time.monotonic()
        self.level = 0.0  # audio level 0..1 (voice or TTS)
        self.active_animation = True

        self.set_content_width(size)
        self.set_content_height(size)
        self.set_hexpand(False)
        self.set_vexpand(False)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)

        # Set Cairo draw function
        self.set_draw_func(self._draw)

        # Click handling
        click_gesture = Gtk.GestureClick.new()
        click_gesture.connect("released", self._on_clicked)
        self.add_controller(click_gesture)

        # 60 FPS animation tick
        self.add_tick_callback(self._on_tick)

    def set_active_animation(self, active: bool) -> None:
        self.active_animation = active
        if active:
            self.last_tick = time.monotonic()
            self.queue_draw()

    def set_state(self, state: str) -> None:
        if state in PALETTES:
            self.state = state
            self.queue_draw()

    def set_audio_level(self, level: float) -> None:
        self.level = max(0.0, min(1.0, level))

    def _on_clicked(self, _gesture, _n, _x, _y) -> None:
        if self.on_click:
            self.on_click()

    def _on_tick(self, _widget, _frame_clock) -> bool:
        if not self.active_animation or not self.get_mapped() or not self.get_visible():
            return GLib.SOURCE_CONTINUE
        now = time.monotonic()
        dt = min(0.1, now - self.last_tick)
        self.last_tick = now

        speed = SPEEDS.get(self.state, 0.55)
        if self.state in ("listening", "speaking") and self.level > 0.02:
            speed += self.level * 5.5
        elif self.state == "speaking":
            speed += 0.4 * math.sin(self.phase * 2.0)

        self.phase = (self.phase + dt * speed) % (math.pi * 1000.0)
        self.queue_draw()
        return GLib.SOURCE_CONTINUE

    def _draw(self, _area, cr: cairo.Context, w: int, h: int) -> None:
        if w <= 0 or h <= 0:
            return

        cx = w / 2.0
        cy = h / 2.0
        max_r = min(w, h) / 2.0
        r_base = max_r * (0.62 + self.level * 0.16)

        palette = PALETTES.get(self.state, PALETTES["idle"])
        t = self.phase

        # 1. Outer ambient glow (strictly contained inside max_r to prevent edge clipping)
        glow_r = min(max_r * 0.95, r_base * (1.16 + 0.20 * self.level))
        outer_glow = cairo.RadialGradient(cx, cy, r_base * 0.15, cx, cy, glow_r)
        c0 = palette[0]
        outer_glow.add_color_stop_rgba(0.0, c0[0], c0[1], c0[2], min(1.0, c0[3] * (0.45 + self.level * 0.35)))
        c1 = palette[1]
        outer_glow.add_color_stop_rgba(0.55, c1[0], c1[1], c1[2], min(1.0, c1[3] * (0.20 + self.level * 0.25)))
        outer_glow.add_color_stop_rgba(1.0, 0.0, 0.0, 0.0, 0.0)
        cr.set_source(outer_glow)
        cr.paint()

        # 2. Dynamic multi-blobs (4 organic swirling layers with additive blending)
        cr.set_operator(cairo.OPERATOR_ADD)

        for i, col in enumerate(palette):
            angle = t * (0.75 + i * 0.30) + (i * math.pi / 2.0)
            dist_factor = (0.12 + 0.04 * math.sin(t * 1.2 + i))
            if self.state in ("listening", "speaking"):
                dist_factor += self.level * 0.20

            bx = cx + math.cos(angle) * (r_base * dist_factor)
            by = cy + math.sin(angle * 1.25) * (r_base * dist_factor)
            br = r_base * (0.58 + 0.10 * math.sin(t * 1.8 + i * 1.4) + self.level * 0.20)

            blob = cairo.RadialGradient(bx, by, br * 0.05, bx, by, br)
            blob.add_color_stop_rgba(0.0, col[0], col[1], col[2], col[3])
            blob.add_color_stop_rgba(0.5, col[0], col[1], col[2], col[3] * 0.5)
            blob.add_color_stop_rgba(1.0, col[0], col[1], col[2], 0.0)
            cr.set_source(blob)
            cr.arc(bx, by, br, 0, 2 * math.pi)
            cr.fill()

        # 3. Inner core highlights (crystal-like bright center)
        core_r = r_base * (0.35 + 0.05 * math.sin(t * 2.0) + self.level * 0.12)
        core = cairo.RadialGradient(cx, cy, 0, cx, cy, core_r)
        core.add_color_stop_rgba(0.0, 1.0, 1.0, 1.0, 0.92)
        core.add_color_stop_rgba(0.3, palette[0][0], palette[0][1], palette[0][2], 0.75)
        core.add_color_stop_rgba(0.7, palette[1][0], palette[1][1], palette[1][2], 0.35)
        core.add_color_stop_rgba(1.0, 0.0, 0.0, 0.0, 0.0)
        cr.set_source(core)
        cr.arc(cx, cy, core_r, 0, 2 * math.pi)
        cr.fill()

        # 4. Subtle chromatic edge rim
        cr.set_operator(cairo.OPERATOR_OVER)
        rim_r = min(max_r * 0.94, r_base * 1.02)
        rim = cairo.RadialGradient(cx, cy, r_base * 0.82, cx, cy, rim_r)
        rim.add_color_stop_rgba(0.0, 1.0, 1.0, 1.0, 0.0)
        rim.add_color_stop_rgba(0.5, 0.9, 0.95, 1.0, 0.35)
        rim.add_color_stop_rgba(1.0, 0.0, 0.0, 0.0, 0.0)
        cr.set_source(rim)
        cr.arc(cx, cy, rim_r, 0, 2 * math.pi)
        cr.fill()
