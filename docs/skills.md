# Skills

A skill is a directory with a `SKILL.md` file that teaches Sayri a capability. Skills are instructions and conventions, not code: the model reads them at runtime and uses the tools it already has.

## Format

```
~/.config/sayri/skills/
  my-skill/
    SKILL.md
```

`SKILL.md` is markdown with a small front matter:

```markdown
---
name: my-skill
description: One line. This is what the model sees when deciding to use the skill.
---

# Skill: my-skill

## Instructions

When the user asks for X, do Y.
Concrete commands, file formats, edge cases.
```

The `description` line matters most: it is injected into the agent's system prompt (up to 15 skills) so the model knows the skill exists. The full text is read on demand with the `read_skill` tool. Keep the body short and concrete — it spends context when read.

## Installing

From the Pulsar OS store and ClawHub:

```bash
sayri skills list
sayri skills search <query>
sayri skills install <id>
sayri skills read <id>
sayri skills uninstall <id>
```

Resolution order for `install`: Pulsar Store catalog first, then the ClawHub API, then the OpenClaw skills repository on GitHub. Every download is audited by the static scanner and extracted with zip-slip protection before touching disk (see [security](security.md)).

Any OpenClaw-compatible skill works: same `SKILL.md` convention.

## Writing a good skill

- Name it after what it does; the directory name is the skill id.
- Put the trigger conditions in the description ("Use when the user asks to ...").
- Prefer exact commands over prose. The model executes what it reads.
- If the skill needs credentials, do not put them in the file: store them in the vault and reference the variable name.
- Nothing in a skill can widen the sandbox. If the command needs level 3, say so — the executor will refuse it from a level 1 agent.

## How they are loaded

`skills.list_skills()` scans the skills directory, reads each `description`, and `AgentEngine.build_system_prompt` injects the list into every agent whose `allowed_skills` includes them (empty list = all installed skills). The skill itself never executes; the model may emit commands based on it, and those commands pass the full sandbox and permission pipeline.
