"""Human-friendly description of every Sayri setting.

The GTK Cajita renders its Settings tab by hand: a handful of curated sections
with real labels, dropdowns that say "Always Listening" instead of ``always``,
and one Save button per section. That knowledge was trapped in 500 lines of
widget code, so no other UI could reuse it.

This module is that knowledge as data. It answers three questions for any
front-end (the WebKit companion panel, the orb, a future mobile UI):

* which settings exist, grouped into sections a person can scan;
* what each one is called, and what it does;
* which widget it deserves -- text, password, number, toggle or dropdown, with
  friendly option labels.

Settings the schema does not describe explicitly are still reachable: anything
present in :data:`sayri.config.DEFAULTS` but missing here is appended to a
catch-all section, so adding a key to the config can never make it
unconfigurable. ``tests``/``sayri`` ships a check for that.

Plugin settings live in each plugin's ``manifest.json`` under ``ui.settings``
and are already declarative, so :func:`plugin_sections` converts them rather
than restating them here.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from . import config as _config

# ── shared option lists ───────────────────────────────────────────

# Values first, then the label a person should read. The GTK Cajita has always
# shown these names; keeping them identical means the two UIs feel the same.
STT_MODES = [
    ("wakeword", "Wakeword (only listens after you say the trigger)"),
    ("always", "Always listening"),
    ("manual", "Manual (push to talk)"),
    ("disabled", "Disabled (no voice input)"),
]

STT_MODEL_SIZES = [
    ("tiny", "tiny — fastest, ~75 MB"),
    ("tiny.en", "tiny.en — English, ~75 MB"),
    ("base", "base — balanced, ~142 MB"),
    ("base.en", "base.en — English, ~142 MB"),
    ("small", "small — better, ~466 MB"),
    ("small.en", "small.en — English, ~466 MB"),
    ("medium", "medium — very good, ~1.5 GB"),
    ("medium.en", "medium.en — English, ~1.5 GB"),
    ("large-v3", "large-v3 — best quality, ~3.1 GB"),
]

LANGUAGES = [
    ("auto", "Auto-detect"),
    ("en", "English"),
    ("es", "Spanish"),
    ("ca", "Catalan"),
    ("gl", "Galician"),
    ("eu", "Basque"),
    ("fr", "French"),
    ("de", "German"),
    ("it", "Italian"),
    ("pt", "Portuguese"),
    ("nl", "Dutch"),
    ("pl", "Polish"),
    ("ru", "Russian"),
    ("uk", "Ukrainian"),
    ("sv", "Swedish"),
    ("tr", "Turkish"),
    ("el", "Greek"),
    ("ar", "Arabic"),
    ("hi", "Hindi"),
    ("zh", "Chinese"),
    ("ja", "Japanese"),
    ("ko", "Korean"),
]

# Piper voices as (language, voice, quality), matching downloads.PIPER_VOICES.
PIPER_VOICES: list[tuple[str, str, str]] = [
    ("es_ES", "sharvard", "medium"),
    ("es_ES", "davefx", "medium"),
    ("es_ES", "carlfm", "x_low"),
    ("es_MX", "ald", "medium"),
    ("es_MX", "claude", "high"),
    ("ca_ES", "upc_ona", "medium"),
    ("ca_ES", "upc_pau", "medium"),
    ("en_US", "amy", "medium"),
    ("en_US", "lessac", "medium"),
    ("en_US", "joe", "medium"),
    ("en_US", "ryan", "high"),
    ("en_US", "kathleen", "low"),
    ("en_GB", "alan", "medium"),
    ("en_GB", "alba", "medium"),
    ("en_GB", "northern_english_male", "medium"),
    ("en_GB", "cori", "medium"),
    ("fr_FR", "siwis", "medium"),
    ("fr_FR", "upmc", "medium"),
    ("de_DE", "thorsten", "medium"),
    ("de_DE", "ramona", "low"),
    ("it_IT", "paola", "medium"),
    ("pt_BR", "faber", "medium"),
    ("pt_BR", "edresson", "low"),
    ("nl_NL", "mls_7432", "low"),
    ("ru_RU", "irina", "medium"),
    ("pl_PL", "darkman", "medium"),
    ("zh_CN", "huayan", "medium"),
    ("sv_SE", "nst", "medium"),
    ("uk_UA", "lada", "medium"),
    ("tr_TR", "fdf", "medium"),
    ("el_GR", "rapunzelina", "low"),
    ("ar_JO", "kareem", "medium"),
]

AUTOSTART_MODES = [
    ("ui", "Start the desktop UI and the daemon at login"),
    ("daemon", "Start only the daemon (CLI, gateways) at login"),
    ("off", "Do not start anything at login"),
]

# ── the schema ────────────────────────────────────────────────────
#
# Each field is a plain dict so it can go straight over IPC.
#
#   key      dotted config path this field edits ("provider.api_key")
#   reads    config paths whose value forms the widget's current value
#   writes   config paths the saved value is split into (defaults to [key])
#   kind     text | password | number | toggle | choice | textarea
#   save     "now" applies the moment it changes (toggles, dropdowns),
#            "section" waits for the section's Save button (typed values) --
#            the same split the GTK Cajita makes
#   options  [{"value": ..., "label": ...}] for kind == "choice"

_SECTIONS: list[dict[str, Any]] = [
    {
        "id": "llm",
        "title": "LLM provider",
        "subtitle": "Where Sayri sends your prompts.",
        "save_label": "Save provider settings",
        "fields": [
            {
                "key": "provider.base_url",
                "label": "Base URL",
                "hint": "OpenAI-compatible endpoint, e.g. https://api.mistral.ai/v1",
                "kind": "text",
                "save": "section",
            },
            {
                "key": "provider.api_key",
                "label": "API key",
                "hint": "Stored obfuscated. Leave blank to keep the current one.",
                "kind": "password",
                "secret": True,
                "save": "section",
            },
            {
                "key": "provider.model",
                "label": "Model",
                "hint": "Model name as your provider spells it.",
                "kind": "text",
                "save": "section",
            },
        ],
    },
    {
        "id": "assistant",
        "title": "Assistant",
        "subtitle": "How Sayri answers.",
        "save_label": "Save assistant settings",
        "fields": [
            {
                "key": "provider.system_prompt",
                "label": "System prompt",
                "hint": "Sets the persona and the answer style.",
                "kind": "textarea",
                "save": "section",
            },
            {
                "key": "provider.agent_mode",
                "label": "Agent mode",
                "hint": "Lets Sayri call tools and run commands instead of only answering.",
                "kind": "toggle",
                "save": "now",
            },
            {
                "key": "provider.temperature",
                "label": "Creativity",
                "hint": "0 is factual and repetitive, 1 is inventive. 0.7 is a good middle.",
                "kind": "number",
                "min": 0.0,
                "max": 2.0,
                "step": 0.05,
                "save": "section",
            },
            {
                "key": "provider.max_tokens",
                "label": "Answer length",
                "hint": "Maximum tokens per reply.",
                "kind": "number",
                "min": 64,
                "max": 32768,
                "step": 64,
                "save": "section",
            },
            {
                "key": "provider.stream",
                "label": "Stream answers",
                "hint": "Start speaking before the whole reply is generated.",
                "kind": "toggle",
                "save": "now",
            },
            {
                "key": "provider.timeout",
                "label": "Timeout (seconds)",
                "hint": "Give up on a provider that stops responding.",
                "kind": "number",
                "min": 5,
                "max": 600,
                "step": 5,
                "save": "section",
            },
            {
                "key": "provider.strip_patterns",
                "label": "Hidden patterns",
                "hint": "Regex fragments removed from the reply before it is spoken, e.g. <thought>.*?</thought>",
                "kind": "text",
                "save": "section",
            },
        ],
    },
    {
        "id": "speech_input",
        "title": "Speech recognition",
        "subtitle": "How Sayri hears you (Whisper).",
        "save_label": "Save speech settings",
        "fields": [
            {
                "key": "stt.mode",
                "label": "Listening mode",
                "kind": "choice",
                "options": [{"value": v, "label": lbl} for v, lbl in STT_MODES],
                "save": "now",
                "restart": True,
            },
            {
                "key": "stt.wake_word",
                "label": "Wake word",
                "hint": "Say this to get Sayri's attention in wakeword mode.",
                "kind": "text",
                "save": "section",
            },
            {
                "key": "stt.model_size",
                "label": "Whisper model",
                "kind": "choice",
                "options": [{"value": v, "label": lbl} for v, lbl in STT_MODEL_SIZES],
                "save": "now",
                # Reading this field also tells the user whether the model is
                # on disk yet, and offers to fetch it.
                "asset": {"kind": "whisper_model", "arg": "model_size", "size_arg": "model_size"},
            },
            {
                "key": "stt.language",
                "label": "Spoken language",
                "kind": "choice",
                "options": [{"value": v, "label": lbl} for v, lbl in LANGUAGES],
                "save": "now",
            },
            {
                "key": "stt.mic_device",
                "label": "Microphone",
                "hint": "Leave blank to use the system default.",
                "kind": "text",
                "save": "section",
            },
            {
                "key": "stt.silence_ms",
                "label": "Silence before answering (ms)",
                "hint": "How long Sayri waits after you stop talking.",
                "kind": "number",
                "min": 200,
                "max": 5000,
                "step": 100,
                "save": "section",
            },
            {
                "key": "stt.live_transcript",
                "label": "Show the live transcript",
                "hint": "Display what you are saying while you say it.",
                "kind": "toggle",
                "save": "now",
            },
        ],
    },
    {
        "id": "voice",
        "title": "Voice output",
        "subtitle": "How Sayri sounds (Piper).",
        "save_label": "Save voice settings",
        "fields": [
            {
                "key": "tts.enabled",
                "label": "Speak answers out loud",
                "kind": "toggle",
                "save": "now",
            },
            {
                # A Piper voice is really a (language, voice, quality) triple,
                # but nobody should have to pick three numbers to change their
                # voice. Offer the triple as one choice and let the daemon split
                # it back into the three config keys on save.
                "key": "tts.voice_pick",
                "reads": ["tts.language", "tts.voice", "tts.quality"],
                "writes": ["tts.language", "tts.voice", "tts.quality"],
                "label": "Voice",
                "kind": "choice",
                "options": [
                    {"value": f"{lang}|{voice}|{quality}",
                     "label": f"{lang}: {voice} ({quality})"}
                    for lang, voice, quality in PIPER_VOICES
                ],
                "save": "now",
                "asset": {"kind": "piper_voice", "arg": "voice_pick",
                          "language_arg": "language", "voice_arg": "voice",
                          "quality_arg": "quality"},
            },
            {
                "key": "tts.speed",
                "label": "Speaking speed",
                "hint": "1.0 is the voice's natural pace.",
                "kind": "number",
                "min": 0.5,
                "max": 2.0,
                "step": 0.05,
                "save": "section",
            },
        ],
    },
    {
        "id": "interface",
        "title": "Desktop & tray",
        "subtitle": "How Sayri appears on your desktop.",
        "save_label": "Save interface settings",
        "fields": [
            {
                "key": "ui.default_ui",
                "label": "Default interface",
                "hint": "Which UI the launcher opens.",
                "kind": "choice",
                "options": [],          # filled in from the installed UIs
                "dynamic_options": "ui_list",
                "save": "now",
            },
            {
                "key": "ui.autostart",
                "label": "Start Sayri when I log in",
                "kind": "toggle",
                "save": "now",
            },
            {
                "key": "ui.autostart_mode",
                "label": "What to start",
                "kind": "choice",
                "options": [{"value": v, "label": lbl} for v, lbl in AUTOSTART_MODES],
                "save": "now",
            },
            {
                "key": "ui.orb_size",
                "label": "Orb size",
                "kind": "number",
                "min": 80,
                "max": 400,
                "step": 10,
                "save": "section",
            },
            {
                "key": "ui.orb_position",
                "label": "Orb position",
                "kind": "choice",
                "options": [
                    {"value": "top-right", "label": "Top right"},
                    {"value": "top-left", "label": "Top left"},
                    {"value": "bottom-right", "label": "Bottom right"},
                    {"value": "bottom-left", "label": "Bottom left"},
                ],
                "save": "section",
            },
            {
                "key": "ui.always_on_top",
                "label": "Keep Sayri above other windows",
                "kind": "toggle",
                "save": "now",
            },
            {
                "key": "ui.bubble_visible",
                "label": "Show the speech bubble",
                "kind": "toggle",
                "save": "now",
            },
        ],
    },
]

# `ui.setup_complete` is written by the first-run wizard, not by a person, so
# it is deliberately not offered as a setting.

_HIDDEN_KEYS = frozenset({"ui.setup_complete"})


def _described_keys() -> set[str]:
    """Every config key a field covers, directly or through reads/writes."""
    keys: set[str] = set()
    for section in _SECTIONS:
        for field in section["fields"]:
            keys.add(field["key"])
            # A composite field (e.g. the Piper voice triple) is the only place
            # its underlying keys are configured, so they count as described.
            keys.update(field.get("reads") or ())
            keys.update(field.get("writes") or ())
    return keys


def undeclared_keys() -> list[str]:
    """Config keys this schema does not describe (excluding hidden ones)."""
    missing = []
    for group, keys in _config.DEFAULTS.items():
        for key in keys:
            dotted = f"{group}.{key}"
            if dotted in _HIDDEN_KEYS:
                continue
            if dotted not in _described_keys():
                missing.append(dotted)
    return sorted(missing)


def _default_kind(key: str) -> str:
    """Best-guess widget for a key nobody described."""
    type_name = _config._TYPES.get(key.split(".", 1)[0], {}).get(key.split(".", 1)[1], "string")
    if type_name == "bool":
        return "toggle"
    if type_name in ("int", "double"):
        return "number"
    return "text"


def _raw_value(key: str) -> Any:
    group, _, name = key.partition(".")
    return _config.config.get(group, name)


def _field_value(field: dict[str, Any]) -> Any:
    """Current value of one field, honouring composite (multi-key) fields."""
    reads = field.get("reads") or [field["key"]]
    values = [_raw_value(k) for k in reads]
    if len(reads) == 1:
        value = values[0]
    else:
        value = "|".join("" if v is None else str(v) for v in values)
    if field.get("secret") and isinstance(value, str) and value:
        # Never ship the credential itself; the UI only needs to know it is set.
        value = _mask(value)
    return value


def _mask(value: str) -> str:
    if len(value) <= 8:
        return "***"
    return f"{value[:3]}...{value[-3:]}"


def _dynamic_options(field: dict[str, Any]) -> list[dict[str, Any]]:
    """Fill a field's dropdown from live data (e.g. the installed UIs)."""
    source = field.get("dynamic_options")
    if not source:
        return field.get("options", [])
    try:
        from .cli import _list_ui_plugins
    except Exception:
        return field.get("options", [])
    try:
        plugins = _list_ui_plugins()
    except Exception:
        return field.get("options", [])
    options = []
    for plugin in plugins:
        label = plugin.get("name") or plugin.get("id") or ""
        if plugin.get("running"):
            label += "  (running)"
        options.append({"value": plugin.get("id", ""), "label": label})
    return options


def _piper_size(language: str, voice: str, quality: str) -> str:
    for entry in _downloads().PIPER_VOICES.get(language, []):
        if entry["voice"] == voice and entry["quality"] == quality:
            return str(entry.get("size", ""))
    return ""


def _downloads():
    from . import downloads as module
    return module


def field_asset(field: dict[str, Any], value: Any) -> Optional[dict[str, Any]]:
    """Describe the local file a field's current value depends on.

    Choosing a Whisper model or a Piper voice is only half the job: the file
    also has to be on disk, or the feature fails quietly. Reporting that next to
    the dropdown lets any UI offer the download in place, instead of leaving a
    person to discover the problem from a silent non-working voice.
    """
    asset = field.get("asset")
    if not asset or value in (None, ""):
        return None
    try:
        downloads = _downloads()
    except Exception:  # noqa: BLE001 - a missing optional dep must not break settings
        return None

    if asset["kind"] == "whisper_model":
        size = str(value)
        if size not in downloads.WHISPER_MODELS:
            return None
        language = str(_raw_value("stt.language") or "en")
        if language == "auto":
            # "auto" transcribes English by default in practice; that is also
            # the only model Sayri can fall back to.
            language = "en"
        path = downloads.whisper_model_path(size, language)
        return {
            "kind": "whisper_model",
            "downloaded": os.path.isfile(path),
            "path": path,
            "label": f"Whisper {size}",
            "size": str(downloads.WHISPER_MODELS[size].get("size", "")),
            "params": {"model_size": size, "language": language},
        }

    if asset["kind"] == "piper_voice":
        parts = str(value).split("|")
        if len(parts) != 3 or parts[0] not in downloads.PIPER_VOICES:
            return None
        language, voice, quality = parts
        return {
            "kind": "piper_voice",
            "downloaded": downloads.has_piper_voice(language, voice, quality),
            "path": downloads.piper_voice_path(language, voice, quality),
            "label": f"Piper {language}: {voice}",
            "size": _piper_size(language, voice, quality),
            "params": {"language": language, "voice": voice, "quality": quality},
        }

    return None


def schema() -> list[dict[str, Any]]:
    """Return the settings schema with current values, ready for JSON.

    The returned list always ends with an "Other settings" section listing any
    config key the curated sections did not cover, so the surface stays
    complete even when a key is added to the config without a label here.
    """
    extras = undeclared_keys()

    out: list[dict[str, Any]] = []
    for section in _SECTIONS:
        fields = []
        for field in section["fields"]:
            rendered = dict(field)
            value = _field_value(field)
            rendered["value"] = value
            if field.get("dynamic_options"):
                rendered["options"] = _dynamic_options(field)
            if field.get("asset"):
                rendered["asset"] = field_asset(field, value)
            fields.append(rendered)
        out.append({
            "id": section["id"],
            "title": section["title"],
            "subtitle": section.get("subtitle", ""),
            "save_label": section.get("save_label", "Save"),
            "fields": fields,
        })

    if extras:
        fields = []
        for key in extras:
            fields.append({
                "key": key,
                "label": key.split(".", 1)[1].replace("_", " ").capitalize(),
                "hint": "",
                "kind": _default_kind(key),
                "save": "section",
                "value": _field_value({"key": key}),
            })
        out.append({
            "id": "other",
            "title": "Other settings",
            "subtitle": "Advanced values without a dedicated field.",
            "save_label": "Save other settings",
            "fields": fields,
        })

    return out


# ── plugin settings ───────────────────────────────────────────────
#
# Plugins already declare their own settings in manifest.json, so the schema
# only has to translate the manifest vocabulary into the same field shape.

_KIND_FROM_MANIFEST = {
    "check": "toggle",
    "switch": "toggle",
    "bool": "toggle",
    "select": "choice",
    "choice": "choice",
    "enum": "choice",
    "text": "text",
    "password": "password",
    "number": "number",
    "textarea": "textarea",
}


def _as_bool(value: Any) -> bool:
    """Manifests and config files both spell booleans several ways."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in ("1", "true", "yes", "on", "si")


def manifest_field(entry: dict[str, Any], values: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    """Convert one manifest ``ui.settings`` entry into a schema field.

    ``values`` are what the plugin has actually stored; a key that was never
    written falls back to the manifest default.
    """
    stored = values or {}
    key = str(entry.get("key") or entry.get("id") or "")
    if not key:
        return None
    kind = _KIND_FROM_MANIFEST.get(str(entry.get("t", "text")).lower(), "text")
    value = stored[key] if key in stored else entry.get("default")
    if kind == "toggle":
        value = _as_bool(value)
    field: dict[str, Any] = {
        "key": key,
        "label": str(entry.get("label") or entry.get("id") or ""),
        "hint": str(entry.get("hint") or ""),
        "kind": kind,
        "save": "now" if kind in ("toggle", "choice") else "section",
        "value": value,
    }
    options = entry.get("options") or []
    if options:
        field["options"] = [
            {"value": o.get("value", ""), "label": str(o.get("label", o.get("value", "")))}
            for o in options
        ]
    if entry.get("placeholder"):
        field["placeholder"] = str(entry["placeholder"])
    return field


def plugin_sections(plugins: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build one settings section per installed plugin that declares settings.

    ``plugins`` is a list of ``{"id", "name", "description", "manifest"}``.
    """
    sections = []
    for plugin in plugins:
        manifest = plugin.get("manifest") or {}
        entries = ((manifest.get("ui") or {}).get("settings")) or []
        if not entries:
            continue
        try:
            from .plugin_settings import read_values
            values = read_values(manifest)
        except Exception:  # noqa: BLE001 - an unreadable file still gets listed
            values = {}
        fields = [f for f in (manifest_field(e, values) for e in entries) if f]
        if not fields:
            continue
        sections.append({
            "id": f"plugin:{plugin.get('id', '')}",
            "title": plugin.get("name") or plugin.get("id") or "Plugin",
            "subtitle": plugin.get("description") or "",
            # The card title already says which plugin this is, and a plugin
            # name is too long to sit on a button.
            "save_label": "Save settings",
            "plugin_id": plugin.get("id", ""),
            "fields": fields,
        })
    return sections
