"""Tests for plugin background services (sayri.plugin_service + settings toggle)."""

import json
import os
import sys

_TMP = os.path.join(os.path.dirname(__file__), "_pservice_tmp")
os.environ["SAYRI_CONFIG_DIR"] = os.path.join(_TMP, "config")
os.environ["SAYRI_STATE_DIR"] = os.path.join(_TMP, "state")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "share", "sayri", "lib"))

from sayri import plugin_service  # noqa: E402
from sayri import plugin_settings  # noqa: E402

PLUGIN_ID = "sayri-fake"
PLUGIN_DIR = os.path.join(_TMP, "config", "plugins", PLUGIN_ID)
FLAG_START = os.path.join(PLUGIN_DIR, "started.flag")
FLAG_STOP = os.path.join(PLUGIN_DIR, "stopped.flag")

FAKE_GATEWAY = """#!/usr/bin/env python3
import os, sys
cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
here = os.path.dirname(os.path.abspath(__file__))
if cmd == "start":
    open(os.path.join(here, "started.flag"), "w").write("1")
    print("  server running OK")
    raise SystemExit(0)
if cmd == "stop":
    s = os.path.join(here, "started.flag")
    if os.path.exists(s):
        os.unlink(s)
    open(os.path.join(here, "stopped.flag"), "w").write("1")
    print("  server stopped.")
    raise SystemExit(0)
if cmd == "status":
    if os.path.exists(os.path.join(here, "started.flag")):
        print("server:       running (PID 1)")
    else:
        print("server:       stopped")
    raise SystemExit(0)
raise SystemExit(1)
"""

MANIFEST = {
    "id": PLUGIN_ID,
    "name": "Fake Service",
    "description": "fixture",
    "version": "0.0.1",
    "entrypoint": "gateway.py",
    "authorization": {"mode": "none"},
    "service": {
        "auto_start": True,
        "enabled": True,
        "enabled_label": "Run when Sayri starts",
        "start": ["gateway.py", "start"],
        "stop": ["gateway.py", "stop"],
        "status": ["gateway.py", "status"],
    },
    "ui": {
        "settings_file": "fake.json",
        "settings": [
            {"t": "select", "id": "ctx", "label": "Ctx", "key": "ctx_size",
             "default": "4096", "options": [{"value": "4096", "label": "4k"}]}
        ],
    },
}


def _cleanup():
    import shutil
    shutil.rmtree(_TMP, ignore_errors=True)


def _setup():
    _cleanup()
    os.makedirs(PLUGIN_DIR, exist_ok=True)
    with open(os.path.join(PLUGIN_DIR, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(MANIFEST, fh)
    with open(os.path.join(PLUGIN_DIR, "gateway.py"), "w", encoding="utf-8") as fh:
        fh.write(FAKE_GATEWAY)


def test_service_block_detection():
    assert plugin_service.service_block(MANIFEST) is MANIFEST["service"]
    assert plugin_service.service_block({"id": "x"}) is None
    no_cmd = {"id": "x", "service": {"auto_start": True}}
    assert plugin_service.service_block(no_cmd) is None


def test_enabled_helpers():
    _setup()
    assert plugin_service.enabled_key(MANIFEST) == "enabled"
    assert plugin_service.service_enabled(MANIFEST) is True
    plugin_service.set_service_enabled(MANIFEST, False)
    assert plugin_service.service_enabled(MANIFEST) is False
    plugin_service.set_service_enabled(MANIFEST, True)
    assert plugin_service.service_enabled(MANIFEST) is True


def test_service_running_from_status_output():
    _setup()
    assert plugin_service.service_running(MANIFEST) is False
    with open(FLAG_START, "w", encoding="utf-8") as fh:
        fh.write("1")
    assert plugin_service.service_running(MANIFEST) is True


def test_start_stop_persist_and_run():
    _setup()
    ok, _msg = plugin_service.start_service(MANIFEST)
    assert ok
    assert os.path.isfile(FLAG_START)
    assert plugin_service.service_enabled(MANIFEST) is True
    assert plugin_service.service_running(MANIFEST) is True

    ok, _msg = plugin_service.stop_service(MANIFEST)
    assert ok
    assert os.path.isfile(FLAG_STOP)
    assert plugin_service.service_enabled(MANIFEST) is False
    assert plugin_service.service_running(MANIFEST) is False


def test_auto_start_respects_enabled_flag():
    _setup()
    plugin_service.set_service_enabled(MANIFEST, False)
    plugin_service.auto_start_services()
    assert not os.path.isfile(FLAG_START)

    plugin_service.set_service_enabled(MANIFEST, True)
    plugin_service.auto_start_services()
    assert os.path.isfile(FLAG_START)


def test_settings_form_injects_enabled_toggle():
    _setup()
    app = plugin_settings.SettingsApp(MANIFEST)
    scr = app.render()
    names = {n.get("id") for n in scr["body"] if n.get("id")}
    assert "enabled" in names


def test_settings_save_toggles_service():
    _setup()
    assert not os.path.isfile(FLAG_START)
    # start from enabled-by-default, then switch OFF → stops the service
    app = plugin_settings.SettingsApp(MANIFEST)
    out = app.dispatch({"type": "submit", "value": {"ctx": "4096", "enabled": False}})
    assert out and out.get("done")
    assert os.path.isfile(FLAG_STOP)
    assert plugin_service.service_enabled(MANIFEST) is False
    assert plugin_service.service_running(MANIFEST) is False

    # switch ON again → starts the service and persists enabled
    os.unlink(FLAG_STOP)
    app = plugin_settings.SettingsApp(MANIFEST)
    out = app.dispatch({"type": "submit", "value": {"ctx": "4096", "enabled": True}})
    assert out and out.get("done")
    assert os.path.isfile(FLAG_START)
    assert plugin_service.service_enabled(MANIFEST) is True
    assert plugin_service.service_running(MANIFEST) is True


if __name__ == "__main__":
    _cleanup()
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"  FAIL {name}: {exc}")
    _cleanup()
    print("== test_plugin_service.py", "OK" if not failures else f"FAILED ({failures})", "==")
    sys.exit(1 if failures else 0)