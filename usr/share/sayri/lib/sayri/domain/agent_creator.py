"""Voice-driven and Natural Language Creator for Sub-Agents and Skills."""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from sayri import paths
from sayri.domain.models import (
    AgentModelConfig,
    AgentProfile,
    SandboxConfig,
    SandboxLevel,
)


# ── the sandbox, to and from its file ──────────────────────────────
#
# The agent file is the only place an agent's permissions are written down, so
# it has to carry the whole sandbox rather than the two or three fields some
# caller happened to need. Listing fields here is how permission rules quietly
# disappear: somebody adds a rule to the JSON by hand, opens the agent in the
# panel to fix the name, and the save drops the rule with no error anywhere.

def _sandbox_to_dict(cfg: SandboxConfig) -> dict:
    return {
        "level": cfg.level.value,
        "timeout_seconds": cfg.timeout_seconds,
        "isolated_dir": cfg.isolated_dir,
        "allow_network": cfg.allow_network,
        "allowed_binaries": list(cfg.allowed_binaries),
        "blocked_binaries": list(cfg.blocked_binaries),
        "permission_rules": [dict(r) for r in cfg.permission_rules],
        "ask_before_run": bool(cfg.ask_before_run),
    }


def _sandbox_from_dict(raw: Any, level: SandboxLevel) -> SandboxConfig:
    """Read a stored sandbox, defaulting each field from the model.

    A file written by an older Sayri has no permission rules and no switch, and
    the absence means the defaults — not a reason to refuse the agent.
    """
    data = raw if isinstance(raw, dict) else {}

    def _list(key: str) -> List[str]:
        value = data.get(key)
        if not isinstance(value, list):
            return []
        return [str(v) for v in value if str(v).strip()]

    def _rules(key: str) -> List[dict]:
        """Keep the entries that are shaped like a rule, drop the rest.

        Only the shape is checked here. Whether the effect is one Sayri knows
        is the permission layer's business, and it drops what it cannot use at
        check time — a job this function should not duplicate. What matters
        here is not writing junk back out, because a junk entry that survives
        one save survives every save after it.
        """
        value = data.get(key)
        if not isinstance(value, list):
            return []
        out = []
        for entry in value:
            if not isinstance(entry, dict):
                continue
            if all(str(entry.get(k, "")).strip() for k in ("action", "resource", "effect")):
                out.append(dict(entry))
        return out

    default = SandboxConfig(level=level)
    return SandboxConfig(
        level=level,
        timeout_seconds=int(data.get("timeout_seconds", default.timeout_seconds) or 0)
                        or default.timeout_seconds,
        isolated_dir=data.get("isolated_dir") or None,
        allow_network=bool(data.get("allow_network", default.allow_network)),
        allowed_binaries=_list("allowed_binaries"),
        # An absent list keeps the model's built-in refusals. An empty list means
        # the user asked for none, which is a different statement and is kept.
        blocked_binaries=_list("blocked_binaries") if "blocked_binaries" in data
                           else list(default.blocked_binaries),
        permission_rules=_rules("permission_rules"),
        ask_before_run=bool(data.get("ask_before_run", default.ask_before_run)),
    )


class AgentCreator:
    """Automates creation of sub-agent profiles and skills from natural language."""

    @staticmethod
    def list_agents() -> List[AgentProfile]:
        agents_dir = paths.agents_dir()
        os.makedirs(agents_dir, exist_ok=True)
        results: List[AgentProfile] = []

        # Add built-in default agent
        default_agent = AgentProfile(
            id="default",
            name="Main Sayri",
            description="Main operating system assistant for Pulsar OS",
            system_prompt="You are Sayri, the intelligent assistant integrated into Pulsar OS.",
            sandbox=SandboxConfig(level=SandboxLevel.LEVEL_3_HOST_USER),
            investigation_loop=True,
            is_builtin=True,
        )

        loaded_profiles: Dict[str, AgentProfile] = {}

        for filename in sorted(os.listdir(agents_dir)):
            if not filename.endswith(".json"):
                continue
            fpath = os.path.join(agents_dir, filename)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                sandbox_lvl_str = data.get("sandbox", {}).get("level", "LEVEL_3_HOST_USER")
                sandbox_lvl = getattr(SandboxLevel, sandbox_lvl_str, SandboxLevel.LEVEL_3_HOST_USER)
                a_id = data.get("id", filename[:-5])
                is_def = (a_id == "default")

                profile = AgentProfile(
                    id=a_id,
                    name=data.get("name", "Main Sayri" if is_def else filename[:-5]),
                    description=data.get("description", ""),
                    system_prompt=data.get("system_prompt", ""),
                    model=AgentModelConfig(
                        provider=data.get("model", {}).get("provider", "default"),
                        model_name=data.get("model", {}).get("model_name", "default"),
                        temperature=float(data.get("model", {}).get("temperature", 0.7)),
                        max_tokens=data.get("model", {}).get("max_tokens"),
                    ),
                    sandbox=_sandbox_from_dict(data.get("sandbox", {}), sandbox_lvl),
                    allowed_skills=data.get("allowed_skills", []),
                    allowed_plugins=data.get("allowed_plugins", []),
                    allowed_tools=data.get("allowed_tools", []),
                    custom_instructions=data.get("custom_instructions", ""),
                    allowed_commands=data.get("allowed_commands", []),
                    blocked_commands=data.get("blocked_commands", []),
                    investigation_loop=bool(data.get("investigation_loop", False)),
                    reinforcement_learning=bool(data.get("reinforcement_learning", False)),
                    created_at=data.get("created_at", time.time()),
                    is_builtin=is_def,
                )
                loaded_profiles[a_id] = profile
            except Exception as exc:
                print(f"[AgentCreator] Error loading agent {filename}: {exc}")

        # Ensure default agent exists (customized if loaded, else fallback default)
        if "default" in loaded_profiles:
            results.append(loaded_profiles.pop("default"))
        else:
            results.append(default_agent)

        for p in loaded_profiles.values():
            results.append(p)

        return results

    @classmethod
    def get_agent(cls, agent_id: str) -> Optional[AgentProfile]:
        for a in cls.list_agents():
            if a.id == agent_id:
                return a
        return None

    @classmethod
    def save_agent(cls, profile: AgentProfile, caller_level: Optional[SandboxLevel] = None) -> str:
        caller_level_env = os.environ.get("SAYRI_SANDBOX_LEVEL")
        if caller_level is None and caller_level_env:
            try:
                caller_level = getattr(SandboxLevel, caller_level_env, None) or SandboxLevel(caller_level_env)
            except Exception:
                pass

        if caller_level is not None:
            if caller_level in (
                SandboxLevel.LEVEL_0_NO_EXEC,
                SandboxLevel.LEVEL_RESTRICTED_COMMANDS,
                SandboxLevel.LEVEL_1_READONLY,
                SandboxLevel.LEVEL_2_ISOLATED_DEV,
            ):
                raise PermissionError(
                    f"Security violation: Sandbox level '{caller_level.value}' cannot create or modify agent profiles. "
                    "Self-configuration is strictly restricted to LEVEL_3_HOST_USER."
                )
            levels_order = [
                SandboxLevel.LEVEL_0_NO_EXEC,
                SandboxLevel.LEVEL_RESTRICTED_COMMANDS,
                SandboxLevel.LEVEL_1_READONLY,
                SandboxLevel.LEVEL_2_ISOLATED_DEV,
                SandboxLevel.LEVEL_3_HOST_USER,
                SandboxLevel.LEVEL_4_HOST_ROOT,
            ]
            caller_idx = levels_order.index(caller_level) if caller_level in levels_order else 4
            target_idx = levels_order.index(profile.sandbox.level) if profile.sandbox.level in levels_order else 4
            if target_idx > caller_idx:
                raise PermissionError(
                    f"Security violation: Privilege escalation attempt blocked. "
                    f"Caller at level '{caller_level.value}' cannot create agent at level '{profile.sandbox.level.value}'."
                )

        clean_id = re.sub(r"[^\w\-]", "_", profile.id).strip("_")
        if not clean_id:
            clean_id = f"agent_{int(time.time())}"
        profile.id = clean_id

        # Sync allowed_commands and sandbox.allowed_binaries
        if profile.allowed_commands and not profile.sandbox.allowed_binaries:
            profile.sandbox.allowed_binaries = list(profile.allowed_commands)
        elif profile.sandbox.allowed_binaries and not profile.allowed_commands:
            profile.allowed_commands = list(profile.sandbox.allowed_binaries)

        agents_dir = os.path.abspath(paths.agents_dir())
        os.makedirs(agents_dir, exist_ok=True)
        fpath = os.path.abspath(os.path.join(agents_dir, f"{clean_id}.json"))
        if not fpath.startswith(agents_dir):
            raise ValueError("Security violation: Invalid agent storage path.")

        payload = {
            "id": profile.id,
            "name": profile.name,
            "description": profile.description,
            "system_prompt": profile.system_prompt,
            "custom_instructions": getattr(profile, "custom_instructions", ""),
            "model": {
                "provider": profile.model.provider,
                "model_name": profile.model.model_name,
                "temperature": profile.model.temperature,
                "max_tokens": profile.model.max_tokens,
            },
            "sandbox": _sandbox_to_dict(profile.sandbox),
            "allowed_skills": profile.allowed_skills,
            "allowed_plugins": getattr(profile, "allowed_plugins", []),
            "allowed_tools": profile.allowed_tools,
            "allowed_commands": getattr(profile, "allowed_commands", []),
            "blocked_commands": getattr(profile, "blocked_commands", []),
            "investigation_loop": bool(getattr(profile, "investigation_loop", False)),
            "reinforcement_learning": bool(getattr(profile, "reinforcement_learning", False)),
            "created_at": profile.created_at,
        }

        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        try:
            os.chmod(fpath, 0o600)
        except OSError:
            pass
        return fpath

    @classmethod
    def delete_agent(cls, agent_id: str) -> bool:
        caller_level_env = os.environ.get("SAYRI_SANDBOX_LEVEL")
        if caller_level_env and caller_level_env not in ("LEVEL_3_HOST_USER", "LEVEL_4_HOST_ROOT"):
            raise PermissionError("Security violation: Deleting agents is only permitted from LEVEL_3_HOST_USER.")

        clean_id = re.sub(r"[^\w\-]", "_", agent_id).strip("_")
        if not clean_id or clean_id == "default":
            return False
        agents_dir = os.path.abspath(paths.agents_dir())
        fpath = os.path.abspath(os.path.join(agents_dir, f"{clean_id}.json"))
        if not fpath.startswith(agents_dir):
            return False
        if os.path.isfile(fpath):
            try:
                os.remove(fpath)
                return True
            except Exception:
                pass
        return False

    @classmethod
    def create_agent_from_prompt(
        cls,
        prompt_text: str,
        max_allowed_level: SandboxLevel = SandboxLevel.LEVEL_3_HOST_USER,
        inherits_investigation_loop: bool = False,
        inherits_learning: bool = False,
    ) -> Tuple[bool, str, Optional[AgentProfile]]:
        """Parses natural language prompt into a structured AgentProfile with privilege containment."""
        caller_env = os.environ.get("SAYRI_SANDBOX_LEVEL")
        if caller_env:
            try:
                caller_lvl = SandboxLevel(caller_env)
                if caller_lvl in (
                    SandboxLevel.LEVEL_0_NO_EXEC,
                    SandboxLevel.LEVEL_RESTRICTED_COMMANDS,
                    SandboxLevel.LEVEL_1_READONLY,
                    SandboxLevel.LEVEL_2_ISOLATED_DEV,
                ):
                    raise PermissionError(
                        f"Security error: Caller in sandbox '{caller_lvl.value}' cannot create sub-agents. "
                        "Self-configuration is only permitted at LEVEL_3_HOST_USER."
                    )
            except ValueError:
                pass

        # Non-escalation enforcement: Restricted sandboxes cannot create subagents
        if max_allowed_level in (
            SandboxLevel.LEVEL_0_NO_EXEC,
            SandboxLevel.LEVEL_RESTRICTED_COMMANDS,
            SandboxLevel.LEVEL_1_READONLY,
            SandboxLevel.LEVEL_2_ISOLATED_DEV,
        ):
            raise PermissionError(
                f"Security error: The current environment is restricted to level '{max_allowed_level.value}'. "
                "It has no permissions to create or register sub-agents on the system. "
                "Self-configuration is only permitted at LEVEL_3_HOST_USER."
            )

        text = prompt_text.lower()

        # Heuristic determination of Sandbox Level and Allowed Commands
        sandbox_level = SandboxLevel.LEVEL_3_HOST_USER
        allowed_cmds: List[str] = []

        if "gh" in text or "github" in text:
            allowed_cmds.append("gh *")
        if "git" in text:
            allowed_cmds.append("git *")
        if "curl" in text:
            allowed_cmds.append("curl *")
        if "jq" in text:
            allowed_cmds.append("jq *")
        if "npm" in text:
            allowed_cmds.append("npm *")
        if "python" in text:
            allowed_cmds.append("python3 *")

        restricted_triggers = (
            "comandos especificos", "comandos específicos", "solo comandos",
            "restricted commands", "allowlist", "comandos permitidos",
            "comandos restringidos", "restricted", "restringido", "restricted command",
            "solo con comandos", "limitado a comandos", "solo ejecute", "solo puede ejecutar"
        )
        if any(w in text for w in restricted_triggers) or (
            allowed_cmds and any(w in text for w in ("solo", "only", "restringido", "limita", "resumen", "resum"))
        ):
            sandbox_level = SandboxLevel.LEVEL_RESTRICTED_COMMANDS
            if not allowed_cmds:
                allowed_cmds = ["gh *", "git *"]
        elif any(w in text for w in ("sin comandos", "no ejecute", "no comandos", "solo chat", "no commands", "chat only", "conversational only")):
            sandbox_level = SandboxLevel.LEVEL_0_NO_EXEC
        elif any(w in text for w in ("solo lectura", "aislado", "sandbox", "read only", "isolated")):
            sandbox_level = SandboxLevel.LEVEL_2_ISOLATED_DEV

        # Enforce privilege boundary: target level can never exceed max_allowed_level
        levels_order = [
            SandboxLevel.LEVEL_0_NO_EXEC,
            SandboxLevel.LEVEL_RESTRICTED_COMMANDS,
            SandboxLevel.LEVEL_1_READONLY,
            SandboxLevel.LEVEL_2_ISOLATED_DEV,
            SandboxLevel.LEVEL_3_HOST_USER,
            SandboxLevel.LEVEL_4_HOST_ROOT,
        ]
        caller_idx = levels_order.index(max_allowed_level) if max_allowed_level in levels_order else 4
        target_idx = levels_order.index(sandbox_level) if sandbox_level in levels_order else 4
        if target_idx > caller_idx:
            sandbox_level = max_allowed_level

        # Model heuristics
        model_name = "default"
        provider = "default"
        if "ollama" in text or "local" in text:
            provider = "ollama"
            model_name = "qwen2.5-coder:7b"
        elif "flash" in text or "rapido" in text or "barato" in text or "fast" in text or "cheap" in text:
            model_name = "gemini-2.5-flash"
        elif "sonnet" in text or "claude" in text:
            model_name = "claude-3-5-sonnet"

        # Generate ID & Name
        slug_match = re.search(r"(?:subagente|agente|crear un agente para|crea un subagente para|subagent|agent|create an agent for|create a subagent for)\s+([a-zA-Z0-9_\-\s]{3,30})", prompt_text, re.IGNORECASE)
        raw_name = slug_match.group(1).strip() if slug_match else "New Sub-Agent"
        agent_id = re.sub(r"[^\w\-]", "_", raw_name.lower())[:24].strip("_") or f"agent_{int(time.time())}"

        profile = AgentProfile(
            id=agent_id,
            name=raw_name.capitalize(),
            description=f"Sub-agent created by voice: {prompt_text[:80]}...",
            system_prompt=(
                f"You are {raw_name.capitalize()}, a specialized Sayri sub-agent in Pulsar OS.\n"
                f"Your specific objective is: {prompt_text}\n"
                "Always respond concisely and respect your assigned security levels."
            ),
            model=AgentModelConfig(
                provider=provider,
                model_name=model_name,
                temperature=0.6,
            ),
            sandbox=SandboxConfig(
                level=sandbox_level,
                timeout_seconds=12,
                allowed_binaries=allowed_cmds,
            ),
            allowed_tools=[] if sandbox_level == SandboxLevel.LEVEL_0_NO_EXEC else ["bash"],
            allowed_commands=allowed_cmds,
            investigation_loop=bool(inherits_investigation_loop),
            reinforcement_learning=bool(inherits_learning),
        )

        saved_path = cls.save_agent(profile, caller_level=max_allowed_level)
        msg = f"✓ Sub-agent '{profile.name}' created successfully (Level: {sandbox_level.value}) at `{saved_path}`."
        return True, msg, profile

    @classmethod
    def create_skill_from_prompt(cls, name: str, description: str, instructions: str) -> Tuple[bool, str]:
        """Creates a new SKILL.md template under ~/.config/sayri/skills/<name>/."""
        clean_name = re.sub(r"[^\w\-]", "_", name.lower()).strip("_")
        if not clean_name:
            return False, "Invalid skill name."

        skill_dir = os.path.join(paths.skills_dir(), clean_name)
        os.makedirs(skill_dir, exist_ok=True)
        skill_file = os.path.join(skill_dir, "SKILL.md")

        content = f"""---
name: {clean_name}
description: {description or 'Skill created by voice in Sayri'}
---

# Skill: {clean_name}

## Description
{description or 'Custom skill for Sayri / Pulsar OS.'}

## Execution Instructions
{instructions or 'When the user asks for this task, run the corresponding bash commands safely.'}
"""

        with open(skill_file, "w", encoding="utf-8") as f:
            f.write(content)

        return True, f"✓ Skill '{clean_name}' created at `{skill_file}`."
