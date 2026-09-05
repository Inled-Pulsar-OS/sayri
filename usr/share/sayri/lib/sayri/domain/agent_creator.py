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
                    sandbox=SandboxConfig(
                        level=sandbox_lvl,
                        timeout_seconds=int(data.get("sandbox", {}).get("timeout_seconds", 10)),
                        allow_network=bool(data.get("sandbox", {}).get("allow_network", True)),
                    ),
                    allowed_skills=data.get("allowed_skills", []),
                    allowed_plugins=data.get("allowed_plugins", []),
                    allowed_tools=data.get("allowed_tools", ["bash", "read_skill", "search_history"]),
                    custom_instructions=data.get("custom_instructions", ""),
                    investigation_loop=bool(data.get("investigation_loop", True)),
                    reinforcement_learning=bool(data.get("reinforcement_learning", True)),
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
    def save_agent(cls, profile: AgentProfile) -> str:
        clean_id = re.sub(r"[^\w\-]", "_", profile.id).strip("_")
        if not clean_id:
            clean_id = f"agent_{int(time.time())}"
        profile.id = clean_id

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
            "sandbox": {
                "level": profile.sandbox.level.value,
                "timeout_seconds": profile.sandbox.timeout_seconds,
                "allow_network": profile.sandbox.allow_network,
            },
            "allowed_skills": profile.allowed_skills,
            "allowed_plugins": getattr(profile, "allowed_plugins", []),
            "allowed_tools": profile.allowed_tools,
            "investigation_loop": getattr(profile, "investigation_loop", True),
            "reinforcement_learning": getattr(profile, "reinforcement_learning", True),
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
    ) -> Tuple[bool, str, Optional[AgentProfile]]:
        """Parses natural language prompt into a structured AgentProfile with privilege containment."""
        # Non-escalation enforcement: Restricted sandboxes cannot create subagents
        if max_allowed_level in (SandboxLevel.LEVEL_0_NO_EXEC, SandboxLevel.LEVEL_1_READONLY, SandboxLevel.LEVEL_2_ISOLATED_DEV):
            return (
                False,
                f"Security error: The current environment is restricted to level '{max_allowed_level.value}'. "
                "It has no permissions to create or register sub-agents on the system.",
                None,
            )

        text = prompt_text.lower()

        # Heuristic determination of Sandbox Level
        sandbox_level = SandboxLevel.LEVEL_3_HOST_USER
        if "sin comandos" in text or "no ejecute" in text or "no comandos" in text or "solo chat" in text or "discord" in text or "telegram" in text or "no commands" in text or "chat only" in text or "conversational only" in text:
            sandbox_level = SandboxLevel.LEVEL_0_NO_EXEC
        elif "solo lectura" in text or "aislado" in text or "sandbox" in text or "read only" in text or "isolated" in text:
            sandbox_level = SandboxLevel.LEVEL_2_ISOLATED_DEV

        # Enforce privilege boundary: target level can never exceed max_allowed_level
        levels_order = [
            SandboxLevel.LEVEL_0_NO_EXEC,
            SandboxLevel.LEVEL_1_READONLY,
            SandboxLevel.LEVEL_2_ISOLATED_DEV,
            SandboxLevel.LEVEL_3_HOST_USER,
            SandboxLevel.LEVEL_4_HOST_ROOT,
        ]
        caller_idx = levels_order.index(max_allowed_level) if max_allowed_level in levels_order else 3
        target_idx = levels_order.index(sandbox_level) if sandbox_level in levels_order else 3
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
            ),
            allowed_tools=[] if sandbox_level == SandboxLevel.LEVEL_0_NO_EXEC else ["bash"],
            investigation_loop=True,
            reinforcement_learning=True,
        )

        saved_path = cls.save_agent(profile)
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
