"""Tests for the xui cross-renderer micro-framework. Run via tests/run-tests.sh."""

import io
import os
import sys
import time

_TMP = os.path.join(os.path.dirname(__file__), "_xui_tmp")
os.environ["SAYRI_CONFIG_DIR"] = os.path.join(_TMP, "config")
os.environ["SAYRI_STATE_DIR"] = os.path.join(_TMP, "state")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "share", "sayri", "lib"))

from sayri import xui  # noqa: E402
from sayri import wizard  # noqa: E402


def test_widget_builders():
    assert xui.text("hi") == {"t": "text", "text": "hi", "accent": False, "dim": False}
    assert xui.text("hi", accent=True)["accent"] is True
    assert xui.sub("S")["t"] == "sub"
    assert xui.note("n", "warn")["level"] == "warn"
    assert xui.note("n", "bogus")["level"] == "info"  # invalid level normalises
    assert xui.button("go", "Go", "primary") == {"t": "button", "id": "go", "label": "Go",
                                                 "kind": "primary", "icon": ""}
    assert xui.entry("e", secret=True)["secret"] is True
    assert xui.select("s", "Sel", [{"value": "a", "label": "A"}], "a")["default"] == "a"
    assert xui.check("c", "Chk", True)["default"] is True
    assert xui.progress("p", 0.5)["pct"] == 0.5


def test_screen_shape():
    scr = xui.screen("T", [xui.text("b")], subtitle="sub", footer=[xui.button("ok", "OK")])
    assert scr["id"] == ""
    assert scr["title"] == "T"
    assert scr["subtitle"] == "sub"
    assert scr["busy"] is False and scr["done"] is False
    assert len(scr["body"]) == 1 and scr["footer"][0]["t"] == "button"


def test_collect_defaults():
    scr = xui.screen("T", [
        xui.entry("e", default="foo"),
        xui.select("s", "S", [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}], "b"),
        xui.check("c", "C", True),
    ])
    vals = xui.collect_defaults(scr)
    assert vals == {"e": "foo", "s": "b", "c": True}
    assert "e" in {wid for wid, _ in xui.iter_focusable(scr)}


def test_task_runner():
    tr = xui.TaskRunner()

    def fn(prog, log):
        log(["start"])
        prog(0.4)
        time.sleep(0.05)
        prog(1.0)
        return "ok"

    tr.start("t", fn)
    assert tr.running
    deadline = time.time() + 5
    while tr.running and time.time() < deadline:
        time.sleep(0.01)
    poll = tr.poll()
    assert poll["result"] == "ok"
    assert poll["progress"] == 1.0
    assert "start" in poll["log"]
    assert poll["error"] is None


def test_task_runner_error():
    tr = xui.TaskRunner()

    def fn(_p, _l):
        raise RuntimeError("boom")

    tr.start("t", fn)
    deadline = time.time() + 5
    while tr.running and time.time() < deadline:
        time.sleep(0.01)
    assert tr.poll()["error"] == "boom"


def test_tui_renders_welcome_screen():
    tui = xui.Tui(wizard.WelcomeApp(), stream=io.StringIO(), out=io.StringIO())
    scr = wizard.WelcomeApp().render()  # welcome screen uses dim text + step
    lines = tui._render(scr, width=72)
    assert lines and lines[0].startswith("┌")
    assert any("SAYRI" in line for line in lines)
    assert any("Hi 👋" in line for line in lines)


def test_tui_line_mode_completes_wizard():
    script = "1\n1\n2\n1\n2\n\n\n\n2\n1\nn\n2\n2\nn\n2\n2\n2\n"
    out = io.StringIO()
    code = wizard.run_cli(stream=io.StringIO(script), out=out)
    text = out.getvalue()
    assert code == 0
    # the wizard walked every step and reached the "done" footer
    assert "Welcome to Sayri" in text
    assert "Open Sayri" in text
    assert "Close" in text


def test_tui_eof_terminates():
    t0 = time.time()
    code = wizard.run_cli(stream=io.StringIO(""), out=io.StringIO())
    assert code == 0
    assert time.time() - t0 < 2


def test_html_page():
    scr = wizard.WelcomeApp().render()
    page = xui.wizard_page(scr, title="Sayri", transport={"kind": "webkit"})
    assert '"kind": "webkit"' in page
    assert '"id": "welcome"' in page
    assert "window.xui" in page
    assert "xui_ready" in page

    page2 = xui.wizard_page(scr, transport={"kind": "fetch", "endpoint": "/api/event"})
    assert '"/api/event"' in page2
    assert "fetch(" in page2


def test_tui_fake_host():
    class FakeHost:
        def __init__(self):
            self.n = 0

        def render(self):
            if self.n == 0:
                return xui.screen("One", [xui.text("first")], footer=[xui.button("next", "Continuar")])
            return xui.screen("Two", [xui.text("second")], footer=[xui.button("quit", "Cerrar")])

        def dispatch(self, ev):
            if ev.get("type") == "action" and ev.get("widget") == "next":
                self.n = 1
                return self.render()
            if ev.get("type") == "action" and ev.get("widget") == "quit":
                return None
            return self.render()

    out = io.StringIO()
    code = xui.run_cli(FakeHost(), stream=io.StringIO("\n\n"), out=out)
    assert code == 0
    text = out.getvalue()
    assert "Continuar" in text and "Cerrar" in text  # both screens were driven


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"  FAIL {name}: {exc}")
    sys.exit(1 if failures else 0)