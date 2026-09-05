"""ReAct Agent Orchestrator with Structured Tool Calling, Sandboxing, and Token-Efficiency."""

from __future__ import annotations

import json
import re
import threading
from typing import Any, Callable, Dict, List, Optional

from sayri import llm, paths, skills
from sayri.adapters.sandbox.executor import SandboxExecutor
from sayri.adapters.storage.sqlite_sessions import SQLiteSessionRepository
from sayri.domain.agent_creator import AgentCreator
from sayri.domain.models import (
    AgentProfile,
    Message,
    SandboxLevel,
    Session,
    ToolCall,
    ToolCallStatus,
)


def strip_unwanted_patterns(text: str, patterns_cfg: str) -> str:
    """Removes configured thinking tags or custom phrases from the LLM text output."""
    if not text:
        return ""
    cleaned = text
    # Default stripping for <think> and <thought> tags
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"<thought>.*?</thought>", "", cleaned, flags=re.DOTALL | re.IGNORECASE)

    if patterns_cfg:
        raw_patterns = [p.strip() for p in re.split(r"[,\n]", patterns_cfg) if p.strip()]
        for pat in raw_patterns:
            if not pat:
                continue
            try:
                cleaned = re.sub(pat, "", cleaned, flags=re.DOTALL | re.IGNORECASE)
            except Exception:
                cleaned = cleaned.replace(pat, "")

    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


class AgentEngine:
    """Orchestrates ReAct agent loop, tool calling, sandboxed execution, and memory persistence."""

    def __init__(
        self,
        storage: Optional[SQLiteSessionRepository] = None,
        sandbox: Optional[SandboxExecutor] = None,
    ) -> None:
        self.storage = storage or SQLiteSessionRepository()
        self.sandbox = sandbox or SandboxExecutor()
        self._active_queries: Dict[int, bool] = {}
        self._query_counter = 0

    def build_system_prompt(self, profile: AgentProfile) -> str:
        """Constructs a token-efficient system prompt with self-awareness of sub-agents, skills, plugins and strict sandboxing."""
        installed = skills.list_skills()
        skills_summary = ""
        is_level_0 = profile.sandbox.level == SandboxLevel.LEVEL_0_NO_EXEC
        is_read_only = profile.sandbox.level in (SandboxLevel.LEVEL_0_NO_EXEC, SandboxLevel.LEVEL_1_READONLY)

        if installed and not is_level_0:
            # Filter skills by profile.allowed_skills if specified
            filtered_skills = []
            for s in installed:
                s_id = s.get("id") or s.get("name", "")
                if profile.allowed_skills and s_id not in profile.allowed_skills and s.get("name") not in profile.allowed_skills:
                    continue
                filtered_skills.append(s)

            if filtered_skills:
                items = [f"- {s['name']}: {s['description']}" for s in filtered_skills[:15]]
                skills_summary = "\nAUTHORIZED SKILLS (read details with the 'read_skill' tool):\n" + "\n".join(items)

        # Sandbox / Execution context (clear & grounded so small models don't hallucinate sandbox isolation)
        if is_level_0:
            sandbox_info = "Active Isolation Level: LEVEL_0_NO_EXEC (STRICTLY FORBIDDEN TO EXECUTE BASH/SYSTEM COMMANDS; you are in a pure conversational assistant mode)."
            execution_policy = "- LEVEL_0_NO_EXEC: Purely conversational mode. No terminal or system access."
        elif profile.sandbox.level in (SandboxLevel.LEVEL_1_READONLY, SandboxLevel.LEVEL_2_ISOLATED_DEV):
            sandbox_info = f"Active Isolation Level: {profile.sandbox.level.value} (Isolated Bubblewrap sandbox container without graphical Wayland/X11 display)."
            execution_policy = f"- {profile.sandbox.level.value}: Isolated Bubblewrap environment for read or isolated files. No display server access."
        elif profile.sandbox.level == SandboxLevel.LEVEL_4_HOST_ROOT:
            sandbox_info = "DIRECT HOST ACCESS (LEVEL_4_HOST_ROOT): Full host execution with administrative/Polkit elevation."
            execution_policy = "- LEVEL_4_HOST_ROOT: Administrative access with Polkit elevation (pkexec)."
        else:
            sandbox_info = "DIRECT HOST ACCESS (LEVEL_3_HOST_USER): You run directly on the host Linux desktop with active user privileges. You have full terminal access to launch desktop applications, inspect system packages and manage files."
            execution_policy = "- LEVEL_3_HOST_USER: Direct execution on user's host desktop. Full capability to launch graphical apps (e.g. `google-chrome &`, `flatpak run ...`, `gtk-launch ...`) and run commands."

        custom_instr = f"\nSPECIFIC AGENT INSTRUCTIONS:\n{profile.custom_instructions}\n" if getattr(profile, "custom_instructions", "") else ""

        loop_protocol = ""
        if getattr(profile, "investigation_loop", True) and not is_level_0:
            loop_protocol = (
                "AUTONOMOUS 2-PHASE LOOP PROTOCOL (SEARCH HOST & WEB FIRST -> THEN LAUNCH):\n"
                "CRITICAL RULE: Never guess binary names blindly. Follow this exact 2-step pattern for all desktop and system actions:\n\n"
                "Example 1 (Locating and launching desktop apps):\n"
                "User: open the music player\n"
                "Assistant:\n"
                "```bash\n"
                "sayri-pref query 'music player' || grep -iE 'Name=.*(Music|Audio|Player)' /usr/share/applications/*.desktop ~/.local/share/applications/*.desktop 2>/dev/null | head -n 6\n"
                "```\n"
                "[System Observation]: /usr/share/applications/io.bassi.Amberol.desktop:Name=Amberol\n"
                "Assistant:\n"
                "```bash\n"
                "amberol &\n"
                "```\n"
                "[System Observation]: Command completed with exit code 0\n"
                "Assistant: I have launched Amberol music player for you.\n\n"
                "Example 2 (Searching the web or finding utilities before acting):\n"
                "User: what is the command for system diagnostics?\n"
                "Assistant:\n"
                "```bash\n"
                "sayri-web 'linux system diagnostics command' || which btop htop top 2>/dev/null\n"
                "```\n"
                "[System Observation]: • Summary: btop and htop are standard Linux system monitors.\n"
                "Assistant:\n"
                "```bash\n"
                "btop\n"
                "```\n"
                "[System Observation]: Command completed with exit code 0\n"
                "Assistant: You can use btop for real-time diagnostics.\n\n"
                "Example 3 (Self-Healing / Retry on Error):\n"
                "If any command fails (non-zero exit code or error output), DO NOT STOP. Inspect the error output, search with `sayri-web` or `grep`, and emit the corrected command.\n\n"
                "ALWAYS start with Step 1 (search with sayri-pref, sayri-web, grep, or which) before launching.\n\n"
            )

        base = (
            f"You are Sayri, the intelligent assistant, agentic orchestrator and operating-system copilot in Pulsar OS.\n"
            f"Active profile: {profile.name} (ID: {profile.id}).\n"
            f"Security: {sandbox_info}\n"
            f"{custom_instr}\n"
            f"SANDBOX AND EXECUTION POLICY:\n{execution_policy}\n\n"
            "YOUR CAPABILITIES IN PULSAR OS:\n"
            "1. System Orchestration: You can read files, open applications and run tools within your security limits.\n"
            "2. Sub-Agent Creation and Management: You can create sub-agents configured with different models and sandbox levels.\n"
            "3. Skills and Plugins: You can search for and install extensions from the Pulsar Store (https://store-os.inled.es).\n"
            "4. Memory & Conversation History: You maintain context with recent messages. If you need details from earlier in the conversation, you can emit ```search_history <keyword>``` to query past messages.\n"
            + ("5. Learned Preferences: You can query your learned experience and past user preferences anytime in bash with ```sayri-pref query \"<keyword>\"```.\n" if getattr(profile, "reinforcement_learning", True) else "")
            + f"{skills_summary}\n\n"
            f"{loop_protocol}"
            "CRITICAL EXECUTION AND TRUTHFULNESS RULES:\n"
            "1. If you need to perform an action or investigation on the system, emit a block:\n"
            "```bash\n<command>\n```\n"
            "2. ABSOLUTE TRUTHFULNESS: NEVER claim an application has opened or an action has completed unless the system observation confirms exit code 0 with no errors.\n"
            "3. Always respond in the language the user spoke to you in, naturally, concisely and pleasantly (1 to 3 spoken sentences for voice)."
        )
        return base

    def process_query(
        self,
        session_id: str,
        user_text: str,
        profile: AgentProfile,
        cfg: Any,
        on_delta: Callable[[str], None],
        on_done: Callable[[str], None],
        on_tool_start: Callable[[str], None],
        on_tool_finish: Callable[[str, str, int], None],
        on_error: Callable[[Exception], None],
    ) -> int:
        """Initiates a ReAct agent query in a background thread."""
        self._query_counter += 1
        query_id = self._query_counter
        self._active_queries[query_id] = True

        session = self.storage.get_session(session_id) or self.storage.create_session(
            agent_id=profile.id, title=user_text[:30], session_id=session_id
        )

        user_msg = Message(role="user", content=user_text)
        self.storage.add_message(session.id, user_msg)

        # 1. Natural Language Subagent Intent Detection & Non-Escalation Enforcement
        clean_prompt = user_text.strip().lower()
        subagent_triggers = [
            "crea un subagente", "crear un subagente", "crear subagente", "crea subagente",
            "nuevo subagente", "configura un subagente", "configurar subagente", "quiero un subagente",
            "create a subagent", "create subagent", "new subagent", "make a subagent",
            "set up a subagent", "i want a subagent", "configure a subagent"
        ]
        if any(trig in clean_prompt for trig in subagent_triggers):
            if profile.sandbox.level in (SandboxLevel.LEVEL_0_NO_EXEC, SandboxLevel.LEVEL_1_READONLY, SandboxLevel.LEVEL_2_ISOLATED_DEV):
                err_msg = (
                    f"⚠️ Security Error: The current agent is running in a restricted environment ({profile.sandbox.level.value}). "
                    "To prevent privilege escalation, this sandbox level strictly forbids creating or configuring sub-agents on the system. "
                    "This action must be performed from the Sayri desktop application or through an agent with LEVEL_3_HOST_USER level."
                )
                reply_msg = Message(role="assistant", content=err_msg)
                self.storage.add_message(session.id, reply_msg)
                on_delta(err_msg)
                on_done(err_msg)
                return query_id

            ok, msg, created_profile = AgentCreator.create_agent_from_prompt(
                user_text, max_allowed_level=profile.sandbox.level
            )
            reply_msg = Message(role="assistant", content=msg)
            self.storage.add_message(session.id, reply_msg)
            on_delta(msg)
            on_done(msg)
            return query_id

        # 2. Natural Language Skill/Plugin Creation Intent Detection
        skill_triggers = [
            "crea una habilidad", "crear una habilidad", "crea habilidad", "crear habilidad",
            "instala la habilidad", "instalar habilidad", "instala el plugin", "instalar plugin",
            "create a skill", "create skill", "install the skill", "install skill",
            "install the plugin", "install plugin"
        ]
        if any(trig in clean_prompt for trig in skill_triggers) and profile.sandbox.level in (SandboxLevel.LEVEL_0_NO_EXEC, SandboxLevel.LEVEL_1_READONLY, SandboxLevel.LEVEL_2_ISOLATED_DEV):
            err_msg = (
                f"⚠️ Security Error: The current agent is running at level {profile.sandbox.level.value}. "
                "It has no permissions to install or register new skills/plugins on the system. "
                "To install extensions, use the Pulsar Store or the Sayri Settings interface."
            )
            reply_msg = Message(role="assistant", content=err_msg)
            self.storage.add_message(session.id, reply_msg)
            on_delta(err_msg)
            on_done(err_msg)
            return query_id

        # 3. Async AI Title Generator
        if len(session.messages) <= 2 or session.title.startswith("New Conversation") or session.title == user_text[:30]:
            self._generate_session_title_async(session.id, user_text, cfg)

        # Prepare messages payload with token-efficient context window
        system_prompt = self.build_system_prompt(profile)
        all_past_msgs = session.messages
        
        if len(all_past_msgs) > 4:
            # Compact older context summary to save tokens while keeping conversation state
            older_msgs = all_past_msgs[:-4]
            topics_summary = []
            for om in older_msgs[-8:]:
                snippet = om.content.strip().replace("\n", " ")[:90]
                if snippet:
                    role_tag = "User" if om.role == "user" else "Sayri"
                    topics_summary.append(f"{role_tag}: {snippet}")
            if topics_summary:
                system_prompt += (
                    "\n\n[Previous context summary in this session:\n"
                    + "\n".join(topics_summary)
                    + "\n(Use ```search_history <keyword>``` if you need older verbatim details)]"
                )

        messages = [{"role": "system", "content": system_prompt}]

        recent_msgs = all_past_msgs[-4:] if len(all_past_msgs) > 4 else all_past_msgs
        for m in recent_msgs:
            if m.role == "system":
                continue
            role = "assistant" if m.role == "assistant" else "user"
            messages.append({"role": role, "content": m.content})

        messages.append({"role": "user", "content": user_text})

        threading.Thread(
            target=self._react_loop,
            args=(
                query_id,
                session_id,
                profile,
                messages,
                cfg,
                1,
                on_delta,
                on_done,
                on_tool_start,
                on_tool_finish,
                on_error,
            ),
            daemon=True,
        ).start()

        return query_id

    def _generate_session_title_async(self, session_id: str, first_query: str, cfg: Any) -> None:
        """Asynchronously calls LLM to generate a clean 3-4 word title for the session."""
        def _worker():
            try:
                base_url = cfg.get_string("provider", "base_url")
                api_key = cfg.get_string("provider", "api_key")
                model_name = cfg.get_string("provider", "model")
                if not base_url or not model_name:
                    return
                title_messages = [
                    {
                        "role": "system",
                        "content": "You are a conversation title generator. Generate an ultra-short 3 to 4 word title in the same language as the user's query that summarizes it. Respond ONLY with the 3-4 words, no quotes, no period and no explanations."
                    },
                    {"role": "user", "content": first_query[:120]}
                ]
                def _on_done(title_text: str):
                    clean = title_text.strip().strip('"\'').strip('.')
                    if clean and len(clean) >= 3 and not clean.startswith("HTTP"):
                        self.storage.update_session_title(session_id, clean[:36])
                        print(f"[AgentEngine] ✨ Auto-assigned session title: \"{clean[:36]}\"")

                llm.stream_chat(
                    base_url,
                    api_key,
                    model_name,
                    title_messages,
                    temperature=0.3,
                    max_tokens=15,
                    stream=False,
                    timeout=10,
                    on_delta=lambda _: None,
                    on_done=_on_done,
                    on_error=lambda _: None,
                )
            except Exception as exc:
                print(f"[AgentEngine] Title gen notice: {exc}")

        threading.Thread(target=_worker, daemon=True).start()

    def cancel_query(self, query_id: int) -> None:
        self._active_queries[query_id] = False

    def _react_loop(
        self,
        query_id: int,
        session_id: str,
        profile: AgentProfile,
        messages: List[Dict[str, Any]],
        cfg: Any,
        depth: int,
        on_delta: Callable[[str], None],
        on_done: Callable[[str], None],
        on_tool_start: Callable[[str], None],
        on_tool_finish: Callable[[str, str, int], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        max_depth = 10 if getattr(profile, "investigation_loop", True) else 6
        if not self._active_queries.get(query_id, False) or depth > max_depth:
            return

        base_url = profile.model.base_url or cfg.get_string("provider", "base_url")
        api_key = profile.model.api_key or cfg.get_string("provider", "api_key")
        model_name = profile.model.model_name
        if not model_name or model_name == "default":
            model_name = cfg.get_string("provider", "model")

        temperature = profile.model.temperature
        max_tokens = profile.model.max_tokens or cfg.get_int("provider", "max_tokens") or None

        current_full: List[str] = []

        def _handle_delta(delta: str) -> None:
            if not self._active_queries.get(query_id, False):
                return
            current_full.append(delta)
            on_delta(delta)

        def _handle_error(exc: Exception) -> None:
            if not self._active_queries.get(query_id, False):
                return
            err_msg = f"⚠️ Sayri Error: {exc}"
            try:
                self.storage.add_message(session_id, Message(role="assistant", content=err_msg))
            except Exception:
                pass
            on_error(exc)

        def _handle_done(full_text: str) -> None:
            if not self._active_queries.get(query_id, False):
                return

            # 1. Check for internal SQLite conversation history search tool (works in LEVEL_0_NO_EXEC without touching host)
            m_hist = re.search(r"```(?:search_history|search_chat_history|recall_history)\s*\n(.*?)\n```", full_text, re.DOTALL)
            if not m_hist:
                m_hist = re.search(r"<(?:search_history|recall_history)>(.*?)</(?:search_history|recall_history)>", full_text, re.DOTALL)

            if m_hist and depth < max_depth:
                query_term = m_hist.group(1).strip()
                on_tool_start(f"search_history: {query_term}")
                past_matches = self.storage.search_session_messages(session_id, query=query_term, limit=6)
                if not past_matches:
                    past_matches = self.storage.search_all_messages(query=query_term, limit=8)
                if past_matches:
                    fmt_items = []
                    for item in past_matches:
                        session_tag = f" ({item['session_title']})" if "session_title" in item else ""
                        fmt_items.append(f"- [{item['role'].upper()}{session_tag}]: {item['content']}")
                    obs = f"[Chat History Search Results for '{query_term}']:\n" + "\n".join(fmt_items)
                else:
                    obs = f"[Chat History Search Results for '{query_term}']: No matching messages found across conversations."

                on_tool_finish(f"search_history: {query_term}", obs, 0)
                next_messages = list(messages)
                next_messages.append({"role": "assistant", "content": full_text})
                next_messages.append({"role": "user", "content": obs})
                self._react_loop(
                    query_id,
                    session_id,
                    profile,
                    next_messages,
                    cfg,
                    depth + 1,
                    on_delta,
                    on_done,
                    on_tool_start,
                    on_tool_finish,
                    on_error,
                )
                return

            # 2. Check for bash commands in reply
            m = re.search(r"```(?:bash|sh)?\s*\n(.*?)\n```", full_text, re.DOTALL)
            if not m:
                m = re.search(r"<(?:bash|sh|tool)>(.*?)</(?:bash|sh|tool)>", full_text, re.DOTALL)

            if m and depth < max_depth:
                cmd = m.group(1).strip()
                if cmd:
                    on_tool_start(cmd)

                    # Strict Sandbox Level 0 Enforcement: Block without executing
                    if profile.sandbox.level == SandboxLevel.LEVEL_0_NO_EXEC:
                        retcode = 126
                        output = (
                            "🔒 [SECURITY POLICY LEVEL_0_NO_EXEC]: Command execution blocked by sandbox. "
                            "This agent operates in pure conversational mode and cannot execute bash commands, run scripts, or touch host files."
                        )
                        duration = 1.0
                    else:
                        retcode, output, duration = self.sandbox.execute(
                            cmd, profile.sandbox, agent_id=profile.id
                        )

                    on_tool_finish(cmd, output, retcode)

                    # Redact any accidental secret tokens from tool output
                    from sayri.domain.secrets_manager import secrets_manager
                    sanitized_output = secrets_manager.sanitize_text_for_llm(output)

                    # Store tool execution
                    tc = ToolCall(
                        name="bash",
                        arguments={"command": cmd},
                        status=ToolCallStatus.SUCCESS if retcode == 0 else ToolCallStatus.FAILED,
                        output=sanitized_output,
                        exit_code=retcode,
                        duration_ms=duration,
                    )
                    assistant_msg = Message(
                        role="assistant", content=full_text, tool_calls=[tc]
                    )
                    self.storage.add_message(session_id, assistant_msg)

                    # Followup
                    next_messages = list(messages)
                    next_messages.append({"role": "assistant", "content": full_text})

                    if retcode != 0:
                        if getattr(profile, "investigation_loop", True):
                            observation = (
                                f"⚠️ [EXECUTION FAILED (Exit Code {retcode})]:\n{sanitized_output}\n\n"
                                "AUTONOMOUS SELF-HEALING LOOP ACTIVE: The previous command failed or did not finish as expected. "
                                "DO NOT STOP OR GIVE UP. Collect information about the error, investigate the host system or search for solutions "
                                "(e.g. check binary paths with `which`, `find /usr/share/applications`, `flatpak list`, `pacman -Qs`), "
                                "correct your approach, and emit the next bash block ```bash\n<command>\n``` to continue toward completing the goal."
                            )
                        else:
                            observation = (
                                f"⚠️ [EXECUTION ERROR (Exit Code {retcode})]:\n{sanitized_output}\n\n"
                                "CRITICAL RULE: The previous command did NOT run or failed. "
                                "Explain to the user clearly what error occurred and how to resolve it."
                            )
                    else:
                        is_investigation_cmd = any(cmd.strip().startswith(prefix) for prefix in ("sayri-pref", "sayri-web", "grep", "which", "find", "cat", "echo", "pwd", "ls", "search_history", "head", "tail", "pacman -Q", "flatpak list"))
                        if is_investigation_cmd and getattr(profile, "investigation_loop", True):
                            observation = (
                                f"[Investigation Output (Exit Code 0)]:\n{sanitized_output}\n\n"
                                "Phase 1 search is complete. Now proceed to Phase 2: emit the bash block with the discovered application binary in background (e.g. ```bash\n<binary> &\n```) to execute the action for the user."
                            )
                        else:
                            observation = (
                                f"[Execution Result (Exit Code 0)]:\n{sanitized_output}\n\n"
                                "The command/application was executed successfully. Respond to the user concisely and pleasantly, reporting the result."
                            )
                    # Auto-record learned preferences for substantive commands
                    if getattr(profile, "reinforcement_learning", True) and not cmd.startswith(("sayri-pref", "grep", "which", "find", "cat", "echo", "pwd", "ls", "search_history")):
                        try:
                            user_intent = ""
                            for m_item in messages:
                                if m_item.get("role") == "user" and not m_item.get("content", "").startswith(("[", "⚠️")):
                                    user_intent = m_item.get("content", "").strip()
                                    break
                            if user_intent:
                                self.storage.record_preference(
                                    agent_id=profile.id,
                                    intent=user_intent[:120],
                                    command=cmd,
                                    success=(retcode == 0),
                                )
                        except Exception:
                            pass

                    next_messages.append({"role": "user", "content": observation})

                    self._react_loop(
                        query_id,
                        session_id,
                        profile,
                        next_messages,
                        cfg,
                        depth + 1,
                        on_delta,
                        on_done,
                        on_tool_start,
                        on_tool_finish,
                        on_error,
                    )
                    return

            # Final response (filter thinking tags / custom strip patterns)
            patterns_cfg = cfg.get_string("provider", "strip_patterns") if cfg else ""
            clean_reply = strip_unwanted_patterns(full_text, patterns_cfg)
            final_msg = Message(role="assistant", content=clean_reply)
            self.storage.add_message(session_id, final_msg)
            on_done(clean_reply)

        llm.stream_chat(
            base_url,
            api_key,
            model_name,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            timeout=cfg.get_int("provider", "timeout"),
            on_delta=_handle_delta,
            on_done=_handle_done,
            on_error=_handle_error,
        )
