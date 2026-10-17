"""Per-channel memory capture for the Sayri Discord memory gateway.

Detected items are appended to one markdown file per channel under
``memory_dir`` (0600 via the gateway's umask 077). ``channel_id`` is reduced
to digits before use in the filename, so a crafted id can never escape
``memory_dir``, and captured text is stripped of control characters and
truncated so a message cannot inject escapes or bloat the file.
"""
import re
import time
from typing import Optional

MARKERS = {
    "task": ("tarea", "task", "to-do", "todo", "hacer", "make", "create",
             "run", "execute", "instalar", "install", "fix", "arreglar"),
    "proposal": ("propuesta", "proposal", "suggestion", "sugerencia", "idea",
                 "what if", "how about", "que tal"),
    "question": ("?", "how", "what", "why", "when", "where", "who", "como",
                 "cómo", "que ", "qué", "por que", "por qué"),
}
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
MAX_CHARS = 4000


def classify(text: str) -> Optional[str]:
    lowered = (text or "").lower().strip()
    for kind in ("task", "proposal", "question"):
        if any(marker in lowered for marker in MARKERS[kind]):
            return kind
    return None


def safe_text(text: str, limit: int = MAX_CHARS) -> str:
    return CONTROL_CHARS.sub("", text or "").strip()[:limit]


def channel_file(memory_dir: str, channel_id) -> str:
    digits = re.sub(r"[^0-9]", "", str(channel_id))[:32] or "unknown"
    return f"{memory_dir.rstrip('/')}/channel_{digits}.md"


def append(memory_dir: str, channel_id, author: str, text: str, kind: str) -> str:
    path = channel_file(memory_dir, channel_id)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "a", encoding="utf-8", errors="replace") as handle:
        handle.write(f"\n## [{stamp}] {kind} by {safe_text(author, 80)}\n")
        handle.write(safe_text(text) + "\n")
    return path