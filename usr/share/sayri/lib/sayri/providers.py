"""AI provider registry — built-in options plus plugin-declared providers.

Sayri speaks OpenAI-compatible APIs. The built-in choices live in
:data:`sayri.wizard.PROVIDERS`; plugins can register more by declaring an
``ai_providers`` array in their ``manifest.json`` using the same shape
(``value`` / ``label`` / ``base_url`` / ``model`` / ``key``). Providers
declared only by a plugin show up as soon as that plugin is installed and
disappear when it is uninstalled.
"""

from __future__ import annotations

import json
import os
from typing import Any

from . import paths

MANIFEST = "manifest.json"


def _search_bases() -> list[str]:
    roots: list[str] = []
    for attr in ("plugins_dir", "shared_plugins_dir"):
        try:
            roots.append(getattr(paths, attr)())
        except Exception:  # noqa: BLE001
            pass
    return roots


def plugin_providers() -> list[dict[str, Any]]:
    """Providers advertised by installed plugins; user copies take priority."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for base in _search_bases():
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            mfile = os.path.join(base, name, MANIFEST)
            if not os.path.isfile(mfile):
                continue
            try:
                with open(mfile, encoding="utf-8") as fh:
                    manifest = json.load(fh)
            except Exception:  # noqa: BLE001
                continue
            for entry in manifest.get("ai_providers") or []:
                if not isinstance(entry, dict) or not entry.get("value"):
                    continue
                value = entry["value"]
                if value in seen:
                    continue
                seen.add(value)
                out.append(dict(entry))
    return out


def available_providers(builtin: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Built-in list plus plugin-declared providers with fresh values."""
    known = {p["value"] for p in builtin if p.get("value")}
    return builtin + [p for p in plugin_providers() if p.get("value") not in known]