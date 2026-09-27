# Publishing to the store

The Pulsar OS store ([store-os.inled.es](https://store-os.inled.es)) distributes skills and plugins for Sayri. Publishing is free; submissions are reviewed by AI agents and scanned with VirusTotal before they appear.

## What can be published

| Store type | What it is |
| ---------- | ---------- |
| `sayri_skill` | A directory with a `SKILL.md`. |
| `sayri_plugin` | A directory with `manifest.json` + entrypoint. |

## Requirements

- The package is a zip whose root contains the skill or plugin directory.
- `SKILL.md` needs valid front matter (`name`, `description`); `manifest.json` needs `id`, `name`, `type`, `version`, `entrypoint`, `description`.
- The scanner audits content at install time and the reviewers audit it at publish time. Reverse shells, `curl | bash`, obfuscated code, raw disk writes, credential access: rejected, and mostly blocked outright by the score.
- Declare what you use: `required_secrets` for credentials, `sandbox_level` for what the plugin expects, `authorization.mode` for gateways. Undeclared capability is the fastest way to a rejection.
- Do not ship credentials, tokens or endpoints with keys in the package.
- Pin versions. Bump `version` on every release; the store indexes by id and version.

## Install-time behaviour

When a user runs `sayri skills install <id>`:

1. The store catalog (`https://store-os.inled.es/schema/index.json`, fallback `pulsar-store.pages.dev`) is fetched and matched by id or name. Official packages always outrank ClawHub results in search.
2. The zip is downloaded and extracted only after every entry passes the zip-slip check.
3. The static scanner scores the content; 40+ warns, 80+ blocks.
4. Files land in `~/.config/sayri/skills/` (skills) or `~/.config/sayri/plugins/` (plugins).

Because install can fail at any of these steps, a package that extracts correctly and scores low installs silently; a package that trips a pattern shows the user the exact warning and lets them decide at WARN, or refuses at BLOCK.

## Index format

The catalog is a JSON document:

```json
{
  "packages": [
    {
      "id": "my-skill",
      "name": "My Skill",
      "type": "sayri_skill",
      "description": "One line.",
      "author": "You",
      "version": "1.0.0",
      "download_url": "https://.../my-skill.zip"
    }
  ]
}
```

`download_url` must serve the zip directly. Search matches against `id`, `name` and `description` in lowercase.

## Checklist

- [ ] `id` is unique, lowercase, no spaces.
- [ ] `description` is one honest line — it is what users see in search.
- [ ] Skills: front matter parses, description says when to use it.
- [ ] Plugins: manifest parses, entrypoint runs with no arguments, SIGTERM handled.
- [ ] Secrets declared in `required_secrets`, none hardcoded.
- [ ] Works from `~/.config/sayri/plugins/` (user dir), not just from `/usr/share`.
- [ ] Version bumped.
