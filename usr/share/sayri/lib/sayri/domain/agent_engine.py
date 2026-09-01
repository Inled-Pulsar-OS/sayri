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
            skills_summary = "\nHABILIDADES INSTALADAS (Lee los detalles con la tool 'read_skill'):\n" + "\n".join(items)

        sandbox_info = f"Nivel de Aislamiento Activo: {profile.sandbox.level.value}."
        if profile.sandbox.level == SandboxLevel.LEVEL_0_NO_EXEC:
            sandbox_info += " (ESTRICTAMENTE PROHIBIDO EJECUTAR COMANDOS BASH/SISTEMA; eres un agente puramente conversacional. Si te piden ejecutar algo, explica que tu sandbox LEVEL_0_NO_EXEC lo prohíbe)."
        elif profile.sandbox.level in (SandboxLevel.LEVEL_1_READONLY, SandboxLevel.LEVEL_2_ISOLATED_DEV):
            sandbox_info += " (Estás en un contenedor aislado Bubblewrap sin servidor gráfico Wayland/X11. NO puedes abrir ventanas de aplicaciones gráficas en la pantalla del host)."

        base = (
            f"Eres Sayri, la asistente inteligente, orquestadora agéntica y copiloto de sistema operativo en Pulsar OS.\n"
            f"Perfil activo: {profile.name} (ID: {profile.id}).\n"
            f"Seguridad: {sandbox_info}\n\n"
            "POLÍTICA DE SANDBOX Y EJECUCIÓN:\n"
            "- LEVEL_0_NO_EXEC: Modo puramente conversacional. Bloqueado para ejecutar comandos.\n"
            "- LEVEL_1_READONLY / LEVEL_2_ISOLATED_DEV: Entorno aislado en Bubblewrap para operaciones de lectura, inspección o desarrollo aislado. No tiene acceso al servidor de pantalla (Wayland/X11); por tanto, NO puede abrir aplicaciones de interfaz gráfica (como calculadora, editores o navegadores) en la pantalla del usuario.\n"
            "- LEVEL_3_HOST_USER: Acceso completo al host del usuario local. Puede interactuar con el sistema y lanzar aplicaciones gráficas (ej. `gnome-calculator &` o `gtk-launch org.gnome.Calculator`).\n"
            "- LEVEL_4_HOST_ROOT: Acceso administrativo con elevación Polkit (pkexec).\n\n"
            "TUS CAPACIDADES EN PULSAR OS:\n"
            "1. Orquestación del Sistema: Puedes consultar archivos, abrir aplicaciones y ejecutar herramientas dentro de los límites de tu sandbox.\n"
            "2. Creación y Gestión de Subagentes: Puedes crear subagentes configurados con distintos modelos y niveles de sandbox.\n"
            "3. Habilidades (Skills) y Plugins: Puedes buscar e instalar extensiones de la Pulsar Store (https://store-os.inled.es).\n"
            f"{skills_summary}\n\n"
            "REGLAS CRÍTICAS DE EJECUCIÓN Y VERACIDAD:\n"
            "1. Si necesitas realizar una acción en el sistema, emite un bloque:\n"
            "```bash\n<comando>\n```\n"
            "2. VERACIDAD ABSOLUTA: NUNCA afirmes que una aplicación se ha abierto o que una acción se ha completado a menos que la observación del sistema confirme código de salida 0 sin errores de sandbox.\n"
            "3. Si un comando falla por sandbox (código distinto de 0 o error de display/permisos), explica al usuario con total honestidad y claridad la restricción de sandbox que impidió la acción y cómo solucionarlo (por ejemplo cambiando el gateway/agente a LEVEL_3_HOST_USER).\n"
            "4. Responde siempre en el idioma en el que te haya hablado el usuario de forma natural, concisa y agradable (1 a 3 frases habladas para voz)."
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
            "nuevo subagente", "configura un subagente", "configurar subagente", "quiero un subagente"
        ]
        if any(trig in clean_prompt for trig in subagent_triggers):
            if profile.sandbox.level in (SandboxLevel.LEVEL_0_NO_EXEC, SandboxLevel.LEVEL_1_READONLY, SandboxLevel.LEVEL_2_ISOLATED_DEV):
                err_msg = (
                    f"⚠️ Error de Seguridad: El agente actual está ejecutándose en un entorno restringido ({profile.sandbox.level.value}). "
                    "Para prevenir escalada de privilegios, este nivel de sandbox tiene terminantemente prohibido crear o configurar subagentes en el sistema. "
                    "Esta acción debe realizarse desde la aplicación de escritorio de Sayri o mediante un agente con nivel LEVEL_3_HOST_USER."
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
            "instala la habilidad", "instalar habilidad", "instala el plugin", "instalar plugin"
        ]
        if any(trig in clean_prompt for trig in skill_triggers) and profile.sandbox.level in (SandboxLevel.LEVEL_0_NO_EXEC, SandboxLevel.LEVEL_1_READONLY, SandboxLevel.LEVEL_2_ISOLATED_DEV):
            err_msg = (
                f"⚠️ Error de Seguridad: El agente actual está ejecutándose en nivel {profile.sandbox.level.value}. "
                "No tiene permisos para instalar o registrar nuevas habilidades/plugins en el sistema. "
                "Para instalar extensiones, utiliza la Pulsar Store o la interfaz de Ajustes de Sayri."
            )
            reply_msg = Message(role="assistant", content=err_msg)
            self.storage.add_message(session.id, reply_msg)
            on_delta(err_msg)
            on_done(err_msg)
            return query_id

        # 3. Async AI Title Generator
        if len(session.messages) <= 2 or session.title.startswith("Nueva Conversación") or session.title == user_text[:30]:
            self._generate_session_title_async(session.id, user_text, cfg)

        # Prepare messages payload
        messages = [{"role": "system", "content": self.build_system_prompt(profile)}]
        # Sliding context window (last 10 messages) for token efficiency
        for m in session.messages[-10:]:
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
                        "content": "Eres un titulador de conversaciones. Genera un título ultra corto de 3 a 4 palabras en español que resuma la consulta del usuario. Responde ÚNICAMENTE con las 3-4 palabras, sin comillas, sin punto y sin explicaciones."
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

            # Check for bash commands in reply
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
                            f"⚠️ [ERROR EN EJECUCIÓN (Exit Code {retcode})]:\n{sanitized_output}\n\n"
                            "REGLA CRÍTICA: El comando anterior NO se ejecutó o falló debido a restricciones de seguridad/sandbox o error del sistema. "
                            "Debes informar explícitamente al usuario de que la acción NO se ha podido realizar, "
                            "explicando la causa exacta de sandbox o entorno y qué nivel de sandbox se requeriría (ej. LEVEL_3_HOST_USER para apps gráficas). "
                            "NUNCA digas que la aplicación se abrió o que el comando funcionó."
                        )
                    else:
                        observation = (
                            f"[Resultado de la Ejecución (Código 0)]:\n{sanitized_output}\n\n"
                            "El comando se ejecutó exitosamente. Responde al usuario de forma concisa y natural informando del resultado."
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
