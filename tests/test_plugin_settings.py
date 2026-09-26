"""Tests for declarative plugin settings (sayri.plugin_settings)."""

import json
import os
import sys

_TMP = os.path.join(os.path.dirname(__file__), "_psettings_tmp")
os.environ["SAYRI_CONFIG_DIR"] = os.path.join(_TMP, "config")
os.environ["SAYRI_STATE_DIR"] = os.path.join(_TMP, "state")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "share", "sayri", "lib"))

from sayri import plugin_settings  # noqa: E402

MANIFEST = {
    "id": "sayri-test",
    "name": "Test Plugin",
    "ui": {
        "settings_file": "tplugin.json",
        "sync_instructions": "Ajustes del plugin de prueba.",
        "settings": [
            {"t": "note", "text": "Nota.", "level": "info"},
            {"t": "select", "id": "family", "label": "Family", "key": "family",
             "default": "ternary",
             "options": [{"value": "ternary", "label": "Ternary"},
                         {"value": "bonsai", "label": "Bonsai"}]},
            {"t": "select", "id": "size", "label": "Size", "key": "size",
             "default": "8B",
             "options": [{"value": "27B", "label": "27B"}, {"value": "8B", "label": "8B"}]},
            {"t": "entry", "id": "port", "label": "Port", "key": "port",
             "default": "8080", "hint": "puerto"},
            {"t": "entry", "id": "params_file", "label": "Params", "key": "params_file",
             "placeholder": "/path"},
        ],
    },
}


def _cleanup():
    cfg = os.path.join(_TMP, "config")
    fp = os.path.join(cfg, "tplugin.json")
    if os.path.isfile(fp):
        os.remove(fp)


def test_settings_schema_detection():
    assert plugin_settings.settings_schema(MANIFEST) is MANIFEST["ui"]
    assert plugin_settings.settings_schema({}) is None
    no_form = {"id": "x", "ui": {"chat_url": "http://x"}}
    assert plugin_settings.settings_schema(no_form) is None


def test_settings_file_path():
    fp = plugin_settings.settings_file_path(MANIFEST)
    assert fp.name == "tplugin.json"
    assert str(fp).endswith(os.path.join("config", "tplugin.json"))
    absolute = {"id": "x", "ui": {"settings_file": "/tmp/custom.json"}}
    assert str(plugin_settings.settings_file_path(absolute)) == "/tmp/custom.json"


def test_save_and_read_values():
    _cleanup()
    app = plugin_settings.SettingsApp(MANIFEST)
    out = app.dispatch({"type": "submit", "value": {
        "family": "bonsai", "size": "8B", "port": "8123", "params_file": ""}})
    assert out and out.get("done")
    values = plugin_settings.read_values(MANIFEST)
    assert values["family"] == "bonsai"
    assert values["port"] == "8123"
    assert values["params_file"] == ""
    _cleanup()


def test_defaults_prefill():
    _cleanup()
    app = plugin_settings.SettingsApp(MANIFEST)
    scr = app.render()
    nodes = {n["id"]: n for n in scr["body"] if n.get("id")}
    assert nodes["size"]["default"] == "8B"
    assert nodes["family"]["default"] == "ternary"
    assert nodes["port"]["default"] == "8080"


def test_node_schema_detection():
    assert plugin_settings.settings_schema({}) is None


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
    print("== test_plugin_settings.py", "OK" if not failures else f"FAILED ({failures})", "==")
    sys.exit(1 if failures else 0)