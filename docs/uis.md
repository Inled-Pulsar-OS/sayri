# User interfaces

A UI is a plugin of type `ui` that renders Sayri on screen. The core knows nothing about windows: every UI attaches to the same event stream (`CoreUI` sink over IPC) and sends the same commands back. That is why the orb, the cajita, a Clippy widget and the CLI all behave identically.

## Shipped UIs

**Orb + cajita** (`sayri-ui-orb`, `entrypoint: gateway.py`) — the default. A GTK4 app that pins a transparent, always-on-top overlay to the top of the screen with layer-shell. Two elements side by side:

- the orb: a circular indicator that reacts to audio level and animates through `idle -> listening -> thinking -> speaking`;
- the cajita: a rounded card with live transcriptions, replies, tool output, text input and the settings tab.

Web content inside WebKit, with a `sayri://` bridge to the daemon. `webkit.py` holds the bridge; `orb.py` the orb rendering; `cajita.py` the panel.

**Classic companions** (`sayri-ui-clippy`, `entrypoint: main.py`) — Clippy, Bonzi, Merlin, Rover, Peedy, Genie, Links, Rocky and F1, with speech balloons, sound effects and voice/chat. GTK3 with WebKit; it re-executes itself without the gtk4-layer-shell preload because the GTK4 preload breaks GDK3 symbol resolution. It speaks the full IPC command set through an allowlist (`CALL_ALLOWLIST` in `main.py`): conversation, config, permissions, settings, agents, skills, plugins, UIs, gateways, routines, vault.

## The xui micro-framework

`sayri/xui.py` renders the same screen description — a plain JSON component tree — in an interactive terminal, inside a WebKit/GTK window, or in a browser page. Only the event transport changes.

A screen:

```python
{
  "id": "welcome",
  "title": "Welcome to Sayri",
  "subtitle": "One-minute setup",
  "step": "1/5",
  "body":  [ nodes ],
  "footer":[ buttons ],
  "busy": false,
  "done": False,
}
```

Node types (`t`): `text`, `sub`, `note` (info|ok|warn|error), `spacer`, `button` (primary|secondary|danger), `entry`, `select`, `check`, `progress`.

A host owns the flow and implements `render() -> screen | None` and `dispatch(event) -> screen | None` (`None` ends the flow). Events:

```
{"type": "submit", "value": {field: value}}   form
{"type": "action", "widget": "<button id>"}   button
{"type": "change", "widget": id, "value": v}  live widget
{"type": "poll"}                              refresh while busy
```

The welcome wizard and every plugin wizard are written once and rendered everywhere. If you build a UI, use xui for forms and flows instead of inventing your own widgets.

## The settings schema

`sayri/settings_schema.py` describes every setting as data: sections, labels, hints, and which widget each one deserves (`text | password | number | toggle | choice | textarea`), with save semantics (`now` applies immediately, `section` waits for the section's Save). Anything present in `config.DEFAULTS` but missing from the schema is appended to a catch-all section, so adding a config key can never make it unconfigurable. Plugin settings come from each manifest's `ui.settings` and are converted, not restated.

A UI renders settings with `settings_schema` over IPC (`settings_schema`, `settings_save` commands) instead of hardcoding them.

## Writing a UI plugin

Manifest:

```json
{
  "id": "sayri-ui-mine",
  "name": "My UI",
  "type": "ui",
  "version": "1.0.0",
  "description": "One line.",
  "author": "You",
  "entrypoint": "main.py",
  "ui": {
    "kind": "character",
    "xui_renderer": "speech-bubble",
    "sync_instructions": "Shown once to the user on first sync.",
    "settings": [ ...declarative settings... ]
  }
}
```

`kind` is free-form (`orb`, `character`, ...); `xui_renderer` names the renderer that will draw your xui screens; `sync_instructions` is displayed by other UIs when they offer to switch.

The entrypoint:

1. Connects to the daemon IPC (`sayri.ipc.SayriClient` on `sayri-daemon.sock`).
2. Renders state from the broadcast events (`state`, `partial`, `assistant_delta`, `tool_start`, ...).
3. Sends commands for user actions (`talk`, `interrupt`, `config_set`, ...).
4. Restricts what the UI can invoke to an explicit allowlist if it renders remote content.
5. Writes a PID file and exits cleanly on SIGTERM.

Only one UI renders at a time; UIs are switched from the panel or `ui_start` / `ui_stop` IPC commands, and the active one is a per-user setting.

## The orb animation

The orb is a shader-driven circle whose palette matches the rest of Pulsar OS: a deep space-blue base with magenta/violet energy, an outer chroma ring that rotates continuously, an inner react ring driven by the microphone level, and a bloom/glow layer. States:

- `idle`: slow rotation, low intensity, gentle breathing scale.
- `listening`: ring amplitude tracks audio level in real time.
- `thinking`: faster rotation and a pulsing inner glow.
- `speaking`: amplitude tracks the TTS output.

Any UI can reproduce the look: layered radial gradients, `conic-gradient` chroma ring rotating via `@keyframes`, opacity and scale tied to state, durations between 0.8s (thinking pulse) and 6s (idle rotation). The same palette is used by this website.
