"""Shared text/OS helpers used by both the GUI app and the headless core.

Kept free of any GTK / GI dependency so the CLI and daemon can import it
without pulling in a display stack.
"""

from __future__ import annotations

import os
import re


def markdown_to_plain_speech(text: str) -> str:
    """Clean markdown syntax into natural, clean spoken text for Piper TTS."""
    if not text:
        return ""
    cleaned = text
    # Remove thinking tags  thinking... response and <thought>...</thought> first
    cleaned = re.sub(r" thinking.*? response", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"<thought>.*?</thought>", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    # Remove code blocks completely so TTS doesn't dictate raw scripts
    cleaned = re.sub(r"```(?:[a-zA-Z0-9_\-]+)?\n?(.*?)\n?```", "", cleaned, flags=re.DOTALL)
    # Convert inline code `foo` -> foo
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    # Convert bold / italic
    cleaned = re.sub(r"\*\*([^\*]+)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"__([^_]+)__", r"\1", cleaned)
    cleaned = re.sub(r"(?<!\*)\*(?!\*)([^\*\n]+?)(?<!\*)\*(?!\*)", r"\1", cleaned)
    cleaned = re.sub(r"(?<!\w)_([^\_\n]+?)_(?!\w)", r"\1", cleaned)
    # Convert headers # Header -> Header
    cleaned = re.sub(r"^(?:#{1,6})\s+(.+)$", r"\1", cleaned, flags=re.MULTILINE)
    # Convert list bullets - / *
    cleaned = re.sub(r"^[\*\-]\s+", "", cleaned, flags=re.MULTILINE)
    # Links [text](url) -> text
    cleaned = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", cleaned)
    # Strip URLs
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    # Remove leftover markdown symbols
    cleaned = re.sub(r"[\*\_~`#><]", "", cleaned)
    # Strip emojis and pictographs so TTS speaks pure words without reciting emoji names
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"  # emoticons
        "\U0001F300-\U0001F5FF"  # symbols & pictographs
        "\U0001F680-\U0001F6FF"  # transport & map symbols
        "\U0001F1E0-\U0001F1FF"  # flags
        "\U0001F900-\U0001F9FF"  # supplemental symbols & pictographs
        "\U0001FA00-\U0001FAFF"  # chess, symbols extended
        "\U00002700-\U000027BF"  # dingbats
        "\U00002600-\U000026FF"  # misc symbols
        "\U00002B50"              # star
        "\U0000200D"              # zero-width joiner
        "\U0000FE0F"              # variation selector-16
        "]+",
        flags=re.UNICODE,
    )
    cleaned = emoji_pattern.sub("", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{2,}", "\n", cleaned).strip()
    return cleaned


def detect_os() -> str:
    """Best-effort OS/distro name, safe to call on any platform."""
    if os.path.exists("/etc/arch-release"):
        return "Arch Linux"
    elif os.path.exists("/etc/debian_version"):
        return "Debian"
    try:
        with open("/etc/os-release") as f:
            content = f.read().lower()
            if "arch" in content:
                return "Arch Linux"
            elif "debian" in content or "ubuntu" in content:
                return "Debian"
            elif "fedora" in content or "rhel" in content:
                return "Fedora"
    except Exception:
        pass
    return "Linux"