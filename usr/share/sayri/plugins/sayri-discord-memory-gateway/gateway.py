#!/usr/bin/env python3
"""Sayri Discord Gateway Plugin.

Listens to every message in configured Discord channels, detects tasks,
proposals and user requests, and writes them to memory files. Routes
matched messages to the bound Sayri agent via the daemon.

Environment contract (set by GatewaySupervisor):
  SAYRI_GATEWAY_INSTANCE_ID
  SAYRI_TARGET_AGENT
  SAYRI_SANDBOX_LEVEL
  SAYRI_ALLOW_RESUME_PREVIOUS
  SAYRI_INACTIVITY_TIMEOUT
  SAYRI_PID_FILE
  SAYRI_AUTH_FILE
  SAYRI_PIN_FILE
  DISCORD_BOT_TOKEN (from vault)
"""
import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Optional

# Ensure sayri lib is available
script_dir = os.path.dirname(os.path.abspath(__file__))
sayri_lib = os.path.normpath(os.path.join(script_dir, "..", "..", "lib"))
if os.path.isdir(sayri_lib) and sayri_lib not in sys.path:
    sys.path.insert(0, sayri_lib)

try:
    import discord
    from discord.ext import commands
except ImportError:
    print("discord.py not installed. Install with: pip install discord.py", file=sys.stderr)
    sys.exit(1)

from sayri import paths


# ── memory file writer ──────────────────────────────────────────────

MEMORY_DIR = os.path.join(paths.config_dir(), "discord_memory")
os.makedirs(MEMORY_DIR, exist_ok=True)


def _write_memory(channel_id: str, author: str, content: str, classification: str) -> None:
    """Append a detected item to the channel's memory markdown file."""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    fname = os.path.join(MEMORY_DIR, f"channel_{channel_id}.md")
    with open(fname, "a", encoding="utf-8") as f:
        f.write(f"\n## [{ts}] {classification} by {author}\n")
        f.write(f"{content}\n")
    print(f"[DiscordGateway] Wrote {classification} to {fname}")


def _classify_message(text: str) -> Optional[str]:
    """Heuristic classification: task, proposal, question, or None."""
    t = text.lower().strip()
    task_markers = ("tarea", "task", "to-do", "todo", "hacer", "make", "create", "run", "execute", "instalar", "install")
    proposal_markers = ("propuesta", "proposal", "suggestion", "sugerencia", "idea", "what if", "how about")
    question_markers = ("?", "how", "what", "why", "when", "where", "who", "qué", "cómo", "por qué", "cuándo")

    if any(m in t for m in task_markers):
        return "task"
    if any(m in t for m in proposal_markers):
        return "proposal"
    if any(m in t for m in question_markers):
        return "question"
    return None


# ── gateway bot ─────────────────────────────────────────────────────

class SayriDiscordBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance_id = os.environ.get("SAYRI_GATEWAY_INSTANCE_ID", "default")
        self.target_agent = os.environ.get("SAYRI_TARGET_AGENT", "default")
        self.sandbox_level = os.environ.get("SAYRI_SANDBOX_LEVEL", "LEVEL_1_READONLY")

    async def on_ready(self):
        print(f"[DiscordGateway] Logged in as {self.user} (instance: {self.instance_id})")
        # Write PID file
        pid_file = os.environ.get("SAYRI_PID_FILE")
        if pid_file:
            Path(pid_file).write_text(str(os.getpid()), encoding="utf-8")

    async def on_message(self, message):
        if message.author == self.user:
            return

        # Classify and write to memory
        classification = _classify_message(message.content)
        if classification:
            _write_memory(
                channel_id=str(message.channel.id),
                author=str(message.author),
                content=message.content,
                classification=classification,
            )

        # Route to Sayri agent via daemon
        await self._route_to_sayri(message)

    async def _route_to_sayri(self, message):
        """Send the message to the bound Sayri agent and reply in-channel."""
        try:
            import socket
            sock_path = str(paths.state_dir() / "sayri-daemon.sock")
            if not os.path.exists(sock_path):
                await message.channel.send("Sayri daemon is not running.")
                return

            msg = {
                "type": "INCOMING_MSG",
                "text": message.content,
                "author": str(message.author),
                "target_agent": self.target_agent,
                "sandbox_level": self.sandbox_level,
                "instance_id": self.instance_id,
                "session_id": f"discord_{message.channel.id}",
            }
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(sock_path)
            sock.settimeout(120)
            sock.sendall((json.dumps(msg) + "\n").encode())
            reply = b""
            while not reply.endswith(b"\n"):
                chunk = sock.recv(8192)
                if not chunk:
                    break
                reply += chunk
            sock.close()
            result = json.loads(reply.decode()).get("text", "")
            if result:
                await message.reply(result)
        except Exception as exc:
            print(f"[DiscordGateway] Error routing to Sayri: {exc}", file=sys.stderr)


def main():
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        print("DISCORD_BOT_TOKEN not set", file=sys.stderr)
        sys.exit(1)

    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True

    bot = SayriDiscordBot(command_prefix="!", intents=intents)

    def _sigterm_handler(signum, frame):
        print("[DiscordGateway] Received signal, shutting down...")
        asyncio.create_task(bot.close())

    signal.signal(signal.SIGTERM, _sigterm_handler)

    try:
        bot.run(token)
    except Exception as exc:
        print(f"[DiscordGateway] Fatal error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()