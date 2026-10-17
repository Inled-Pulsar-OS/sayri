#!/usr/bin/env python3
"""Sayri Discord Memory Gateway.

Listens to messages in Discord, detects tasks/proposals/questions and writes
them to per-channel memory markdown files. Matched messages from *authorized*
users are also routed to the bound Sayri agent via the daemon, and the reply
comes back in-channel.

Security model (as declared in manifest.json):
- Desktop OTP pairing: the first `!pair <pin>` from the PIN shown in the
  desktop UI authorizes that Discord account as owner (`SAYRI_AUTH_FILE`).
- Only the paired owner is routed to the agent. DMs are owner-only.
- Memory capture follows the same gate: an unpaired stranger cannot make the
  assistant remember their messages.
- Failed PIN attempts are rate-limited (5 per 10 minutes) and the PIN rotates
  after a successful pairing.

Commands: `!pair <pin>`, `!status`.

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
from __future__ import annotations

import asyncio
import hmac
import json
import os
import random
import signal
import socket
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

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


# ── authorization (desktop OTP pairing) ────────────────────────────

AUTH_FILE = Path(os.environ.get(
    "SAYRI_AUTH_FILE",
    os.path.join(paths.config_dir(), "authorizations_discord_memory.json")))
SHARED_PIN_FILE = Path(os.environ.get(
    "SAYRI_PIN_FILE",
    os.path.join(paths.config_dir(), "pairing_pin_discord_memory.json")))


class AuthorizationManager:
    """Enforces desktop OTP pairing: only a paired owner may interact."""

    def __init__(self) -> None:
        self.auth_file = AUTH_FILE
        self.whitelisted_users: set[str] = set()
        self._failed_attempts: dict[str, tuple[int, float]] = {}
        self._load()

    def _load(self) -> None:
        if self.auth_file.is_file():
            try:
                data = json.loads(self.auth_file.read_text(encoding="utf-8"))
                self.whitelisted_users = {
                    str(u).lower() for u in data.get("allowed_discord_users", [])
                }
            except Exception:
                pass

    def _save(self) -> None:
        try:
            self.auth_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "updated_at": time.time(),
                "allowed_discord_users": list(self.whitelisted_users),
            }
            self.auth_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            try:
                os.chmod(self.auth_file, 0o600)
            except OSError:
                pass
        except Exception as exc:
            print(f"[Auth] Save error: {exc}", file=sys.stderr)

    def is_owner(self, user_id: str, username: Optional[str] = None) -> bool:
        self._load()
        u_id = str(user_id).lower()
        if u_id in self.whitelisted_users:
            return True
        if username:
            u_name = username.lower()
            return u_name in self.whitelisted_users or f"@{u_name}" in self.whitelisted_users
        return False

    def verify_pairing_pin(self, pin: str, user_id: str,
                           username: Optional[str] = None) -> Tuple[bool, str]:
        now = time.time()
        fail_count, last_fail = self._failed_attempts.get(str(user_id), (0, 0.0))
        if now - last_fail > 600:
            fail_count = 0
        if fail_count >= 5:
            cooldown_left = int(600 - (now - last_fail))
            return False, (f"Too many failed attempts. "
                           f"Please wait {cooldown_left} seconds before trying again.")

        clean_pin = pin.replace(" ", "").replace("-", "").strip()
        if not clean_pin:
            return False, "PIN code is empty."

        if SHARED_PIN_FILE.is_file():
            try:
                data = json.loads(SHARED_PIN_FILE.read_text(encoding="utf-8"))
                file_pin = str(data.get("pin", "")).replace(" ", "").replace("-", "").strip()
                expires_at = data.get("expires_at", float("inf"))
                if hmac.compare_digest(clean_pin, file_pin) and now <= expires_at:
                    self.whitelisted_users.add(str(user_id).lower())
                    if username:
                        self.whitelisted_users.add(username.lower())
                    self._save()
                    self._failed_attempts.pop(str(user_id), None)
                    # Rotate the PIN so it cannot be replayed.
                    try:
                        SHARED_PIN_FILE.write_text(json.dumps({
                            "pin": f"{random.randint(100000, 999999)}",
                            "created_at": time.time(),
                            "expires_at": time.time() + 86400,
                        }, indent=2), encoding="utf-8")
                        os.chmod(SHARED_PIN_FILE, 0o600)
                    except OSError:
                        pass
                    return True, ("Pairing completed successfully! Your Discord "
                                  "account is now the owner of this Sayri gateway.")
            except Exception as exc:
                print(f"[Auth] Error checking pin file: {exc}", file=sys.stderr)
        else:
            print(f"[Auth] PIN file not found at {SHARED_PIN_FILE}")

        self._failed_attempts[str(user_id)] = (fail_count + 1, now)
        remaining = 5 - (fail_count + 1)
        return False, f"Incorrect or expired PIN. Remaining attempts: {max(0, remaining)}"


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
    task_markers = ("tarea", "task", "to-do", "todo", "hacer", "make", "create",
                    "run", "execute", "instalar", "install")
    proposal_markers = ("propuesta", "proposal", "suggestion", "sugerencia",
                        "idea", "what if", "how about")
    question_markers = ("?", "how", "what", "why", "when", "where", "who",
                        "qué", "cómo", "por qué", "cuándo")

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
        self.auth = AuthorizationManager()
        # One pairing hint per user so an unpaired server does not turn into
        # an answer machine for every bystander.
        self._hinted: set[int] = set()

    async def on_ready(self):
        print(f"[DiscordGateway] Logged in as {self.user} "
              f"(instance: {self.instance_id})")
        pid_file = os.environ.get("SAYRI_PID_FILE")
        if pid_file:
            Path(pid_file).write_text(str(os.getpid()), encoding="utf-8")

    async def on_message(self, message):
        if message.author == self.user:
            return

        content = (message.content or "").strip()

        # `!pair <pin>` — authorize this Discord account from the desktop PIN.
        if content.lower().startswith("!pair"):
            pin = content[len("!pair"):].strip()
            ok, reply = self.auth.verify_pairing_pin(
                pin, str(message.author.id), str(message.author))
            await message.reply(reply)
            return

        if content.lower().startswith("!status"):
            owner = self.auth.is_owner(str(message.author.id), str(message.author))
            await message.reply("Paired as owner." if owner
                                else "Not paired yet. Use `!pair <pin>` with the "
                                     "PIN shown in the Sayri desktop UI.")
            return

        is_dm = isinstance(message.channel, discord.DMChannel)
        if not self.auth.is_owner(str(message.author.id), str(message.author)):
            # Silent for memory capture (no spam), one hint per user.
            if message.author.id not in self._hinted:
                self._hinted.add(message.author.id)
                if not is_dm:
                    try:
                        await message.reply(
                            "I only work for the paired desktop owner. "
                            "The owner can pair you with `!pair <pin>`.")
                    except discord.HTTPException:
                        pass
            return

        # Owner: classify and write to memory.
        classification = _classify_message(content)
        if classification:
            _write_memory(
                channel_id=str(message.channel.id),
                author=str(message.author),
                content=content,
                classification=classification,
            )

        # Route to Sayri agent via daemon and reply in-channel.
        await self._route_to_sayri(message)

    async def _route_to_sayri(self, message):
        """Send the message to the bound Sayri agent and reply in-channel."""
        try:
            sock_path = os.path.join(paths.state_dir(), "sayri-daemon.sock")
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
