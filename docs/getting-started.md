# Introduction and setup

Sayri is the AI agent for Pulsar OS: voice in, voice out, an agent that can run commands, and a security model that makes it safe to leave running. It consumes a fraction of the tokens and files of similar agents, and its code is small enough to audit.

## What it does

- Listens and speaks with 100% local models (whisper.cpp for STT, Piper for TTS), with wake word support.
- Runs as an agent: the model issues `bash` commands, Sayri executes them under the sandbox and feeds the output back.
- Runs multiple agents in parallel, each with its own model, sandbox level and gateways.
- Connects to Discord, Telegram or any other channel through gateway plugins.
- Installs skills from the Pulsar OS store and from ClawHub.
- Uses almost no resources.

## Requirements

System packages (same list as the Debian control file):

- python3, python3-gi, python3-httpx
- gir1.2-gtk-4.0, gir1.2-webkit-6.0
- libgtk4-layer-shell0
- pipewire

Optional, per feature:

- `whisper-cli` (whisper.cpp) plus a model for speech-to-text. Downloadable from the settings window.
- Piper plus a voice for text-to-speech. Also downloadable.
- `bwrap` (bubblewrap), required for sandbox levels 1 and 2. Without it, isolated levels fall back to the host — do not do that on a machine you care about.
- `grim` for screenshots, `ydotool` for input automation.

There is no LLM server bundled. Sayri talks to any OpenAI-compatible endpoint. The shipped default is Ollama (`http://127.0.0.1:11434/v1`, model `llama3.2`). On first run, the setup screen asks for a provider and API key.

## Running without installing

Sayri is a plain Python package. From a checkout:

```bash
export PYTHONPATH="$PWD/PKG/sayri/usr/share/sayri/lib"
python3 -m sayri
```

The `__main__` entry point re-executes itself with `libgtk4-layer-shell` preloaded (`LD_PRELOAD`); on Wayland this is what pins the overlay and makes WebKit load reliably. The web UI build and bundled sounds are auto-detected from the package layout, so no `SAYRI_DATA_DIR` is needed.

To keep all state inside the repo during development:

```bash
export SAYRI_CONFIG_DIR="$PWD/.sayri/config"
export SAYRI_STATE_DIR="$PWD/.sayri/state"
```

When installed as a package, launch with `sayri`.

## Command line

```
sayri                      Launch the agent (orb + cajita overlay)
sayri --toggle | -t        Toggle the overlay of the running instance
sayri --show / --hide      Show or hide the overlay
sayri --settings | -s      Open the settings window
sayri --quit | -q          Quit the running instance
sayri --autostart          Start silently (used by the autostart entry)
sayri skills <args>        Manage skills
sayri plugins <args>       Manage plugins and gateways
sayri screenshot <path>    Take a screenshot (needs grim)
```

Sayri is single-instance: the first launch opens a UNIX socket (`$SAYRI_STATE_DIR/sayri.sock`); later invocations forward their command to the running instance over that socket.

## Configuration

Settings live in a GLib key file at `~/.config/sayri/sayri.conf` (or `$SAYRI_CONFIG_DIR/sayri.conf`). Groups:

- `[provider]` — `base_url`, `api_key`, `model`, `system_prompt`, `agent_mode`, `temperature`, `max_tokens`, `stream`, `timeout`
- `[stt]` — `mode` (`always` | `wakeword` | `manual`), `wake_word`, `model_size`, `language`, `mic_device`, `silence_ms`, `live_transcript`
- `[tts]` — `enabled`, `language`, `voice`, `quality`, `speed`
- `[ui]` — `orb_size`, `orb_position`, `autostart`, `always_on_top`, `bubble_visible`

Edit by hand or with the settings window (gear icon in the cajita, or `sayri --settings`).

## Where everything lives

| Path | Contents |
| ---- | -------- |
| `~/.config/sayri/sayri.conf` | Main configuration |
| `~/.config/sayri/skills/` | Installed skills |
| `~/.config/sayri/plugins/` | Installed plugins (user) |
| `~/.config/sayri/agents/` | Agent profiles, one JSON per agent |
| `~/.config/sayri/memory.md` | Long-term memory, editable by the model |
| `~/.config/sayri/USER.md` | User profile |
| `~/.local/share/sayri/models/` | Whisper STT models |
| `~/.local/share/sayri/voices/` | Piper TTS voices |
| `~/.local/share/sayri/sessions.db` | Conversation history (SQLite) |
| `~/.local/share/sayri/sandboxes/` | Isolated workspaces per agent |
| `~/.local/share/sayri/logs/` | Gateway instance logs |

All three roots can be overridden with `SAYRI_DATA_DIR`, `SAYRI_CONFIG_DIR` and `SAYRI_STATE_DIR`. `SAYRI_SKIP_PRELOAD=1` disables the layer-shell preload, `SAYRI_FORCE_WAYLAND=1` forces the Wayland backend.
