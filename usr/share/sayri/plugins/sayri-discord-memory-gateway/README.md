# Sayri Discord Memory Gateway

Watches Discord conversations, detects **tasks**, **proposals** and
**questions** and appends them to per-channel memory files
(`~/.config/sayri/discord_memory/channel_<id>.md`). Messages from the paired
desktop owner are routed to the bound Sayri agent through the daemon and the
answer is posted back with a per-channel session (`discord_<channel_id>`).

## Security

- **Desktop OTP pairing**: the first `!pair <pin>` with the PIN shown in the
  Sayri desktop UI authorizes that Discord account as owner.
- Only the paired owner reaches the agent or the memory files; DMs too.
- Failed PIN attempts are rate-limited (5 per 10 minutes), the PIN rotates
  after a successful pairing and every secret is written mode 0600.

## Commands

| Command | Description |
|---|---|
| `!pair <pin>` | Pair this Discord account using the desktop PIN |

## Requirements

- `discord.py` (installed by the gateway supervisor)
- A Discord bot token with the **Message Content** intent enabled
- The Sayri daemon running on the host

## Configuration

Create an instance from Settings → Gateways or with the CLI:

```bash
sayri-gateway save '{"id":"my-discord","plugin_id":"sayri-discord-memory-gateway","agent_id":"default","sandbox_level":"LEVEL_1_READONLY","secret_key":"DISCORD_BOT_TOKEN"}'
sayri-gateway start my-discord
```

The bot token is stored in the Sayri vault under `DISCORD_BOT_TOKEN`.