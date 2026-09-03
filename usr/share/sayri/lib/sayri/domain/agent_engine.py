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
        """Constructs a token-efficient system prompt with self-awareness of sub-agents, skills and sandboxing."""
        installed = skills.list_skills()
        skills_summary = ""
        if installed:
            items = [f"- {s['name']}: {s['description']}" for s in installed[:15]]
            skills_summary = "\nINSTALLED SKILLS (read details with the 'read_skill' tool):\n" + "\n".join(items)

        sandbox_info = f"Active Isolation Level: {profile.sandbox.level.value}."
        if profile.sandbox.level == SandboxLevel.LEVEL_0_NO_EXEC:
            sandbox_info += " (STRICTLY FORBIDDEN TO EXECUTE BASH/SYSTEM COMMANDS; you are a purely conversational agent. If asked to run something, explain that your LEVEL_0_NO_EXEC sandbox forbids it)."
        elif profile.sandbox.level in (SandboxLevel.LEVEL_1_READONLY, SandboxLevel.LEVEL_2_ISOLATED_DEV):
            sandbox_info += " (You are in an isolated Bubblewrap container with no Wayland/X11 graphical server. You CANNOT open graphical application windows on the host screen)."

        base = (
            f"You are Sayri, the intelligent assistant, agentic orchestrator and operating-system copilot in Pulsar OS.\n"
            f"Active profile: {profile.name} (ID: {profile.id}).\n"
            f"Security: {sandbox_info}\n\n"
            "SANDBOX AND EXECUTION POLICY:\n"
            "- LEVEL_0_NO_EXEC: Purely conversational mode. Blocked from executing commands.\n"
            "- LEVEL_1_READONLY / LEVEL_2_ISOLATED_DEV: Isolated Bubblewrap environment for read, inspection or isolated development operations. It has no access to the display server (Wayland/X11); therefore, it CANNOT open graphical applications (such as calculator, editors or browsers) on the user's screen.\n"
            "- LEVEL_3_HOST_USER: Full access to the local user's host. It can interact with the system and launch graphical applications (e.g. `gnome-calculator &` or `gtk-launch org.gnome.Calculator`).\n"
            "- LEVEL_4_HOST_ROOT: Administrative access with Polkit elevation (pkexec).\n\n"
            "YOUR CAPABILITIES IN PULSAR OS:\n"
            "1. System Orchestration: You can read files, open applications and run tools within your sandbox limits.\n"
            "2. Sub-Agent Creation and Management: You can create sub-agents configured with different models and sandbox levels.\n"
            "3. Skills and Plugins: You can search for and install extensions from the Pulsar Store (https://store-os.inled.es).\n"
            "4. Memory & Conversation History: You maintain context with recent messages. If you need details from earlier in the conversation, you can emit ```search_history <keyword>``` to query past messages.\n"
            f"{skills_summary}\n\n"
            "CRITICAL EXECUTION AND TRUTHFULNESS RULES:\n"
            "1. If you need to perform an action on the system, emit a block:\n"
            "```bash\n<command>\n```\n"
            "2. ABSOLUTE TRUTHFULNESS: NEVER claim an application has opened or an action has completed unless the system observation confirms exit code 0 with no sandbox errors.\n"
            "3. If a command fails due to sandbox (non-zero exit code or display/permission error), explain to the user with total honesty and clarity the sandbox restriction that prevented the action and how to fix it (for example by switching the gateway/agent to LEVEL_3_HOST_USER).\n"
            "4. Always respond in the language the user spoke to you in, naturally, concisely and pleasantly (1 to 3 spoken sentences for voice)."
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
            agent_id=profile.id, title=user_text[:30]
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
        messages = [{"role": "system", "content": self.build_system_prompt(profile)}]

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
                compact_note = (
                    "[Previous context summary in this session:\n"
                    + "\n".join(topics_summary)
                    + "\n(Use ```search_history <keyword>``` if you need older verbatim details)]"
                )
                messages.append({"role": "system", "content": compact_note})

            # Append the most recent 4 messages for immediate conversational continuity
            for m in all_past_msgs[-4:]:
                messages.append({"role": m.role, "content": m.content})
        else:
            for m in all_past_msgs:
                messages.append({"role": m.role, "content": m.content})

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
        if not self._active_queries.get(query_id, False) or depth > 6:
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
            on_error(exc)

        def _handle_done(full_text: str) -> None:
            if not self._active_queries.get(query_id, False):
                return

            # 1. Check for internal SQLite conversation history search tool (works in LEVEL_0_NO_EXEC without touching host)
            m_hist = re.search(r"```(?:search_history|search_chat_history|recall_history)\s*\n(.*?)\n```", full_text, re.DOTALL)
            if not m_hist:
                m_hist = re.search(r"<(?:search_history|recall_history)>(.*?)</(?:search_history|recall_history)>", full_text, re.DOTALL)

            if m_hist and depth < 6:
                query_term = m_hist.group(1).strip()
                on_tool_start(f"search_history: {query_term}")
                past_matches = self.storage.search_session_messages(session_id, query=query_term, limit=6)
                if past_matches:
                    fmt_items = [f"- [{item['role'].upper()}]: {item['content']}" for item in past_matches]
                    obs = f"[Chat History Search Results for '{query_term}']:\n" + "\n".join(fmt_items)
                else:
                    obs = f"[Chat History Search Results for '{query_term}']: No matching messages found in session history."

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

            if m and depth < 6:
                cmd = m.group(1).strip()
                if cmd:
                    on_tool_start(cmd)
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
                        observation = (
                            f"⚠️ [EXECUTION ERROR (Exit Code {retcode})]:\n{sanitized_output}\n\n"
                            "CRITICAL RULE: The previous command did NOT run or failed due to security/sandbox restrictions or a system error. "
                            "You must explicitly inform the user that the action could NOT be performed, "
                            "explaining the exact sandbox or environment cause and which sandbox level would be required (e.g. LEVEL_3_HOST_USER for graphical apps). "
                            "NEVER say the application opened or that the command worked."
                        )
                    else:
                        observation = (
                            f"[Execution Result (Exit Code 0)]:\n{sanitized_output}\n\n"
                            "The command ran successfully. Respond to the user concisely and naturally, reporting the result."
                        )

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

            # Final response
            final_msg = Message(role="assistant", content=full_text)
            self.storage.add_message(session_id, final_msg)
            on_done(full_text)

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
