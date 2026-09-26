"""Tests for the Sayri welcome wizard. Run via tests/run-tests.sh."""

import io
import os
import sys
import time

_TMP = os.path.join(os.path.dirname(__file__), "_wizard_tmp")
os.environ["SAYRI_CONFIG_DIR"] = os.path.join(_TMP, "config")
os.environ["SAYRI_STATE_DIR"] = os.path.join(_TMP, "state")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "share", "sayri", "lib"))

from sayri import wizard  # noqa: E402
from sayri import xui  # noqa: E402


def test_wizard_defaults():
    app = wizard.WelcomeApp()
    assert app.idx == 0
    assert app.val["provider"] == "ollama"
    assert app.val["voice"] == ""
    assert app.val["stt_size"] == "base"
    assert app.task.running is False
    scr = app.render()
    assert scr["id"] == "welcome"
    assert scr["step"] == "7/7"
    labels = {b["id"] for b in scr["footer"]}
    assert labels == {"start", "skip"}


def test_module_constant_shapes():
    assert wizard.LANGUAGES[0]["value"] == "es_ES"
    for opt in wizard.LANGUAGES:
        assert opt["value"] and opt["label"]
    for p in wizard.PROVIDERS:
        assert p["value"] and p["label"] and "base_url" in p and "model" in p
    assert wizard.PROVIDERS[0]["value"] == "ollama"
    for s in wizard.WHISPER_SIZES:
        assert s["value"] and s["label"]


def test_dispatch_until_done_writes_config():
    # walk the full flow with the shared host dispatch protocol
    app = wizard.WelcomeApp()
    scr = app.dispatch({"type": "action", "widget": "start", "value": {}})
    assert scr["id"] == "language"

    scr = app.dispatch({"type": "submit", "value": {"language": "es_ES"}})
    assert scr["id"] == "provider"

    scr = app.dispatch({"type": "submit", "value": {"provider": "ollama"}})
    assert scr["id"] == "provider_details"

    scr = app.dispatch({"type": "submit",
                        "value": {"base_url": "http://127.0.0.1:11434/v1", "model": "llama3.2"}})
    assert scr["id"] == "voice"

    scr = app.dispatch({"type": "submit",
                        "value": {"voice": "sharvard", "download_voice": False}})
    assert scr["id"] == "stt"

    scr = app.dispatch({"type": "submit", "value": {"stt_size": "base", "download_stt": False}})
    assert scr["id"] == "review"

    scr = app.dispatch({"type": "action", "widget": "finish", "value": {}})
    assert scr["done"] is True

    conf = os.path.join(os.environ["SAYRI_CONFIG_DIR"], "sayri.conf")
    assert os.path.exists(conf)
    text = open(conf, encoding="utf-8").read()
    assert "setup_complete=true" in text
    assert "language=es" in text
    assert "language=es_ES" in text
    assert "voice=sharvard" in text
    assert "quality=medium" in text
    assert "model_size=base" in text
    assert "base_url=http://127.0.0.1:11434/v1" in text


def test_skip_writes_ui_flag():
    app = wizard.WelcomeApp()
    scr = app.dispatch({"type": "action", "widget": "skip", "value": {}})
    assert scr["done"] is True
    conf = os.path.join(os.environ["SAYRI_CONFIG_DIR"], "sayri.conf")
    assert os.path.exists(conf)
    assert "setup_complete=true" in open(conf, encoding="utf-8").read()


def test_next_action_advances_like_submit():
    app = wizard.WelcomeApp()
    scr = app.dispatch({"type": "action", "widget": "start", "value": {}})
    scr = app.dispatch({"type": "action", "widget": "next", "value": {"language": "en_US"}})
    assert scr["id"] == "provider"
    assert app.val["language"] == "en_US"
    scr = app.dispatch({"type": "action", "widget": "back", "value": {}})
    assert scr["id"] == "language"


def test_back_keeps_language_values():
    app = wizard.WelcomeApp()
    app.dispatch({"type": "action", "widget": "start", "value": {}})
    app.dispatch({"type": "submit", "value": {"language": "fr_FR"}})
    assert app.val["language"] == "fr_FR"
    scr = app.dispatch({"type": "action", "widget": "back", "value": {}})
    assert scr["id"] == "language"


def test_lang_tag_mapping():
    for full, tag in [("es_ES", "es"), ("es_MX", "es"), ("en_US", "en"),
                      ("en_GB", "en"), ("ca_ES", "ca"), ("zh_CN", "zh")]:
        app = wizard.WelcomeApp()
        app.val["language"] = full
        assert app._lang_tag() == tag, full


def test_voices_and_quality_from_catalog():
    app = wizard.WelcomeApp()
    opts = app._voices_for("es_ES")
    assert opts, "expected es_ES Piper voices"
    assert any(o["value"] == "sharvard" for o in opts)
    app.val["voice"] = "sharvard"
    assert app._voice_quality() == "medium"
    assert app._voice_done() is False  # not installed in the temp state dir
    app.val["voice"] = "nope-missing"
    assert app._voice_quality() == "medium"  # unknown voice falls back


def test_build_html_and_install():
    html = wizard.build_html({"kind": "fetch", "endpoint": "/api/event"})
    assert "window.xui" in html
    assert '"/api/event"' in html
    target = wizard.install_html_to()
    assert target.endswith("welcome.html")
    assert os.path.exists(target)
    assert "window.xui" in open(target, encoding="utf-8").read()


def test_offline_resolution_degrades_gracefully():
    app = wizard.WelcomeApp()
    app.val["language"] = "xx_XX"
    # unknown languages never crash; they just yield no voice options
    assert isinstance(app._voices_for("xx_XX"), list)
    assert app._voice_quality() == "medium"
    assert app._lang_tag() == "xx"


def test_close_and_quit_return_none():
    app = wizard.WelcomeApp()
    assert app.dispatch({"type": "action", "widget": "close", "value": {}}) is None
    assert app.dispatch({"type": "action", "widget": "quit", "value": {}}) is None


def test_progress_screen_shape_when_busy():
    import threading
    import time

    app = wizard.WelcomeApp()
    gate = threading.Event()

    def fn(p, l):
        p(0.2)
        gate.wait(2)
        return "ok"

    app._launch_task("Downloading…", fn, None)
    scr = app.dispatch({"type": "poll"})
    assert scr["busy"] is True
    assert "Working" in scr["title"]
    assert any(n.get("t") == "progress" for n in scr["body"])
    gate.set()
    while app.task.running:
        time.sleep(0.01)
    scr = app.dispatch({"type": "poll"})
    assert scr["busy"] is False


def test_run_cli_eof_and_complete():
    code = wizard.run_cli(stream=io.StringIO(""), out=io.StringIO())
    assert code == 0


def test_prism_wizard_flow_persists_plugin_config():
    # provider "bonsai" (Prism ML) opens family → quant → size → overview
    # between provider details and voice, and persists prismml.json (English).
    import json
    app = wizard.WelcomeApp()
    app.dispatch({"type": "action", "widget": "start", "value": {}})
    scr = app.dispatch({"type": "submit", "value": {"language": "en_US"}})
    assert scr["id"] == "provider"
    scr = app.dispatch({"type": "submit", "value": {"provider": "bonsai"}})
    assert scr["id"] == "prism_family"   # prism slides come BEFORE the model details
    assert scr["step"] == "3/11"
    scr = app.dispatch({"type": "submit", "value": {"prism_family": "ternary"}})
    assert scr["id"] == "prism_quant"
    assert [o["value"] for o in scr["body"][0]["options"]] == ["pq2_0", "q2_0_g64", "q2_0", "f16"]
    scr = app.dispatch({"type": "submit", "value": {"prism_quant": "f16"}})
    assert scr["id"] == "prism_size"
    scr = app.dispatch({"type": "submit", "value": {"prism_size": "27B"}})
    assert scr["id"] == "prism_overview"
    assert "what is missing" in scr["title"]
    scr = app.dispatch({"type": "submit", "value": {}})
    assert scr["id"] == "provider_details"   # after the prism slides
    # autofill comes from prismml.json (the configured family/size + default port)
    entries = {e["id"]: e.get("default", "") for e in scr["body"] if e.get("t") == "entry"}
    assert entries["base_url"] == "http://127.0.0.1:8080/v1", entries
    assert entries["model"] == "ternary-27b", entries
    scr = app.dispatch({"type": "submit", "value": {
        "base_url": "http://127.0.0.1:8080/v1", "model": "ternary-27b", "api_key": ""}})
    cfg_path = os.path.join(os.environ["SAYRI_CONFIG_DIR"], "prismml.json")
    cfg = json.loads(open(cfg_path).read())
    assert cfg["family"] == "ternary" and cfg["quant"] == "f16" and cfg["size"] == "27B"
    scr = app.dispatch({"type": "submit", "value": {"voice": "", "download_voice": False}})
    assert scr["id"] == "stt"
    scr = app.dispatch({"type": "submit", "value": {"stt_size": "base", "download_stt": False}})
    assert scr["id"] == "review"
    assert scr["step"] == "10/11"


def test_prism_apply_download_launches_task():
    # "Apply & download" on Prism must start a real download task (binary+model)
    # and stream its progress, instead of doing nothing.
    import json as _json
    plugins = os.path.join(os.environ["SAYRI_CONFIG_DIR"], "plugins")
    pdir = os.path.join(plugins, "sayri-prismml")
    os.makedirs(pdir, exist_ok=True)
    with open(os.path.join(pdir, "manifest.json"), "w", encoding="utf-8") as fh:
        _json.dump({"id": "sayri-prismml", "entrypoint": "gateway.py"}, fh)
    fake = (
        "import sys\n"
        "sys.stdout.write('\\r 50%')\n"
        "sys.stdout.flush()\n"
        "sys.stdout.write('\\nfake download ok\\n')\n"
        "sys.stdout.flush()\n"
    )
    with open(os.path.join(pdir, "gateway.py"), "w", encoding="utf-8") as fh:
        fh.write(fake)

    app = wizard.WelcomeApp()
    app.val.update({"provider": "bonsai", "prism_family": "ternary",
                    "prism_size": "8B", "prism_quant": ""})
    scr = app.dispatch({"type": "action", "widget": "apply", "value": {}})
    assert scr["id"] == "busy", scr["id"]
    for _ in range(200):
        poll = app.dispatch({"type": "poll"})
        if not poll.get("busy"):
            break
        time.sleep(0.01)
    info = app.task.poll()
    assert not info["running"]
    assert "Everything is downloaded" in "\n".join(info["log"])
    ok = app.dispatch({"type": "poll"})
    assert ok.get("busy") is False


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
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"  FAIL {name}: {type(exc).__name__}: {exc}")
    sys.exit(1 if failures else 0)