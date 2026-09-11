"""xui — cross-renderer declarative UI micro-framework for Sayri.

Render the *same* screen description (a plain-JSON "component tree") in an
interactive terminal (TUI), inside a WebKit/GTK window (through the ``sayri://``
bridge) or in a standalone browser page. Only the event *transport* changes;
the screens, the host flow and the field model are shared, so the welcome
wizard and every plugin wizard are written once and rendered everywhere.

Screens
-------
A screen is a small JSON document (``dict`` in Python)::

    {
      "id": "welcome",
      "title": "Welcome to Sayri",
      "subtitle": "One-minute setup",
      "step": "1/5",
      "body":  [ <nodes> ],
      "footer":[ <buttons> ],
      "busy": false,   # set while a background task runs; renderers poll
      "done": false    # final screen marker
    }

Node types (``"t"``):

    text   /  sub     text lines
    note            info | ok | warn | error box
    spacer
    button         id, label, kind=primary|secondary|danger
    entry          id, label, default, placeholder, secret, hint
    select         id, label, options[{value,label,desc}], default
    check          id, label, default
    progress       label, pct (0..1) or null (indeterminate)

Host protocol
-------------
A *host* owns the flow: it implements ``render() -> screen|None`` and
``dispatch(event) -> screen|None`` (``None`` means the flow finished and the
renderer should stop). Events:

    {"type":"submit", "value": {field_id: value, ...}}   form collected values
    {"type":"action", "widget": "<button id>", "value": {...}}
    {"type":"change", "widget": id, "value": v}           live widgets
    {"type":"poll"}                                       refresh while busy

Every transport (TUI keystrokes, WebKit bridge, HTTP fetch) translates user
input into those same events, so ``wizard.py`` and the PrismML plugin host
logic is identical everywhere.
"""

from __future__ import annotations

import html as _html
import json
import os
import sys
import threading
import time
from typing import Any, Callable, Optional

__all__ = [
    "screen", "text", "sub", "note", "button", "entry", "select", "check",
    "progress", "spacer", "collect_defaults", "wizard_page", "render_html",
    "Tui", "run_cli", "TaskRunner", "Widgets",
]


# --------------------------------------------------------------------------- nodes

class Widgets:
    """Namespaced widget builders so call sites read ``xui.W.text(...)``."""

    @staticmethod
    def text(t: str, accent: bool = False, dim: bool = False) -> dict:
        return {"t": "text", "text": str(t), "accent": bool(accent), "dim": bool(dim)}

    @staticmethod
    def sub(t: str) -> dict:
        return {"t": "sub", "text": str(t)}

    @staticmethod
    def note(t: str, level: str = "info") -> dict:
        if level not in ("info", "ok", "warn", "error"):
            level = "info"
        return {"t": "note", "text": str(t), "level": level}

    @staticmethod
    def spacer() -> dict:
        return {"t": "spacer"}

    @staticmethod
    def button(id: str, label: str, kind: str = "secondary", icon: str = "") -> dict:
        return {"t": "button", "id": id, "label": str(label), "kind": kind, "icon": icon}

    @staticmethod
    def entry(id: str, label: str = "", default: str = "", placeholder: str = "",
              secret: bool = False, hint: str = "") -> dict:
        return {"t": "entry", "id": id, "label": str(label), "default": str(default),
                "placeholder": str(placeholder), "secret": bool(secret), "hint": str(hint)}

    @staticmethod
    def select(id: str, label: str, options: list, default: str = "") -> dict:
        return {"t": "select", "id": id, "label": str(label),
                "options": list(options), "default": str(default)}

    @staticmethod
    def check(id: str, label: str, default: bool = False) -> dict:
        return {"t": "check", "id": id, "label": str(label), "default": bool(default)}

    @staticmethod
    def progress(label: str = "", pct: Optional[float] = None) -> dict:
        return {"t": "progress", "label": str(label), "pct": pct}


# Public single-name aliases (function-style). The ``Widgets`` class is the
# recommended explicit form; both render identically.
def text(t: str, accent: bool = False, dim: bool = False) -> dict:
    return Widgets.text(t, accent=accent, dim=dim)


def sub(t: str) -> dict:
    return Widgets.sub(t)


def note(t: str, level: str = "info") -> dict:
    return Widgets.note(t, level=level)


def spacer() -> dict:
    return Widgets.spacer()


def button(id: str, label: str, kind: str = "secondary", icon: str = "") -> dict:
    return Widgets.button(id, label, kind=kind, icon=icon)


def entry(id: str, label: str = "", default: str = "", placeholder: str = "",
          secret: bool = False, hint: str = "") -> dict:
    return Widgets.entry(id, label, default, placeholder, secret, hint)


def select(id: str, label: str, options: list, default: str = "") -> dict:
    return Widgets.select(id, label, options, default)


def check(id: str, label: str, default: bool = False) -> dict:
    return Widgets.check(id, label, default)


def progress(label: str = "", pct: Optional[float] = None) -> dict:
    return Widgets.progress(label, pct)


def screen(title: str, body: list, subtitle: str = "", footer: Optional[list] = None,
           id: Optional[str] = None, step: str = "", busy: bool = False,
           done: bool = False) -> dict:
    """Build a screen document from widget lists."""
    return {
        "id": id or "",
        "title": str(title),
        "subtitle": str(subtitle),
        "step": str(step),
        "body": list(body),
        "footer": list(footer or []),
        "busy": bool(busy),
        "done": bool(done),
    }


def collect_defaults(screen: dict) -> dict:
    """Initial ``{field_id: value}`` map for every input widget on the screen."""
    out: dict = {}
    for node in list(screen.get("body", [])) + list(screen.get("footer", [])):
        t = node.get("t")
        if t == "entry":
            out[node["id"]] = node.get("default", "")
        elif t == "select":
            opts = node.get("options", [])
            default = node.get("default", "")
            out[node["id"]] = default if any(o.get("value") == default for o in opts) else (
                opts[0]["value"] if opts else "")
        elif t == "check":
            out[node["id"]] = bool(node.get("default"))
    return out


def iter_focusable(screen: dict):
    """Yield ``(widget_id, widget_dict)`` for focusable inputs+buttons in order."""
    for node in list(screen.get("body", [])) + list(screen.get("footer", [])):
        if node.get("t") in ("entry", "select", "check", "button"):
            yield node["id"], node


# ------------------------------------------------------------------------- tasks

class TaskRunner:
    """Runs long host operations in a background thread with progress updates.

    The thread updates ``progress`` (0..1 or None = indeterminate), appends
    ``log`` lines and finally stores ``result`` (or ``error``). ``poll()``
    returns ``(running, progress, log, result, error)`` so both the TUI pump
    and the web/HTTP host can render a live progress screen without blocking.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running = False
        self._progress: Optional[float] = None
        self._log: list[str] = []
        self._result: Any = None
        self._error: Optional[str] = None

    def start(self, label: str, fn: Callable[[Callable, list], Any]) -> None:
        """Launch ``fn(progress_cb, log_sink)`` in a daemon thread."""
        if self._running:
            return
        with self._lock:
            self._running = True
            self._progress = None
            self._log = []
            self._result = None
            self._error = None

        def _run() -> None:
            try:
                res = fn(self._progress_cb, self._log_lines)
                with self._lock:
                    self._result = res
            except Exception as exc:  # noqa: BLE001 - surfaced to the UI
                with self._lock:
                    self._error = str(exc)
            finally:
                with self._lock:
                    self._running = False

        threading.Thread(target=_run, daemon=True).start()

    def _progress_cb(self, frac: Optional[float]) -> None:
        with self._lock:
            self._progress = frac

    def _log_lines(self, lines: list[str]) -> None:
        with self._lock:
            self._log.extend(lines)

    def add_log(self, line: str) -> None:
        with self._lock:
            self._log.append(line)

    def poll(self) -> dict:
        with self._lock:
            return {
                "running": self._running,
                "progress": self._progress,
                "log": list(self._log),
                "result": self._result,
                "error": self._error,
            }

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def cancel(self) -> None:
        with self._lock:
            self._running = False


# ------------------------------------------------------------------------- TUI

_W = Widgets  # shorthand used by the TUI renderer below

_SEQ_RESET = "\033[0m"
_SEQ_BOLD = "\033[1m"
_SEQ_DIM = "\033[2m"
_SEQ_REV = "\033[7m"
_SEQ_UNDER = "\033[4m"
_COL_GREEN, _COL_YEL, _COL_RED, _COL_CYAN = "\033[32m", "\033[33m", "\033[31m", "\033[36m"
_COL_BLUE, _COL_WHITE = "\033[34m", "\033[37m"
_COL_MAG = "\033[35m"
_COL_DIM = _SEQ_DIM  # alias so ``_style(_COL_DIM, …)`` reads like the other colors


def _style(seq: str, s: str) -> str:
    return f"{seq}{s}{_SEQ_RESET}"


class Tui:
    """Interactive terminal renderer. Sends ``xui`` events to the host app."""

    def __init__(self, app: Any, stream: Any = None, out: Any = None,
                 poll_interval: float = 0.16) -> None:
        self.app = app
        self.stream = stream if stream is not None else sys.stdin
        self.out = out if out is not None else sys.stdout
        self.poll_interval = poll_interval
        self._fields: dict = {}
        self._focus_idx = 0
        self._focus_ids: list[str] = []
        self._screen: Optional[dict] = None
        self._eof = False
        self._is_tty = bool(getattr(self.stream, "isatty", lambda: False)())

    # ------------------------------------------------------------- public
    def run(self) -> int:
        """Run the interactive loop until the host signals the end."""
        screen = self._safe_render()
        if screen is None:
            return 0
        self._show(screen)
        if not self._is_tty:
            return self._run_lines()
        try:
            while True:
                key = self._read_key()
                if key is None:  # ctrl+c processed inside _read_key
                    self._emit("action", "cancel")
                    continue
                next_screen = self._handle_key(self._screen or screen, key)
                if next_screen is False:  # quit
                    break
                if next_screen is not None:
                    screen = next_screen
        except (KeyboardInterrupt, EOFError):
            pass
        return 0

    def _safe_render(self) -> Optional[dict]:
        try:
            scr = self.app.render()
        except Exception as exc:  # noqa: BLE001
            scr = screen("Error", [note(f"{exc}", "error")], footer=[button("quit", "Close")])
        return scr

    # ------------------------------------------------------- key handling
    def _read_key(self) -> Optional[str]:
        old = _raw_mode(self.stream)
        try:
            b = os.read(self.stream.fileno(), 1) if old else self.stream.readline(1)
        finally:
            _restore_mode(self.stream, old)
        if not b:
            return "\n"
        if b in (b"\x03", b"\x1c"):  # ctrl+c
            raise KeyboardInterrupt
        if b in (b"\x1b",):  # ESC
            seq = ""
            for _ in range(4):
                nxt = os.read(self.stream.fileno(), 1)
                if not nxt:
                    break
                seq += nxt.decode()
                if seq in ("[A", "[B", "[C", "[D"):
                    break
            return {"[A": "up", "[B": "down", "[C": "right", "[D": "left"}.get(seq) or None
        return b.decode("utf-8", "replace")

    def _handle_key(self, scr: dict, key: str) -> Optional[dict]:
        ids = [wid for wid, _ in iter_focusable(scr)]
        self._focus_ids = ids
        if not ids:
            if key in ("\n", "\r"):
                return self._emit("submit", {})
            return None
        idx = min(max(self._focus_idx, 0), len(ids) - 1)
        wid = ids[idx]
        node = dict(iter_focusable(scr))[wid]

        if key in ("up",):
            self._focus_idx = (idx - 1) % len(ids)
            return self._redraw()
        if key in ("down", "\t"):
            self._focus_idx = (idx + 1) % len(ids)
            return self._redraw()

        t = node.get("t")
        if key == "left":
            if t == "select":
                self._fields[wid] = _select_prev(scr, wid, self._fields.get(wid, ""))
                return self._redraw()
            if t == "check":
                self._fields[wid] = not self._fields.get(wid, bool(node.get("default")))
                return self._redraw()
        if key == "right":
            if t == "select":
                self._fields[wid] = _select_next(scr, wid, self._fields.get(wid, ""))
                return self._redraw()
            if t == "check":
                self._fields[wid] = not self._fields.get(wid, bool(node.get("default")))
                return self._redraw()
        if key == " " and t == "check":
            self._fields[wid] = not self._fields.get(wid, bool(node.get("default")))
            return self._redraw()

        if t == "entry":
            if key == "\b" or key == "\x7f":
                self._fields[wid] = str(self._fields.get(wid, node.get("default", "")))[:-1]
                return self._redraw()
            if key in ("\n", "\r"):
                return self._emit("submit", self._submit_values(scr))
            if len(key) == 1 and key.isprintable():
                self._fields[wid] = str(self._fields.get(wid, node.get("default", ""))) + key
                return self._redraw()
            return None

        if t == "select":
            if key in ("\n", "\r"):
                return self._emit("submit", self._submit_values(scr))
            if key.isdigit():
                opts = node.get("options", [])
                k = int(key)
                if 1 <= k <= len(opts):
                    self._fields[wid] = opts[k - 1]["value"]
                    return self._redraw()
            return None

        if t == "check":
            if key in ("\n", "\r"):
                return self._emit("submit", self._submit_values(scr))
            return None

        if t == "button":
            if key in ("\n", "\r"):
                ev: dict = {"type": "action", "widget": node.get("id", "")}
                vals = self._submit_values(scr)
                if vals:
                    ev["value"] = vals
                return self._dispatch(ev)
            return None
        return None

    def _emit(self, kind: str, value: Any) -> Optional[dict]:
        ev: dict = {"type": kind}
        if kind == "action":
            ev["widget"] = str(value)
        else:
            ev["value"] = value
        return self._dispatch(ev)

    def _dispatch(self, ev: dict) -> Optional[dict]:
        scr = self.app.dispatch(ev)
        if scr is None:
            return False
        if scr.get("busy"):
            while scr.get("busy"):
                self._show(scr)
                time.sleep(self.poll_interval)
                nxt = self.app.dispatch({"type": "poll"})
                if nxt is None:
                    return False
                scr = nxt
        self._show(scr)
        return scr

    def _submit_values(self, scr: dict) -> dict:
        values = dict(self._fields) if self._fields else {}
        defaults = collect_defaults(scr)
        defaults.update(values)
        return defaults

    # ------------------------------------------------------------ drawing
    def _redraw(self) -> Optional[dict]:
        if self._screen:
            self._show(self._screen)
        return None

    def _show(self, scr: dict) -> None:
        self._screen = scr
        w = _terminal_width(self.stream) or 72
        lines = self._render(scr, width=w)
        if self._is_tty:
            self.out.write(f"\033[2J\033[H")
        self.out.write("\n".join(lines) + "\n")
        if self._is_tty:
            self.out.write("\033[?25h")
        self.out.flush()

    def _render(self, scr: dict, width: int = 72) -> list[str]:
        inner = max(20, min(100, width - 2))
        title = scr.get("title", "Sayri")
        step = scr.get("step", "")
        head = f" {title} "
        if step:
            head += f" {_style(_COL_DIM, '·')} " + _style(_COL_CYAN, step)
        bar = "─" * max(0, inner - len(head) - len(_fancy_tag("SAYRI")))
        out = [f"┌{_style(_COL_MAG, head)} {_style(_COL_GREEN, 'SAYRI')} {bar}┐"]
        sub = scr.get("subtitle", "")
        if sub:
            out.append("│ " + _style(_COL_DIM, _truncate(sub, inner)) + " " * max(1, inner - len(sub)) + "│")

        # collect body rows
        rows: list[str] = []
        fids = [wid for wid, _ in iter_focusable(scr)]
        focus = fids[min(max(self._focus_idx, 0), len(fids) - 1)] if fids else None
        for node in scr.get("body", []):
            rows.extend(self._render_node(scr, node, inner, focus))
        for i, row in enumerate(rows):
            padded = row + " " * max(0, inner - _visible_len(row))
            out.append("│ " + padded + " │")

        # separator
        out.append("├" + "─" * inner + "┤")

        # footer
        footer = scr.get("footer", [])
        if footer and not scr.get("busy"):
            btns = []
            for node in footer:
                label = _style(_SEQ_BOLD, node.get("label", "?"))
                if node.get("id") == focus:
                    label = _style(_SEQ_REV, " " + label + " ")
                    label = _style(_COL_GREEN, label)
                btns.append(label)
            out.append("│ " + "   ".join(btns) + " " * max(0, inner - sum(_visible_len(b) for b in btns) - 4) + " │" if btns else "")
        elif scr.get("busy"):
            out.append("│ " + _style(_COL_CYAN, "⏳ working...") + " " * max(0, inner - 13) + " │")
        else:
            out.append("│ " + " " * inner + " │")

        if scr.get("done"):
            out[-1] = out[-1]
        out.append("└" + "─" * inner + "┘")
        if not self._is_tty:
            out.append("")  # scrolling separation for piped output
        return out

    def _render_node(self, scr: dict, node: dict, inner: int, focus: Optional[str]) -> list[str]:
        t = node.get("t")
        if t == "text":
            s = str(node.get("text", ""))
            if node.get("accent"):
                s = _style(_SEQ_BOLD + _COL_WHITE, s)
            elif node.get("dim"):
                s = _style(_COL_DIM, s)
            return [_truncate(s, inner)]
        if t == "sub":
            return [_style(_SEQ_DIM + _COL_CYAN, _truncate(str(node.get("text", "")), inner))]
        if t == "note":
            level = node.get("level", "info")
            color = {"info": _COL_CYAN, "ok": _COL_GREEN, "warn": _COL_YEL, "error": _COL_RED}[level]
            tag = {"info": "ℹ", "ok": "✔", "warn": "⚠", "error": "✖"}[level]
            return [_style(color, f" {tag} {_truncate(str(node.get('text','')), inner-3)} ")]
        if t == "spacer":
            return [""]
        if t == "progress":
            pct = node.get("pct")
            label = str(node.get("label", ""))
            if pct is None:
                line = _style(_COL_CYAN, f"  ⟳ {label}") if label else _style(_COL_CYAN, "  ⟳")
            else:
                p = max(0.0, min(1.0, float(pct)))
                bar = "█" * int(p * (inner - 20)) + "░" * (inner - 20 - int(p * (inner - 20)))
                line = f"  [ {bar} ] {p*100:4.0f}% {label}".strip()
            return [line[:inner]]
        wid = node.get("id", "")
        focused = wid == focus
        mark = "❯" if focused else " "
        if t == "entry":
            val = str(self._fields.get(wid, node.get("default", "")))
            shown = "\u2022" * len(val) if node.get("secret") else val
            if not shown:
                shown = _style(_COL_DIM, node.get("placeholder", "…"))
            label = node.get("label", "")
            prefix = f"{mark} {label}: " if label else f"{mark} "
            middle = _style(_SEQ_UNDER, shown + " ") if focused else shown
            return [_truncate(prefix + middle, inner)]
        if t == "select":
            opts = node.get("options", [])
            cur = self._fields.get(wid, node.get("default", "")) or (opts[0]["value"] if opts else "")
            label = node.get("label", "")
            current = next((o["label"] for o in opts if o["value"] == cur), cur)
            prefix = f"{mark} {label}: " if label else f"{mark} "
            idx = next((i for i, o in enumerate(opts) if o["value"] == cur), 0)
            suffix = _style(_COL_DIM, f" ◀{idx+1}/{len(opts)}▶") if len(opts) > 1 else ""
            return [_truncate(prefix + _style(_SEQ_BOLD, current) + suffix, inner)]
        if t == "check":
            val = bool(self._fields.get(wid, node.get("default")))
            box = "[x]" if val else "[ ]"
            box = _style(_COL_GREEN, box) if val and not focused else _style(_COL_CYAN, box) if focused else box
            return [_truncate(f"{mark} {box} {str(node.get('label',''))}", inner)]
        if t == "button":
            btns = [_style(_SEQ_BOLD, node.get("label", "?"))]
            if focused:
                btns[0] = _style(_SEQ_REV, btns[0])
            return [_truncate(f"{mark} {btns[0]}", inner)]
        return [_truncate(json.dumps(node, ensure_ascii=False)[:inner], inner)]

    # --------------------------------------------------- line (piped) mode
    def _run_lines(self) -> int:
        scr = self._screen or self._safe_render()
        if scr is None:
            return 0
        while scr is not None:
            scr = self._prompt_lines(scr)
            if scr is False:
                break
        return 0

    def _prompt_lines(self, scr: dict):
        if self._eof:
            return False
        values = collect_defaults(scr)
        # interactive selection fields
        for node in scr.get("body", []) + scr.get("footer", []):
            t = node.get("t")
            if t == "select":
                opts = node.get("options", [])
                cur = values.get(node["id"], opts[0]["value"] if opts else "")
                self._print_select(node, opts, cur)
                answer = self._readline().strip()
                if self._eof or not answer:
                    choice = cur
                elif answer.isdigit() and 1 <= int(answer) <= len(opts):
                    choice = opts[int(answer) - 1]["value"]
                else:
                    choice = answer
                values[node["id"]] = choice
            elif t == "entry":
                cur = str(values.get(node["id"], node.get("default", "")))
                lab = (node.get("label") or node["id"])
                if node.get("secret"):
                    self.out.write(f"  {lab} [{_style(_COL_DIM, 'hidden')}]: ")
                else:
                    self.out.write(f"  {lab} [{'/'.join(cur.split()) or _style(_COL_DIM,'(enter)')}]: ")
                self.out.flush()
                a = self._readline().strip()
                if self._eof:
                    break
                if a:
                    values[node["id"]] = a
            elif t == "check":
                cur = bool(values.get(node["id"], node.get("default", False)))
                self.out.write(f"  {node.get('label','')}? ['y' if yes] ")
                self.out.flush()
                a = self._readline().strip().lower()
                if self._eof:
                    break
                if a in ("y", "yes", "1", "true", "on"):
                    values[node["id"]] = True
                elif a in ("n", "no", "0", "false", "off"):
                    values[node["id"]] = False
                # empty answer keeps the current default
            elif t == "button":
                pass
        if self._eof:
            return False
        # action row
        footer = scr.get("footer", [])
        if football := footer:
            if len(football) == 1:
                self.out.write(f"  ⌨ {_style(_COL_GREEN, 'Enter')} → {football[0].get('label','ok')}\n")
                self.out.flush()
                self._readline()
                if self._eof:
                    return False
                return self.app.dispatch({"type": "action", "widget": football[0].get("id", ""), "value": values})
            else:
                for i, b in enumerate(football, 1):
                    self.out.write(f"    {i}. {b.get('label','?')} [{b.get('id','')}]\n")
                self.out.write("  choose [1-{0}]: ".format(len(football)))
                self.out.flush()
                a = self._readline().strip()
                if self._eof:
                    return False
                idx = int(a) - 1 if a.isdigit() and 1 <= int(a) <= len(football) else 0
                return self.app.dispatch({"type": "action", "widget": football[idx].get("id", ""), "value": values})
        # free submit (no footer) — read Enter
        self.out.write("  ⌨ Enter para continuar\n")
        self.out.flush()
        self._readline()
        if self._eof:
            return False
        return self._emit("submit", values)

    def _print_select(self, node: dict, opts: list, cur: str) -> None:
        self.out.write(f"\n  {node.get('label', node['id'])} ({opts[0]['value'] if opts else ''}…):\n")
        for i, o in enumerate(opts, 1):
            sel = "❯" if o["value"] == cur else " "
            desc = o.get("desc", "")
            d = f"  · {desc}" if desc else ""
            self.out.write(f"    {sel} {i}. {o.get('label', o['value'])}{d}\n")
        self.out.write("  choose [number, o valor]: ")
        self.out.flush()

    def _readline(self) -> str:
        line = self.stream.readline()
        if not self._is_tty and line == "":
            self._eof = True
        return line.rstrip("\r\n")


def _raw_mode(stream) -> Any:
    try:
        import termios
        import tty
        if not getattr(stream, "isatty", lambda: False)():
            return None
        fd = stream.fileno()
        old = termios.tcgetattr(fd)
        tty.setraw(fd)
        return old
    except Exception:  # noqa: BLE001 - not a tty / no termios
        return None


def _restore_mode(stream, old) -> None:
    if old is None:
        return
    try:
        import termios
        fd = stream.fileno()
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except Exception:  # noqa: BLE001
        pass


def _terminal_width(stream) -> int:
    try:
        import shutil
        w = shutil.get_terminal_size(fallback=(80, 24)).columns
        return w
    except Exception:  # noqa: BLE001
        return 80


def _visible_len(s: str) -> int:
    # strip ANSI escapes for padding math
    import re as _re
    return len(_re.sub(r"\033\[[0-9;?]*[a-zA-Z]", "", s))


def _truncate(s: str, n: int) -> str:
    if _visible_len(s) <= max(n, 0):
        return s
    plain = _visible_len(s)
    cut = max(n - 1, 1)
    return s[:_visible_index(s, cut - int(plain > cut))] + "…"

def _visible_index(s: str, n: int) -> int:
    seen = 0
    i = 0
    while i < len(s) and seen < n:
        if s[i] == "\033":
            j = s.find("m", i)
            if j == -1:
                break
            i = j + 1
            continue
        seen += 1
        i += 1
    return i


def _select_next(scr: dict, wid: str, cur: str) -> str:
    opts = [] 
    for node in list(scr.get("body", [])) + list(scr.get("footer", [])):
        if node.get("id") == wid:
            opts = node.get("options", [])
    idx = next((i for i, o in enumerate(opts) if o["value"] == cur), -1)
    if not opts:
        return cur
    return opts[(idx + 1) % len(opts)]["value"]


def _select_prev(scr: dict, wid: str, cur: str) -> str:
    opts = []
    for node in list(scr.get("body", [])) + list(scr.get("footer", [])):
        if node.get("id") == wid:
            opts = node.get("options", [])
    idx = next((i for i, o in enumerate(opts) if o["value"] == cur), 0)
    if not opts:
        return cur
    return opts[(idx - 1) % len(opts)]["value"]


def _fancy_tag(name: str) -> str:
    return name


def run_cli(app: Any, stream: Any = None, out: Any = None) -> int:
    """Convenience wrapper: build a ``Tui`` and run it against ``app``."""
    return Tui(app, stream=stream, out=out).run()


# ------------------------------------------------------------------------- HTML

def wizard_page(initial: dict, title: str = "Sayri", transport: Any = None,
                favicon: str = "") -> str:
    """Complete, self-contained interactive HTML page for a host-driven screen.

    ``transport`` selects how the JS posts events:

      * ``{"kind":"webkit"}``                 -> window.webkit.messageHandlers.sayri
      * ``{"kind":"fetch","endpoint":"/api/event"}`` -> POST JSON, expects screen JSON
      * ``{"kind":"none"}``                   -> just render (no posting)

    The injected ``window.xui.render(screen)`` lets the host push body/footer
    updates (progress screens, the next step) over the bridge.
    """
    transport = transport or {"kind": "none"}
    page = _HTML_PAGE
    page = page.replace("__TITLE__", _html.escape(title))
    page = page.replace("__INITIAL__", json.dumps(initial, ensure_ascii=False))
    page = page.replace("__TRANSPORT__", json.dumps(transport, ensure_ascii=False))
    if favicon:
        page = page.replace("__FAVICON__", favicon)
    else:
        page = page.replace("__FAVICON__", "data:,")
    return page


def render_html(screen: dict, title: str = "Sayri", transport: Any = None) -> str:
    """Alias of :func:`wizard_page` — render a single host-driven screen."""
    return wizard_page(screen, title=title, transport=transport)


_HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no" />
<title>__TITLE__</title>
<link rel="icon" href="__FAVICON__" />
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; background: rgba(10,12,18,0.96);
               color: #e6ebf2; font: 15px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  .wrap { max-width: 720px; margin: 0 auto; padding: 28px 20px 40px; }
  header { display: flex; align-items: baseline; gap: 12px; border-bottom: 1px solid rgba(255,255,255,.08); padding-bottom: 14px; }
  .logo { font-weight: 800; letter-spacing: .04em; background: linear-gradient(90deg,#22c55e,#38bdf8);
          -webkit-background-clip: text; background-clip: text; color: transparent; }
  header h1 { font-size: 22px; margin: 0; }
  .step { margin-left: auto; color: #7bd8f0; font-size: 13px; font-variant-numeric: tabular-nums; }
  .subtitle { color: #94a3b8; margin: 8px 0 18px; }
  .body { display: flex; flex-direction: column; gap: 12px; }
  .row.text { font-size: 15px; }
  .row.text.dim { color: #94a3b8; }
  .row.text.accent { font-weight: 700; }
  .sub { color: #7bd8f0; font-size: 13px; letter-spacing: .03em; text-transform: uppercase; }
  .note { border-radius: 10px; padding: 10px 12px; font-size: 13.5px; }
  .note.info { background: rgba(56,189,248,.12); color: #bae6fd; }
  .note.ok   { background: rgba(34,197,94,.13); color: #bbf7d0; }
  .note.warn { background: rgba(250,204,21,.12); color: #fef08a; }
  .note.error{ background: rgba(248,113,113,.14); color: #fecaca; }
  .field { display: flex; flex-direction: column; gap: 4px; }
  .field label { font-size: 12.5px; color: #94a3b8; font-weight: 600; }
  input[type=text], input[type=password] { background:#0e1626; border:1px solid rgba(255,255,255,.12);
      border-radius:8px; padding:9px 11px; color:#e6ebf2; font:14px inherit; outline:none; }
  input:focus { border-color:#38bdf8; box-shadow:0 0 0 2px rgba(56,189,248,.18); }
  .hint { font-size:12px; color:#64748b; }
  .opts { display:flex; flex-direction:column; gap:6px; }
  .opt { display:flex; align-items:flex-start; gap:10px; background:#0e1626; border:1px solid rgba(255,255,255,.09);
         border-radius:10px; padding:9px 12px; cursor:pointer; }
  .opt:hover { border-color:#38bdf8; }
  .opt.sel { border-color:#22c55e; background:rgba(34,197,94,.08); }
  .opt .radio { width:14px; height:14px; border-radius:50%; border:2px solid #475569; margin-top:3px; flex:none; }
  .opt.sel .radio { border-color:#22c55e; background:#22c55e; }
  .opt .r { display:flex; flex-direction:column; }
  .opt .r .l { font-weight:600; }
  .opt .r .d { font-size:12px; color:#94a3b8; }
  .check { display:flex; align-items:center; gap:9px; cursor:pointer; background:#0e1626;
           border:1px solid rgba(255,255,255,.09); border-radius:10px; padding:9px 12px; }
  .check .box { width:16px; height:16px; border-radius:5px; border:2px solid #475569; flex:none; }
  .check.on .box { border-color:#22c55e; background:#22c55e; }
  .pbar { height:8px; background:#0e1626; border-radius:99px; overflow:hidden; }
  .pbar .fill { height:100%; background:linear-gradient(90deg,#38bdf8,#22c55e); transition:width .2s; }
  .pbar .indet { width:40%; height:100%; background:linear-gradient(90deg,#38bdf8,#22c55e);
                 animation: slide 1.1s linear infinite; }
  @keyframes slide { from { margin-left:-40%; } to { margin-left:100%; } }
  .plabel { font-size:12.5px; color:#94a3b8; margin-bottom:4px; }
  .progtext { font-size:14px; margin-top:8px; white-space:pre-wrap; color:#cbd5e1; max-height:140px; overflow:auto; }
  .footer { display:flex; gap:10px; justify-content:flex-end; margin-top:24px; }
  button { font:inherit; border-radius:10px; padding:9px 18px; cursor:pointer; border:1px solid rgba(255,255,255,.14);
           background:#16213a; color:#e6ebf2; }
  button.primary { background:linear-gradient(90deg,#22c55e,#16a34a); border-color:transparent; font-weight:700; color:#04120a; }
  button.danger { background:rgba(248,113,113,.14); border-color:rgba(248,113,113,.4); color:#fecaca; }
  button:hover { filter:brightness(1.12); }
  button:disabled, .disabled { opacity:.5; pointer-events:none; }
  .done { text-align:center; padding:40px 0; }
  .done .big { font-size:52px; }
  .busyline { display:flex; align-items:center; gap:10px; color:#7bd8f0; margin-top:14px; }
  .spin { width:16px; height:16px; border-radius:50%; border:2px solid #7bd8f0; border-top-color:transparent; animation: rot .8s linear infinite; }
  @keyframes rot { to { transform: rotate(360deg); } }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1 class="logo">SAYRI</h1>
    <h1 id="title"></h1>
    <div class="step" id="step"></div>
  </header>
  <div class="subtitle" id="subtitle"></div>
  <div class="body" id="body"></div>
  <div class="footer" id="footer"></div>
  <div id="stat"></div>
</div>
<script>
(function () {
  "use strict";
  var TRANSPORT = __TRANSPORT__;
  var state = {};          // field id -> value
  var busy = false;
  var pollTimer = null;

  function post(msg) {
    if (TRANSPORT.kind === "webkit") {
      try {
        if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.sayri) {
          window.webkit.messageHandlers.sayri.postMessage(JSON.stringify(msg));
          return;
        }
      } catch (e) { /* fallthrough */ }
      window.parent && window.parent.postMessage(msg, "*");
    } else if (TRANSPORT.kind === "fetch") {
      fetch(TRANSPORT.endpoint || "/api/event", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(msg)
      }).then(function (r) { return r.json(); }).then(function (screen) {
        if (screen && screen.title) app.render(screen);
      }).catch(function (err) { app.render({ title: "Error", body: [{ t: "note", text: String(err), level: "error" }], footer: [] }); });
    }
  }

  function el(tag, cls, html) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html !== undefined) n.innerHTML = html;
    return n;
  }

  function collect() {
    var out = {};
    for (var k in state) out[k] = state[k];
    return out;
  }

  function inputWidgetsExist(src) {
    var all = src.body.concat(src.footer || []);
    for (var i = 0; i < all.length; i++) {
      var t = all[i].t;
      if (t === "entry" || t === "select" || t === "check") return true;
    }
    return false;
  }

  var app = {
    render: function (src) {
      document.getElementById("title").textContent = src.title || "Sayri";
      document.getElementById("step").textContent = src.step || "";
      document.getElementById("subtitle").textContent = src.subtitle || "";
      document.getElementById("stat").textContent = "";
      busy = !!src.busy;
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
      if (busy) {
        pollTimer = setInterval(function () { post({ type: "poll" }); }, 900);
      }

      var body = document.getElementById("body");
      body.innerHTML = "";
      (src.body || []).forEach(function (node) { body.appendChild(renderNode(src, node)); });

      var foot = document.getElementById("footer");
      foot.innerHTML = "";
      if (src.done) {
        foot.appendChild(buttonNode({ id: "close", label: "Close", kind: "secondary" }));
        return;
      }
      (src.footer || []).forEach(function (b) { foot.appendChild(buttonNode(b)); });

      // seed initial field state
      (src.body || []).concat(src.footer || []).forEach(function (n) {
        var t = n.t;
        if (t === "entry" && !(n.id in state)) state[n.id] = n.default || "";
        if (t === "check" && !(n.id in state)) state[n.id] = !!n.default;
        if (t === "select") {
          var optSel = (n.options || []).filter(function (o) { return o.value === n.default; });
          if (!(n.id in state)) state[n.id] = (optSel.length ? n.default : (n.options || [])[0] && n.options[0].value);
        }
      });
    },

    setBusy: function (b) {
      if (b) { post({ type: "poll" }); }
    }
  };

  function renderNode(src, node) {
    var t = node.t;
    if (t === "text") {
      var p = el("div", "row text" + (node.accent ? " accent" : "") + (node.dim ? " dim" : ""));
      p.innerHTML = esc(node.text).replace(/\\n/g, "<br>");
      return p;
    }
    if (t === "sub") return el("div", "sub", esc(node.text));
    if (t === "note") return el("div", "note " + node.level, esc(node.text));
    if (t === "spacer") return el("div", "", "");
    if (t === "progress") {
      var box = el("div", "", "");
      var lab = el("div", "plabel", esc(node.label || ""));
      var bar = el("div", "pbar");
      if (node.pct === null || node.pct === undefined) {
        bar.appendChild(el("div", "indet", ""));
      } else {
        var fill = document.createElement("div");
        fill.className = "fill";
        fill.style.width = Math.max(0, Math.min(100, node.pct * 100)) + "%";
        bar.appendChild(fill);
      }
      box.appendChild(lab); box.appendChild(bar);
      return box;
    }
    if (t === "entry") {
      var f = el("div", "field");
      if (node.label) f.appendChild(el("label", "", esc(node.label)));
      var inp = document.createElement("input");
      inp.type = node.secret ? "password" : "text";
      inp.placeholder = node.placeholder || "";
      inp.value = state[node.id] || "";
      inp.addEventListener("input", function () { state[node.id] = inp.value; });
      inp.addEventListener("keydown", function (e) {
        if (e.key === "Enter") post({ type: "submit", value: collect() });
      });
      f.appendChild(inp);
      if (node.hint) f.appendChild(el("div", "hint", esc(node.hint)));
      return f;
    }
    if (t === "select") {
      var sf = el("div", "field");
      if (node.label) sf.appendChild(el("label", "", esc(node.label)));
      var opts = el("div", "opts");
      var i, opt;
      for (i = 0; i < node.options.length; i++) {
        (function (o) {
          opt = el("div", "opt" + (o.value === state[node.id] ? " sel" : ""));
          opt.appendChild(el("div", "radio", ""));
          var r = el("div", "r");
          r.appendChild(el("div", "l", esc(o.label || o.value)));
          if (o.desc) r.appendChild(el("div", "d", esc(o.desc)));
          opt.appendChild(r);
          opt.addEventListener("click", function () {
            state[node.id] = o.value;
            opts.querySelectorAll(".opt").forEach(function (x) { x.classList.remove("sel"); });
            opt.classList.add("sel");
          });
          opts.appendChild(opt);
        })(node.options[i]);
      }
      sf.appendChild(opts);
      return sf;
    }
    if (t === "check") {
      var c = el("div", "check" + (state[node.id] ? " on" : ""));
      c.appendChild(el("div", "box", ""));
      c.appendChild(el("span", "", esc(node.label)));
      c.addEventListener("click", function () {
        state[node.id] = !state[node.id];
        c.classList.toggle("on", state[node.id]);
      });
      return c;
    }
    if (t === "button") return buttonNode(node);
    return el("div", "row text", esc(JSON.stringify(node)));
  }

  function buttonNode(b) {
    var btn = el("button", b.kind || "secondary", esc(b.label));
    btn.addEventListener("click", function () {
      post({ type: "action", widget: b.id, value: collect() });
    });
    return btn;
  }

  function esc(s) {
    var d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  window.xui = {
    render: app.render,
    setBusy: app.setBusy,
    state: function () { return collect(); }
  };

  if (TRANSPORT.kind === "webkit") {
    try {
      if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.sayri) {
        window.webkit.messageHandlers.sayri.postMessage(JSON.stringify({ type: "xui_ready" }));
      }
    } catch (e) { /* host will push initial screen */ }
  }

  app.render(__INITIAL__);
})();
</script>
</body>
</html>
"""