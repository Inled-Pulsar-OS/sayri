# Plugins

A plugin is a directory with a `manifest.json` and an executable entrypoint. Skills teach; plugins run.

## Types

The supervisor classifies plugins by manifest:

- `tool` — callable utilities for agents.
- `gateway` — channel connectors (Discord, Telegram, ...). See [gateways](gateways.md).
- `ui` — front-ends. See [UIs](uis.md).
- `service` — owns a background process (declared in the `service` block).

Type resolution, in order: the `type` field, then `plugin_type`, then inference from the `authorization` block or the id, then `ui` for `sayri-ui-*` ids, then `service` if a `service` block exists, else `tool`.

## Manifest

```json
{
  "id": "my-plugin",
  "name": "My Plugin",
  "type": "tool",
  "version": "1.0.0",
  "description": "What it does, one line.",
  "author": "You",
  "entrypoint": "main.py",
  "required_secrets": ["MY_SERVICE_TOKEN"],
  "sandbox_level": "LEVEL_1_READONLY",
  "min_sandbox_level": "LEVEL_1_READONLY",
  "allow_in_level_0": false,
  "capabilities": [],
  "allowed_domains": []
}
```

Fields the supervisor reads:

| Field | Meaning |
| ----- | ------- |
| `id` | Unique id. `sayri-ui-*` prefix marks UI plugins. |
| `entrypoint` | Script the supervisor runs, relative to the plugin dir. |
| `required_secrets` | Vault keys injected as environment variables at start. |
| `sandbox_level` / `min_sandbox_level` | Level bound to instances of this plugin. |
| `allow_in_level_0` | Whether a level-0 agent may use it. |
| `authorization` | `{ "mode": "pairing_otp" | ... }` for gateways. |
| `ui` | UI declaration: `kind`, `xui_renderer`, `settings`, `sync_instructions`, `chat_url`. |
| `service` | Managed background process: `{ auto_start, enabled, enabled_key, start[], stop[], status[] }`. |

## Settings

Declarative settings live in `manifest.json` under `ui.settings`. Each entry:

```json
{
  "t": "select",
  "id": "character",
  "key": "character",
  "label": "Character / avatar",
  "default": "Clippy",
  "hint": "Choose the avatar",
  "options": [
    {"value": "Clippy", "label": "Clippy (Paperclip)"},
    {"value": "Merlin", "label": "Merlin (Wizard)"}
  ]
}
```

`t` is one of `select | check | entry | number`. Values persist in the plugin's own settings file (`plugin_settings.settings_file_path`) — the same one the TUI, the GTK settings window, the daemon and the headless CLI all read and write. Your entrypoint reads them with `plugin_settings.read_values(manifest)` and writes with `plugin_settings.write_setting(manifest, key, value)`.

## Services

A plugin can own a background process:

```json
"service": {
  "auto_start": true,
  "enabled": true,
  "enabled_key": "enabled",
  "start":  ["gateway.py", "start"],
  "stop":   ["gateway.py", "stop"],
  "status": ["gateway.py", "status"]
}
```

Commands run as `sys.executable` with the plugin directory as cwd. The enable flag is persisted in the plugin settings file, so every surface agrees on one value. `plugin_service.start_service` / `stop_service` / `service_running` drive it; `auto_start_services` starts everything marked `auto_start` and enabled on boot.

## CLI

```
sayri plugins list                 List installed plugins
sayri plugins show <id>            Id, description, auth, secrets, chat_url
sayri plugins start|stop <id>      Start/stop a managed service
sayri plugins status <id>          Service status
sayri plugins config <id> level 3 [level0 yes|no]   Sandbox of the plugin
sayri plugins settings <id>        Plugin settings
```

Editing a system plugin's manifest under `/usr/share` is not possible for a user; the CLI mirrors the plugin into `~/.config/sayri/plugins/` and edits the copy.

## Installing and uninstalling

`sayri skills install <id>` handles plugins too (type `sayri_plugin` in the store). Uninstall stops instances, deletes their configs and scrubs their secrets from the vault.

## Where they are searched

`GatewaySupervisor._get_search_dirs()`: `~/.config/sayri/plugins/`, the system dir `/usr/share/sayri/plugins/`, and `packages/plugins/` next to a dev checkout. First manifest with a given id wins.
