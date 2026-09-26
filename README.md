<img align="left" src="usr/share/icons/hicolor/scalable/apps/sayri-tray.png" width="64" height="64">

# Sayri
The AI agent designed with security in mind

---

Sayri is the AI agent for Pulsar OS. It consumes a thousand times fewer tokens than OpenClaw, has far fewer files and much less structure than OpenClaw, and its code is auditable and written with security in mind.

- Includes 5 levels of sandboxing
- Works through skills and plugins
- Some plugins are Gateways: the programs that connect your Sayri to the outside world
- Can download skills from the Pulsar OS store and from ClawHub
- Speaks and listens out of the box. STT and TTS, 100% local models.
- Supports a wake word
- Uses almost no resources

Sayri probably will not be as famous as OpenClaw or Hermes, but at least it is a thousand times safer, and that lets me leave an agent running on Discord without losing sleep over it reaching my PC.

---

## Table of contents

- [Running without installing](#running-without-installing)
- [Requirements](#requirements)
- [How it works](#how-it-works)
- [Configuration](#configuration)
- [Command line](#command-line)
- [Data and configuration directories](#data-and-configuration-directories)
- [Security](#security)
- [Links](#links)

## Running without installing

Sayri is a plain Python package, so it can run directly from a checkout without installing anything. The only prerequisites are the system libraries listed under [Requirements](#requirements).

From the repository root:

```bash
# Point Python at the package directory
export PYTHONPATH="$PWD/PKG/sayri/usr/share/sayri/lib"

# Launch Sayri (orb + cajita overlay)
python3 -m sayri
```

That is the same thing the `usr/bin/sayri` wrapper does: it sets `PYTHONPATH` to the `lib` directory and runs `python3 -m sayri`. Two details are handled automatically:

- The `libgtk4-layer-shell` library is preloaded via `LD_PRELOAD` (the `__main__.py` entry point re-executes itself with it). This is required on Wayland so the overlay windows get pinned and WebKit loads reliably.
- The web UI (`index.html` of the orb build) and the bundled sounds are auto-detected from the package layout, so no `SAYRI_DATA_DIR` is needed when running from the checkout.

The config and state directories default to `~/.config/sayri` and `~/.local/share/sayri`. To keep everything inside the repository during development:

```bash
export SAYRI_CONFIG_DIR="$PWD/.sayri/config"
export SAYRI_STATE_DIR="$PWD/.sayri/state"
```

The other CLI tools can be invoked the same way, without installing:

```bash
# Toggle / show / hide the overlay of a running instance (via IPC)
python3 -m sayri --toggle
python3 -m sayri --show

# Manage skills and plugins
python3 -m sayri.skills list
python3 -m sayri.skills search <query>
python3 -m sayri.skills install <id>

# Take a screenshot (needs grim)
python3 -m sayri.screenshot <path>
```

When installed as a package (built from the `DEBIAN` tree or a `.deb`), launch Sayri with the `sayri` command. The tray indicator, the GTK settings window and the `sayri` / `sayri-skills` / `sayri-plugins` commands are installed to `/usr/bin`.

## Requirements

System packages (same list as the Debian control file):

- python3, python3-gi, python3-httpx
- gir1.2-gtk-4.0, gir1.2-webkit-6.0
- libgtk4-layer-shell0
- pipewire

Optional, used only for specific features:

- `whisper-cli` (whisper.cpp) + a whisper model for speech-to-text. If it is not on `PATH`, the settings window can download a static build and the models on first use.
- Piper + a voice for text-to-speech. Also downloadable from the settings window.
- `bwrap` (bubblewrap), required for the two isolated sandbox levels (1 and 2). If missing, commands fall back to the host.
- `grim` for screenshots, `ydotool` for input automation.

There is no LLM server bundled. By default Sayri expects an OpenAI-compatible endpoint; the shipped default is Ollama (`http://127.0.0.1:11434/v1`, model `llama3.2`). Any compatible API works: OpenAI, Groq, OpenRouter, LM Studio, OpenClaw, etc. On first run Sayri shows a setup screen where you pick a provider and enter an API key.

## How it works

### Interface

Sayri is a GTK4 application that pins a transparent, always-on-top overlay to the top of the screen (layer-shell). The overlay contains two elements side by side:

- The **orb**: a circular indicator that reacts to audio level and animates while listening, thinking or speaking. Clicking it toggles the microphone.
- The **cajita**: a rounded text card next to the orb that shows live transcriptions, the assistant's reply, the tool output, and a text input. It also hosts the settings tab.

The state machine is `idle -> listening -> thinking -> speaking -> listening`.

### Voice pipeline

- **STT** (whisper.cpp): the microphone is captured as raw PCM; an energy-based VAD with an adaptive noise floor splits the stream into utterances (speech followed by configurable silence). Each utterance is transcribed locally with `whisper-cli`, ghost transcriptions (hallucinated subtitles etc.) are filtered, and live partial transcripts can be shown while you talk.
- **Wake word**: in `wakeword` mode Sayri listens in the background and only activates on the configured wake word (default "hey sayri", with phonetic variants). You can also say the wake word followed by the question in one sentence.
- **TTS** (Piper): replies are converted to plain spoken text (markdown, URLs and emoji are stripped) and read aloud with a local Piper voice. The microphone is stopped while speaking to avoid feedback loops.

### LLM and agent mode

Queries are sent to the configured OpenAI-compatible endpoint as streaming chat completions. Sayri runs in **agent mode** by default: the model is instructed to issue commands in markdown `bash` code blocks, which Sayri executes one at a time and feeds the output back so the model can iterate (up to 6 steps per request).

- Commands that take longer than 4 seconds are treated as successfully launched in the background (relevant for GUI apps and `xdg-open`).
- Anything that needs root is run through `pkexec`, which triggers a graphical Polkit confirmation dialog that the user must approve.
- The agent's system prompt is assembled at runtime with the detected distro, the current user, the user profile (`USER.md`), the long-term memory file (`memory.md`) and the installed skills directory, so the model knows where its context lives.

### Sandboxing

Every command goes through the `SandboxExecutor`, which enforces a per-agent sandbox level. Levels 1 and 2 run commands in a bubblewrap container; level 3 runs on the host as the current user; level 4 elevates through Polkit.

| Level | Name | Behaviour |
| ----- | ---- | --------- |
| 0 | `LEVEL_0_NO_EXEC` | Pure conversation. No commands are ever executed. |
| 1 | `LEVEL_1_READONLY` | Read-only filesystem, ephemeral `/tmp`, isolated container. |
| 2 | `LEVEL_2_ISOLATED_DEV` | Read-only system plus a persistent isolated workspace directory per agent. |
| 3 | `LEVEL_3_HOST_USER` | Normal execution as the current user on the host. Default. |
| 4 | `LEVEL_4_HOST_ROOT` | Elevated execution via `pkexec` / Polkit, always with graphical confirmation. |

Enforcement details: privilege escalation (`sudo`, `pkexec`, `su`) is blocked below level 4; a configurable blocklist rejects dangerous binaries (`mkfs`, `dd`, `shutdown`, `reboot` by default); internal management tools (`sayri-skills`, `sayri-settings`, `pkill`, ...) are blocked inside isolated sandboxes; GUI access from inside a container is denied because it cannot reach the Wayland/X11 session.

### Skills, plugins and gateways

- **Skills** are directories with a `SKILL.md` file that teaches Sayri a capability. They live in `~/.config/sayri/skills/` and can be installed from the Pulsar OS store (`store-os.inled.es`) or from ClawHub. Installed skills are named in the agent's system prompt so they can be listed and read at runtime.
- **Plugins** are the same concept but with an executable entrypoint (`manifest.json` + a script). Some plugins are **Gateways**: they connect Sayri to external channels such as Discord or Telegram. Gateway instances are managed by the Gateway Supervisor, which runs one process per instance, binds each instance to an agent profile and a sandbox level, and injects credentials from the secrets vault (never plaintext in the config file). Enabled instances auto-start when Sayri launches and log to `~/.local/share/sayri/logs/`.

### Sessions and memory

Conversations are persisted as sessions in a SQLite database (`~/.local/share/sayri/sessions.db`). Long-term memory lives in `~/.config/sayri/memory.md` and the user profile in `~/.config/sayri/USER.md`; both are plain markdown files the model can read and append to.

## Configuration

Settings are stored in a GLib key file at `~/.config/sayri/sayri.conf` (or `$SAYRI_CONFIG_DIR/sayri.conf`):

- `[provider]` – `base_url`, `api_key`, `model`, `system_prompt`, `agent_mode`, `temperature`, `max_tokens`, `stream`, `timeout`
- `[stt]` – `mode` (`always` | `wakeword` | `manual`), `wake_word`, `model_size`, `language`, `mic_device`, `silence_ms`, `live_transcript`
- `[tts]` – `enabled`, `language`, `voice`, `quality`, `speed`
- `[ui]` – `orb_size`, `orb_position`, `autostart`, `always_on_top`, `bubble_visible`

You can edit the file by hand or use the settings window (gear icon in the cajita, or `sayri --settings`).

## Command line

```
sayri                      Launch the agent (orb + cajita overlay)
sayri --toggle | -t        Toggle the overlay of the running instance
sayri --show               Show the overlay
sayri --hide               Hide the overlay
sayri --settings | -s      Open the settings window
sayri --quit | -q          Quit the running instance
sayri --autostart          Start silently (used by the autostart entry)
sayri skills <args>        Manage skills
sayri screenshot <path>    Take a screenshot (needs grim)
```

Sayri is single-instance: the first launch starts the app and opens a UNIX socket (`$SAYRI_STATE_DIR/sayri.sock`); later invocations forward their command to the running instance over that socket instead of starting a second one.

The `sayri-skills` and `sayri-plugins` commands share the same interface:

```
list                     List installed skills and plugins
search <query>           Search the Pulsar OS store and ClawHub
install <name|id>        Install a skill or plugin
uninstall <name|id>      Remove an installed skill or plugin
read <name|id>           Show the SKILL.md of an installed skill
```

## Data and configuration directories

| Path | Contents |
| ---- | -------- |
| `~/.config/sayri/sayri.conf` | Main configuration |
| `~/.config/sayri/skills/` | Installed skills and plugin gateways |
| `~/.config/sayri/agents/` | Agent profiles |
| `~/.config/sayri/memory.md` | Long-term memory (editable by the model) |
| `~/.config/sayri/USER.md` | User profile |
| `~/.local/share/sayri/models/` | Whisper STT models |
| `~/.local/share/sayri/voices/` | Piper TTS voices |
| `~/.local/share/sayri/bin/` | Downloaded binaries (whisper-cli, etc.) |
| `~/.local/share/sayri/sessions.db` | Conversation history (SQLite) |
| `~/.local/share/sayri/sandboxes/` | Isolated workspaces per agent |
| `~/.local/share/sayri/logs/` | Gateway instance logs |

All paths can be overridden with environment variables: `SAYRI_DATA_DIR`, `SAYRI_CONFIG_DIR` and `SAYRI_STATE_DIR`. `SAYRI_SKIP_PRELOAD=1` disables the `libgtk4-layer-shell` preload, and `SAYRI_FORCE_WAYLAND=1` forces the Wayland backend.

## Security

- Five sandbox levels, enforced at execution time for every command.
- Privilege escalation is impossible below level 4; even level 4 requires an explicit Polkit dialog.
- Skill/plugin downloads are extracted with zip-slip protection and audited by a scanner that assigns a risk score and can block the installation outright.
- The IPC socket is `chmod 600` and rejects connections from other users (peer UID validation).
- Credentials for gateway plugins go through a secrets vault instead of the config file.
- All speech and transcription models run locally; only the LLM API receives your queries.

## Links

- [store-os.inled.es](https://store-os.inled.es) – Store of plugins, skills and more. Publish yours; it gets reviewed by free AI agents plus VirusTotal.
- [Documentation](https://os.inled.es/help/)