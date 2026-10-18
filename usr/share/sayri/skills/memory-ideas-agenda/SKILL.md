---
name: memory-ideas-agenda
description: Capture ideas and tomorrow agenda reminders safely in markdown notes using sayri-memory
---

# Skill: memory-ideas-agenda

## Instructions

Use this skill whenever the user wants to:
1. Save, remember, or brainstorm an idea, thought, or note.
2. Schedule a reminder or task for tomorrow, or inquire about what to do tomorrow.
3. Review or list previously recorded ideas or tomorrow's agenda.

This skill is designed for safe execution even in restricted sandboxes and isolation modes (`LEVEL_RESTRICTED_COMMANDS`), operating exclusively via `sayri-memory` without accessing arbitrary host system files.

### Exact Commands:

1. **Record a new idea:**
   ```bash
   sayri-memory add-idea "Description of the idea"
   ```

2. **Retrieve recorded ideas:**
   ```bash
   sayri-memory get-ideas
   ```

3. **Add a task or reminder for tomorrow:**
   ```bash
   sayri-memory add-tomorrow "Task or reminder for tomorrow"
   ```

4. **Retrieve tomorrow's scheduled tasks and agenda:**
   ```bash
   sayri-memory get-tomorrow
   ```

5. **Write or append to a custom memory markdown note:**
   ```bash
   sayri-memory write <filename.md> "Markdown content"
   ```

6. **Read a custom memory markdown note:**
   ```bash
   sayri-memory read <filename.md>
   ```

7. **List memory notes:**
   ```bash
   sayri-memory list
   ```

### Operational Rules:
- When the user says "tengo una idea...", "recuerda esta idea...", "guarda esta idea...":
  Execute `sayri-memory add-idea "<idea>"` and confirm in a concise, natural response that the idea has been recorded.
- When the user asks "qué tengo que hacer mañana?", "qué tengo para mañana?", "dime mi agenda de mañana":
  Execute `sayri-memory get-tomorrow` and summarize the pending items clearly.
- When the user says "mañana tengo que...", "anota para mañana...", "recuérdame mañana...":
  Execute `sayri-memory add-tomorrow "<task>"` and confirm that it has been scheduled for tomorrow.
- Never attempt to read arbitrary host system files or user documents; all personal memory is safely confined to `sayri-memory`.
