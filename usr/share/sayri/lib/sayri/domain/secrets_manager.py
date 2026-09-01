"""Sayri Zero-Plaintext Secrets Manager & Token Shield.

Prevents credentials, API keys, and webhook tokens from being sent in plaintext
to LLM providers or saved in chat history logs. Injects secrets strictly into
isolated sandbox environment variables at tool execution time.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class SecretsManager:
    """Manages secret credentials with zero-plaintext leakage to LLM prompts."""

    _instance: Optional[SecretsManager] = None

    def __init__(self, secrets_file: Optional[Path] = None):
        if secrets_file is None:
            self.secrets_file = Path.home() / ".config" / "sayri" / "vault.json"
        else:
            self.secrets_file = Path(secrets_file)

        self._salt = self._derive_machine_salt()
        self._cache: Dict[str, Dict[str, str]] = {}
        self._load()

    @classmethod
    def get_instance(cls) -> SecretsManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _derive_machine_salt(self) -> bytes:
        machine_id = "/etc/machine-id"
        seed = b"sayri-zero-plaintext-vault"
        if os.path.exists(machine_id):
            try:
                seed += Path(machine_id).read_bytes()
            except Exception:
                pass
        seed += str(os.getuid()).encode("utf-8")
        return hashlib.sha256(seed).digest()

    def _obfuscate(self, text: str) -> str:
        text_bytes = text.encode("utf-8")
        key = self._salt
        obf = bytes(b ^ key[i % len(key)] for i, b in enumerate(text_bytes))
        return base64.b64encode(obf).decode("ascii")

    def _deobfuscate(self, encoded: str) -> str:
        try:
            raw = base64.b64decode(encoded.encode("ascii"))
            key = self._salt
            deobf = bytes(b ^ key[i % len(key)] for i, b in enumerate(raw))
            return deobf.decode("utf-8")
        except Exception:
            return ""

    def _load(self) -> None:
        self.secrets_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.secrets_file.is_file():
            self._cache = {}
            return
        try:
            data = json.loads(self.secrets_file.read_text(encoding="utf-8"))
            self._cache = data.get("secrets", {})
        except Exception as e:
            print(f"[SecretsManager] Warning loading vault: {e}")
            self._cache = {}

    def _save(self) -> None:
        try:
            self.secrets_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "updated_at": time.time(),
                "secrets": self._cache,
            }
            self.secrets_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.chmod(self.secrets_file, 0o600)
        except Exception as e:
            print(f"[SecretsManager] Failed saving vault: {e}")

    def set_secret(self, key: str, value: str, description: str = "") -> None:
        """Stores a secret securely."""
        clean_key = key.strip().upper().replace(" ", "_")
        self._cache[clean_key] = {
            "value": self._obfuscate(value.strip()),
            "description": description,
            "updated_at": str(int(time.time())),
        }
        self._save()

    def get_secret(self, key: str) -> Optional[str]:
        """Retrieves raw secret value."""
        clean_key = key.strip().upper().replace(" ", "_")
        item = self._cache.get(clean_key)
        if item and "value" in item:
            return self._deobfuscate(item["value"])
        return None

    def delete_secret(self, key: str) -> bool:
        """Removes a secret."""
        clean_key = key.strip().upper().replace(" ", "_")
        if clean_key in self._cache:
            del self._cache[clean_key]
            self._save()
            return True
        return False

    def list_secrets(self) -> List[Dict[str, str]]:
        """Returns metadata list of all stored secrets with masked previews."""
        result = []
        for k, v in self._cache.items():
            raw = self._deobfuscate(v.get("value", ""))
            masked = f"{raw[:3]}...{raw[-3:]}" if len(raw) > 8 else "***"
            result.append({
                "key": k,
                "masked": masked,
                "description": v.get("description", ""),
                "updated_at": v.get("updated_at", ""),
            })
        return result

    def sanitize_text_for_llm(self, text: str) -> str:
        """Redacts all plaintext secret values from text before sending to LLM."""
        if not text:
            return text
        sanitized = text
        for k, v in self._cache.items():
            raw = self._deobfuscate(v.get("value", ""))
            if raw and len(raw) >= 4 and raw in sanitized:
                sanitized = sanitized.replace(raw, f"$SECRET:{k}")
        return sanitized

    def inject_environment(
        self,
        base_env: Optional[Dict[str, str]] = None,
        allowed_keys: Optional[List[str]] = None,
    ) -> Dict[str, str]:
        """Injects authorized secrets into child process environment dictionary."""
        env = dict(base_env) if base_env is not None else dict(os.environ)
        # If allowed_keys is explicitly an empty list, inject no vault secrets
        if allowed_keys is not None and not allowed_keys:
            return env

        allowed_set = set(k.upper() for k in allowed_keys) if allowed_keys is not None else None
        for k, v in self._cache.items():
            if allowed_set is not None and k not in allowed_set:
                continue
            raw = self._deobfuscate(v.get("value", ""))
            if raw:
                env[k] = raw
        return env


secrets_manager = SecretsManager.get_instance()
