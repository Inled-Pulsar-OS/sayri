#!/usr/bin/env python3
"""Sayri Discord Memory Gateway: paired-owner messages go to a Sayri agent over
the local daemon socket; detected items land in per-channel memory files."""
import os
os.umask(0o077)  # secrets private by creation

import asyncio
import signal
import sys
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.normpath(os.path.join(_HERE, "..", "..", "lib")), _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
try:
    import discord
    from discord.ext import commands
except ImportError:
    print("discord.py missing: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)
import auth
import bridge
import memory
from sayri import paths

INSTANCE_ID = os.environ.get("SAYRI_GATEWAY_INSTANCE_ID", "discord-memory")
AGENT = os.environ.get("SAYRI_TARGET_AGENT", "default")
SANDBOX = os.environ.get("SAYRI_SANDBOX_LEVEL", "LEVEL_1_READONLY")
CONFIG_DIR = paths.config_dir()
MEMORY_DIR = os.path.join(CONFIG_DIR, "discord_memory")
DAEMON = bridge.daemon_socket_path(paths.state_dir())
# Fallback paths only serve a direct local run.
AUTH_FILE = Path(os.environ.get(
    "SAYRI_AUTH_FILE", CONFIG_DIR + f"/authorizations_{INSTANCE_ID}.json"))
PIN_FILE = Path(os.environ.get(
    "SAYRI_PIN_FILE", CONFIG_DIR + f"/pairing_pin_{INSTANCE_ID}.json"))
FAIL_FILE = AUTH_FILE.with_name(f"auth_failures_{INSTANCE_ID}.json")


class MemoryGateway(commands.Bot):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.auth = auth.AuthorizationManager(AUTH_FILE, PIN_FILE, FAIL_FILE)
        self.hinted: set = set()
        os.makedirs(MEMORY_DIR, mode=0o700, exist_ok=True)

    async def on_ready(self):
        print(f"[Gateway] {self.user} ready ({INSTANCE_ID})")
        pid = os.environ.get("SAYRI_PID_FILE")
        if pid:
            Path(pid).write_text(str(os.getpid()), encoding="utf-8")

    async def on_message(self, message):
        if message.author == self.user:
            return
        content = (message.content or "").strip()
        if content.lower().startswith("!pair"):
            ok, reply = self.auth.pair(
                content[5:].strip(), str(message.author.id), str(message.author))
            print(f"[Gateway] pairing by {message.author}: {ok}")
            await message.reply(reply)
            return
        # Gate: an unpaired user never reaches the agent or the memory files.
        if not self.auth.is_owner(str(message.author.id), str(message.author)):
            if (message.author.id not in self.hinted
                    and not isinstance(message.channel, discord.DMChannel)):
                self.hinted.add(message.author.id)
                await message.reply("Only the paired owner is served here "
                                    "(`!pair <pin>`).")
            return
        kind = memory.classify(content)
        if kind:
            path = memory.append(MEMORY_DIR, message.channel.id,
                                 str(message.author), content, kind)
            print(f"[Gateway] captured {kind} -> {path}")
        reply = await asyncio.to_thread(bridge.ask, DAEMON, content,
                                        str(message.author), AGENT, SANDBOX,
                                        f"discord_{message.channel.id}")
        if reply:
            await message.reply(reply)


def main() -> int:
    token = os.environ.get("DISCORD_BOT_TOKEN")  # injected by the supervisor
    if not token:
        print("DISCORD_BOT_TOKEN is not set", file=sys.stderr)
        return 1
    intents = discord.Intents.default()
    intents.message_content = True  # read message bodies
    bot = MemoryGateway(command_prefix="!", intents=intents)

    def on_term(_s, _f):
        asyncio.ensure_future(bot.close())

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)
    bot.run(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())