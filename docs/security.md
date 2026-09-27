# Sandbox and security

Sayri is designed so an agent can run unattended without endangering the machine. The security model has five layers, and each one is enforced in code, not in the prompt.

## Five sandbox levels

Every command goes through `SandboxExecutor`, which enforces the agent's level at execution time.

| Level | Name | Behaviour |
| ----- | ---- | --------- |
| 0 | `LEVEL_0_NO_EXEC` | Pure conversation. No command ever executes, including history search. |
| 1 | `LEVEL_1_READONLY` | Read-only filesystem, ephemeral `/tmp`, bubblewrap container. |
| 2 | `LEVEL_2_ISOLATED_DEV` | Read-only system plus a persistent isolated workspace per agent. |
| 3 | `LEVEL_3_HOST_USER` | Normal execution as the current user on the host. Default. |
| 4 | `LEVEL_4_HOST_ROOT` | Elevated via `pkexec` / Polkit, always with a graphical confirmation. |

Enforcement details:

- Privilege escalation (`sudo`, `pkexec`, `su`) is blocked below level 4.
- A configurable blocklist rejects dangerous binaries (`mkfs`, `dd`, `shutdown`, `reboot` by default).
- Internal management tools (`sayri-skills`, `sayri-plugins`, `sayri-settings`, `pkill`, ...) are blocked inside isolated sandboxes, so a sandboxed agent cannot edit its own permissions.
- GUI access from inside a container is denied: it cannot reach the Wayland/X11 session.
- A sub-agent can never receive a higher level than the agent that created it.

## Permission rules

Rules follow the shape `{action, resource, effect}`:

- `action`: `shell | read | write | web | skill | subagent`, or `*` for all.
- `resource`: a whole value with `*` (any run) and `?` (one character). The match is anchored — it covers the entire value, not a fragment.
- `effect`: `allow | ask | deny`.

The last rule that matches decides, so write broad rules first and exceptions after them. Shell commands are normalized before matching (whitespace collapsed, spaces around `|;&` removed), so spacing cannot dodge a rule.

Rules come from three layers, checked in order:

1. The agent's own rules, from its profile.
2. Saved approvals ("Allow always" answers), which behave like appended allow rules.
3. Policies, installed by the distribution, which may only **tighten**.

The policy layer is the part that makes the model safe to impose: a policy `deny` overrides anything, a policy `ask` is carried through, and a policy `allow` never grants access — it only lifts an earlier broad policy deny and hands the decision back to the agent's own rules. No edit to your own configuration can widen the policy layer.

What is refused without any file at all, in code: filesystem creation, raw disk writes, partition tools, shutdown/reboot/halt/poweroff, and a download piped into a shell (`curl ... | sh` and friends, enumerated explicitly so a harmless `wget x | grep sh` is not caught by accident).

## The ask effect

An `ask` is a checkpoint, not a softer deny. With asking on (Settings, per agent) and the agent at level 3 or 4, the command stops, a card appears with Allow / Allow always / Deny, and nothing runs until you answer. Two deliberate restrictions:

- Below level 3 an ask refuses outright: the answer could not change what the sandbox permits, so asking would be theatre.
- A policy ask stays a no when asking is switched off. Your own ask rules bypass to allow when you turn asking off; system rules never do.

A turn that arrived over a gateway has nobody who can answer, so asks there refuse immediately and the model is told to say so instead of retrying.

## Policy files

Shipped file: `/usr/share/sayri/policies.json` (denies only, by design). Admin override: `/etc/sayri/policies.json` (same tightening-only layer). A broken or missing file decides nothing — the in-code baseline still applies.

`policies.example.json` next to it documents the ask rules the distribution deliberately does not ship.

## Secrets: zero plaintext

The vault (`domain/secrets_manager.py`) stores credentials obfuscated with a machine-derived key (`/etc/machine-id` + UID), file mode 600. Three guarantees:

- Secrets are injected into the child process environment only at tool execution time, never into prompts.
- Every tool output is scanned before it reaches the LLM or the chat history; a secret value is replaced with `$SECRET:KEY`.
- Gateway credentials are read from the vault by the supervisor at instance start, never from the config file.

## Skill and plugin auditing

Before anything is installed, the static scanner (`domain/skills_scanner.py`) checks the content against a pattern table with weights: reverse shells, fork bombs, `curl | bash`, base64-obfuscated execution, raw disk writes, SSH/GPG key access, API-key environment scraping, and more. The score decides:

- below 40: allow
- 40–79: warn
- 80+: block

Downloads are extracted with zip-slip protection: any entry that resolves outside the target directory aborts the whole installation. Slugs are sanitized to prevent path traversal.

## Everything else

- The IPC socket is `chmod 600` and validates the peer UID.
- Agent profile files are `chmod 600`.
- All speech and transcription models run locally; only the LLM API receives your queries.
- The config and state directories are created with mode 700.
