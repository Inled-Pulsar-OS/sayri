"""OpenAI-compatible chat completions client (streaming SSE).

Works with any provider exposing POST /chat/completions: OpenAI, Ollama
(/v1), LM Studio, OpenClaw, vLLM, etc.
"""

from __future__ import annotations

import json
from typing import Callable, Optional

DEFAULT_TIMEOUT = 120


def _httpx():
    """Lazy import so the rest of Sayri works even if httpx is missing."""
    try:
        import httpx

        return httpx
    except ImportError as exc:
        raise LLMError(
            "The python3-httpx module is missing. Install it with: "
            "sudo apt install python3-httpx"
        ) from exc


def normalize_base_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url
    return url.rstrip("/")


def _endpoint(base_url: str) -> str:
    base = normalize_base_url(base_url)
    if not base:
        raise LLMError("Base URL is empty. Please enter an endpoint (e.g. https://api.mistral.ai/v1).")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def _parse_error(status: int, text: str) -> str:
    try:
        data = json.loads(text)
        if "error" in data:
            err = data["error"]
            if isinstance(err, dict) and "message" in err:
                return f"HTTP {status}: {err['message']}"
            return f"HTTP {status}: {err}"
        if "message" in data:
            return f"HTTP {status}: {data['message']}"
    except Exception:
        pass
    if status == 401:
        return f"HTTP 401 Unauthorized: Invalid API Key."
    if status == 404:
        return f"HTTP 404: Endpoint or model not found."
    if status == 429:
        return f"HTTP 429: Rate limit or quota exceeded."
    return f"HTTP {status}: {text[:250]}"


def sanitize_messages(messages: list[dict]) -> list[dict]:
    """Sanitizes messages array to strictly conform to Jinja chat templates:
    1. Exactly one system message at the beginning (index 0).
    2. Any subsequent system messages are merged into the initial system prompt.
    """
    if not messages:
        return []

    system_parts = []
    other_msgs = []

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "system":
            if content and str(content).strip():
                system_parts.append(str(content).strip())
        else:
            other_msgs.append({"role": role, "content": content})

    sanitized = []
    if system_parts:
        sanitized.append({"role": "system", "content": "\n\n".join(system_parts)})

    sanitized.extend(other_msgs)
    return sanitized


def build_payload(
    model: str,
    messages: list[dict],
    *,
    stream: bool = True,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
) -> dict:
    payload: dict = {
        "model": (model or "default").strip(),
        "messages": sanitize_messages(messages),
        "stream": stream,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None and max_tokens > 0:
        payload["max_tokens"] = max_tokens
    return payload


class LLMError(Exception):
    pass


def stream_chat(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    *,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
    stream: bool = True,
    timeout: int = DEFAULT_TIMEOUT,
    on_delta: Callable[[str], None],
    on_done: Callable[[str], None],
    on_error: Callable[[Exception], None],
) -> None:
    """Blocking streaming request; run it in a worker thread.

    on_delta(text) is called for every token, on_done(full_text) once at the
    end, on_error(exc) on failure.
    """
    base_url = (base_url or "").strip()
    api_key = (api_key or "").strip()
    model = (model or "").strip()

    if not base_url:
        on_error(LLMError("Base URL is empty. Please enter an endpoint URL in Settings."))
        return

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = build_payload(
        model,
        messages,
        stream=stream,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    try:
        httpx = _httpx()
        ep = _endpoint(base_url)
        with httpx.Client(timeout=timeout) as client:
            if not stream:
                r = client.post(ep, json=payload, headers=headers)
                if r.status_code >= 400:
                    raise LLMError(_parse_error(r.status_code, r.text))
                data = r.json()
                content = (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )
                if content:
                    on_delta(content)
                on_done(content or "")
                return

            with client.stream(
                "POST", ep, json=payload, headers=headers
            ) as r:
                if r.status_code >= 400:
                    try:
                        r.read()
                        detail = r.text
                    except Exception:  # noqa: BLE001
                        detail = ""
                    raise LLMError(_parse_error(r.status_code, detail))
                full: list[str] = []
                for line in r.iter_lines():
                    if isinstance(line, bytes):
                        line = line.decode("utf-8", errors="replace")
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = (choices[0].get("delta") or {}).get("content")
                    if delta is not None:
                        if isinstance(delta, bytes):
                            delta = delta.decode("utf-8", errors="replace")
                        elif not isinstance(delta, str):
                            delta = str(delta)
                        if delta:
                            full.append(delta)
                            on_delta(delta)
                on_done("".join(full))
    except Exception as exc:  # noqa: BLE001 - surface any transport error
        on_error(exc)
