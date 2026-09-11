"""Tests for the bare-`sayri` default entry and `sayri ui default`.

Run via tests/run-tests.sh.
"""

import io
import os
import sys

_TMP = os.path.join(os.path.dirname(__file__), "_cli_default_tmp")
os.environ["SAYRI_CONFIG_DIR"] = os.path.join(_TMP, "config")
os.environ["SAYRI_STATE_DIR"] = os.path.join(_TMP, "state")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "share", "sayri", "lib"))

from sayri import cli  # noqa: E402
from sayri import config as cfg  # noqa: E402


def _capture(fn, *args):
    out = io.StringIO()
    old = sys.stdout
    sys.stdout = out
    try:
        code = fn(*args)
    finally:
        sys.stdout = old
    return code, out.getvalue()


def test_default_entry_runs_welcome_when_unconfigured():
    cfg.config.set("ui", "setup_complete", False, persist=True)
    assert cli.setup_pending() is True
    stdin = sys.stdin
    sys.stdin = io.StringIO("")  # EOF input for the interactive wizard
    try:
        code = cli.main([])
    finally:
        sys.stdin = stdin
    assert code == 0  # welcome wizard EOF


def test_default_entry_prints_help_when_configured():
    cfg.config.set("ui", "setup_complete", True, persist=True)
    assert cli.setup_pending() is False
    code, out = _capture(cli.main, [])
    assert code == 0
    assert "Sayri CLI" in out
    assert "sayri ui default" in out
    assert "Pulsar OS" not in out


def test_ui_default_resolves_to_desktop_plugin():
    cfg.config.set("ui", "default_ui", "desktop", persist=True)
    ui_id = cli._ui_resolve_default()
    assert ui_id == "desktop"
    gateway = cli._ui_plugin_gateway(ui_id)
    assert gateway is not None and gateway.is_file()


def test_help_is_english():
    code, out = _capture(cli.cmd_help, {}, [])
    assert code == 0
    assert "no flags" in out
    for banned in ("texto plano", "sin flags", "asistente de configuración"):
        assert banned not in out


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
    if failures:
        raise SystemExit(f"{failures} test(s) failed")
    print("ALL PASS")