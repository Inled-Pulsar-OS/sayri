# Sayri Discord Memory Gateway

Discord gateway that watches the conversations of a server, detects
**tasks**, **proposals** and **questions**, and appends them to per-channel
memory markdown files (`~/.config/sayri/discord_memory/channel_<id>.md`).
Messages from the paired desktop owner are also routed to the bound Sayri
agent through the daemon, and the answer is posted back in the channel with
a per-channel session (`discord_<channel_id>`), so each channel keeps its own
conversation thread.

## Security

- **Desktop OTP pairing**: the first `!pair <pin>` from the PIN shown in the
  Sayri desktop UI authorizes that Discord account as owner.
- Only the paired owner is routed to the agent and only the paired owner's
  messages are captured to memory. DMs are owner-only.
- Failed PIN attempts are rate-limited (5 per 10 minutes) and the PIN rotates
  after a successful pairing.

## Commands

| Command | Description |
|---|---|
| `!pair <pin>` | Pair this Discord account using the desktop PIN |
| `!status` | Show whether this account is paired |

## Requirements

- `discord.py` (installed automatically by the gateway supervisor)
- A Discord bot token with the **Message Content** intent enabled
- The Sayri daemon running on the host

## Configuration

Create an instance from the Sayri desktop UI (Settings → Gateways) or with
the CLI:

```bash
sayri-gateway save '{"id":"my-discord","plugin_id":"sayri-discord-memory-gateway","agent_id":"default","sandbox_level":"LEVEL_1_READONLY","secret_key":"DISCORD_BOT_TOKEN"}'
sayri-gateway start my-discord
```

The bot token is stored in the Sayri vault under `DISCORD_BOT_TOKEN`.
