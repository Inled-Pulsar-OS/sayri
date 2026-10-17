# Sayri Discord Memory Gateway

Watches Discord conversations, detects **tasks**, **proposals** and
**questions** and appends them to per-channel memory files
(`~/.config/sayri/discord_memory/channel_<id>.md`). Messages from the paired
desktop owner are routed to the bound Sayri agent through the daemon, and the
answer is posted back with a per-channel session (`discord_<channel_id>`).

## How to use

1. Create a bot at https://discord.com/developers/applications, enable
   **Message Content Intent** in the *Bot* tab and invite it to your server
   with the *Send Messages*, *Read Message History* and *View Channels*
   permissions.
2. Save the bot token in the Sayri vault as `DISCORD_BOT_TOKEN`
   (Settings → Gateways in the desktop UI).
3. Create and start an instance:

```bash
sayri-gateway save '{"id":"my-discord","plugin_id":"sayri-discord-memory-gateway","agent_id":"default","sandbox_level":"LEVEL_1_READONLY","secret_key":"DISCORD_BOT_TOKEN"}'
sayri-gateway start my-discord
```

4. Send `!pair <pin>` in any channel, using the PIN shown in the Sayri
   desktop UI or printed by `sayri-gateway pin my-discord`. That Discord
   account is now the owner, and the PIN rotates right after pairing.
5. Done. Detected tasks/proposals/questions land in that channel's memory
   file, and the owner can talk to the Sayri agent straight from the
   channel — every channel keeps its own conversation thread.

Other commands: `sayri-gateway list|stop|delete <id>`.

## Security

- **Desktop OTP pairing**: constant-time PIN compare, PIN rotation after a
  successful pairing and a 5 attempts / 10 minutes lockout persisted on disk.
- Secrets and lockout state are written mode 0600; a group/other-readable
  secret file is refused.
- Unpaired users never reach the agent or the memory files.

## Requirements

- `discord.py` (installed by the gateway supervisor)
- A Discord bot token with the **Message Content** intent enabled
- The Sayri daemon running on the host