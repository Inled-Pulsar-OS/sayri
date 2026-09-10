"""Sayri command-line interface (plain-text, order-independent).

The CLI speaks plain words, not flags::

    sayri help
    sayri status
    sayri talk "apaga la luz"
    sayri ask "¿qué hora es?"
    sayri listen
    sayri toggle
    sayri vault add API_KEY sk-1234 "clave de la api"
    sayri agents create Compilador prompt "eres un compilador" level 3
    sayri gateway create telegram "bot vip" token 123:ABC
    sayri routines add Despertador prompt "dame el tiempo" at 08:30
    sayri downloads model tiny en
    sayri sessions rename abc123 Hola
    sayri config set stt.mode manual
    sayri orb start
    sayri daemon
    sayri daemon stop

Command *words* can appear in any order; values can be given positionally
(``vault add API_KEY sk-1234``) or as ``key=value`` pairs
(``vault add key=API_KEY value=sk-1234``).  Runtime commands (talk/ask/
toggle/interrupt/status/config...) talk to the daemon over IPC; management
commands (vault, agents, routines, sessions, skills, plugins, gateways,
downloads) operate the same storage the UI uses and work without a daemon.
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

from .ipc import SayriClient, SayriError, daemon_is_running


# --------------------------------------------------------------------------- I/O

def _ensure_daemon(wait_s: float = 4.0) -> bool:
    """If the headless daemon is not running, start it detached (no terminal)."""
    if daemon_is_running():
        return True
    from . import paths
    log = os.path.join(paths.state_dir(), "daemon.log")
    os.makedirs(os.path.dirname(log), exist_ok=True)
    with open(log, "ab", buffering=0) as log_fd:
        proc = subprocess.Popen(
            [sys.executable, "-c", "import sys; from sayri.daemon import main; sys.exit(main())"],
            stdin=subprocess.DEVNULL,
            stdout=log_fd,
            stderr=log_fd,
            start_new_session=True,
            close_fds=True,
        )
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if daemon_is_running():
            return True
        time.sleep(0.2)
    print(f"daemon did not come up (log: {log}, rc still running pid={proc.pid})", file=sys.stderr)
    return False


def _client() -> SayriClient:
    c = SayriClient()
    if not c.connect():
        raise SayriError(
            "Sayri daemon is not running. Start it with:  sayri daemon"
        )
    return c


def _print_json(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _print_status(status: dict) -> None:
    provider = status.get("provider", {})
    missing = ", ".join(status.get("stt_missing", []) or status.get("tts_missing", []) or [])
    print(f"Sayri v{status.get('version', '?')}")
    print(f"  state:      {status.get('state')}{'  (busy)' if status.get('busy') else ''}")
    print(f"  STT:        {'ready' if status.get('stt_ready') else 'missing: ' + missing} (mode: {status.get('stt_mode')})")
    print(f"  TTS:        {'ready' if status.get('tts_ready') else 'not ready'}")
    print(f"  provider:   {provider.get('base_url')} / {provider.get('model')} / api_key={'set' if provider.get('api_key_set') else 'unset'}")
    print(f"  session:    {status.get('session_id')}  (agent: {status.get('agent')})")
    print(f"  ui_visible: {status.get('ui_visible')}")


# --------------------------------------------------------------------- parsing

def _norm(tok: str) -> str:
    return tok.strip().lower().lstrip("-.").rstrip(",.!?;:()[]{}%").strip()


# key=value and "tag <value>" extraction (order-independent).
_TAGS = {
    "key", "value", "desc", "description", "name", "prompt", "instructions",
    "agent", "level", "sandbox", "id", "query", "slug", "title", "timeout",
    "limit", "at", "every", "every_hours", "plugin", "token", "secret",
    "interval", "temperature", "guests", "resume", "language", "size",
    "quality", "voice", "model", "from", "max_tokens",
    "nombre", "titulo", "descripcion", "instrucciones", "modelo", "lenguaje",
    "contenido", "tiempo", "intervalo", "proveedor", "favicon", "web", "nivel",
}

_KEY_ALIASES = {
    "titulo": "title", "nombre": "name", "descripcion": "desc",
    "instrucciones": "prompt", "modelo": "model", "lenguaje": "language",
    "contenido": "prompt", "tiempo": "timeout", "intervalo": "interval",
    "identificador": "id", "plugin": "plugin", "size": "size", "nivel": "level",
}

_NOISE = {"por", "favor", "please", "por_favor", "sayri"}


def _extract_pairs(tokens: list[str]) -> tuple[dict[str, str], list[str]]:
    """Split ``key=value`` and ``tag value`` tokens; return (pairs, rest)."""
    pairs: dict[str, str] = {}
    rest: list[str] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if "=" in t and not t.startswith("=") and not t.endswith("="):
            k, _, v = t.partition("=")
            key = _norm(k).replace("-", "_")
            pairs[_KEY_ALIASES.get(key, key)] = v.strip()
            i += 1
            continue
        low = _norm(t)
        if low in _TAGS and i + 1 < len(tokens):
            pairs[_KEY_ALIASES.get(low, low)] = tokens[i + 1].strip()
            i += 2
            continue
        rest.append(t)
        i += 1
    return pairs, rest


def _pick_action(rest: list[str], actions: dict[str, set[str]], default: str) -> tuple[str, list[str]]:
    """Find the action with the most matched keywords; removes its tokens."""
    best_name = default
    best_score = 0
    best_pos = 10**9
    for name, toks in actions.items():
        score = 0
        first = 10**9
        for idx, t in enumerate(rest):
            if _norm(t) in toks:
                score += 1
                first = min(first, idx)
        if score and (score > best_score or (score == best_score and first < best_pos)):
            best_name = name
            best_score = score
            best_pos = first
    cleaned = [t for t in rest if _norm(t) not in actions.get(best_name, set())]
    return best_name, cleaned


class _Cmd:
    def __init__(self, name: str, priority: int, area: set[str],
                 handler: Any, help: str, actions: dict[str, set[str]] | None = None,
                 default: str = ""):
        self.name = name
        self.priority = priority
        self.area = area
        self.handler = handler
        self.help = help
        self.actions = actions or {}
        self.default = default


def _match_command(rest: list[str]) -> Optional[tuple[_Cmd, dict[str, str], list[str]]]:
    scores: list[tuple[int, int, int, _Cmd, list[int]]] = []
    for cmd in _COMMANDS:
        hits = 0
        first = 10**9
        for idx, t in enumerate(rest):
            if _norm(t) in cmd.area:
                hits += 1
                first = min(first, idx)
        if hits:
            scores.append((hits, -first, cmd.priority, cmd, first))
    if not scores:
        return None
    # (hits desc, earliest position asc, priority desc) -> best command words
    scores.sort(key=lambda x: (x[0], x[1], x[3].priority), reverse=True)
    cmd = scores[0][3]
    area = cmd.area
    action = ""
    if cmd.actions:
        action, _ = _pick_action(rest, cmd.actions, cmd.default)
    cleaned = [t for t in rest if _norm(t) not in area]
    cleaned = [t for t in cleaned if _norm(t) not in _NOISE]
    if cleaned and _norm(cleaned[0]) in area:
        cleaned = [t for t in cleaned if _norm(t) not in area]
    return cmd, {"action": action}, cleaned


_LEGACY_VERBS = {
    "switch-session": ["sessions", "switch"],
    "switch_session": ["sessions", "switch"],
    "new-conversation": ["new"],
    "new_conversation": ["new"],
    "stop_listening": ["stop"],
}


def _normalize(argv: list[str]) -> list[str]:
    out: list[str] = []
    for tok in argv:
        low = tok.lower()
        if low in _LEGACY_VERBS:
            out.extend(_LEGACY_VERBS[low])
        else:
            out.append(tok)
    return out


# -------------------------------------------------------------------- runtime


def _run_agent_switch(pairs: dict, rest: list[str]) -> int:
    c = _client()
    try:
        r = c.request("agent_switch", {"agent_id": rest[0] if rest else pairs.get("agent", "")})
        print(f"active agent: {r.get('name')} ({r.get('agent_id')})")
    finally:
        c.close()
    return 0


# ------------------------------------------------------------------- commands

def cmd_help(pairs: dict, rest: list[str]) -> int:
    topic = (" ".join(rest)).strip().lower()
    SHELLS = {
        "vault": "vault", "boveda": "vault", "secrets": "vault", "secretos": "vault",
        "agents": "agents", "agent": "agents", "agentes": "agents", "subagent": "agents",
        "skills": "skills", "habilidades": "skills",
        "plugins": "plugins", "extensiones": "plugins",
        "gateway": "gateway", "gateways": "gateway", "puerta": "gateway",
        "routines": "routines", "rutinas": "routines", "cron": "routines",
        "sessions": "sessions", "conversations": "sessions", "conversaciones": "sessions",
        "config": "config", "settings": "config", "ajustes": "config",
        "downloads": "downloads", "model": "downloads", "voice": "downloads",
        "talk": "talk", "ask": "ask", "listen": "listen",
        "orb": "orb", "daemon": "daemon", "status": "status",
    }
    if topic in SHELLS:
        canonical = SHELLS[topic]
    elif topic and not any(topic.startswith(s) for s in SHELLS):
        # allow anything not a command to just show general help
        printed = False
        for c in _COMMANDS:
            if _norm(topic) in c.area or any(t in c.area for t in topic.split()):
                _print_cmd_help(c)
                printed = True
        if printed:
            return 0
        canonical = ""
    else:
        canonical = topic
    if canonical:
        for c in _COMMANDS:
            if c.name == canonical:
                _print_cmd_help(c)
                return 0
    print("Sayri CLI — texto plano, sin flags. ``sayri <comando>``")
    print()
    print("Manos libres / runtime:")
    print("  sayri status                 estado del core, proveedor y sesión")
    print("  sayri talk <texto>           enviar texto (async, responde con TTS)")
    print("  sayri ask <texto>            preguntar y esperar respuesta completa")
    print("  sayri listen [timeout S]     transcribir la siguiente frase")
    print("  sayri toggle                 activa/desactiva el micrófono")
    print("  sayri stop                   dejar de escuchar")
    print("  sayri interrupt              cortar TTS/generación")
    print("  sayri new                    nueva conversación")
    print("  sayri sessions ...           list|switch|rename|delete|show")
    print("  sayri agents ...             list|create|edit|use|delete|show")
    print("  sayri daemon [start|stop|status]    (start = en segundo plano)")
    print("  sayri ui [start|stop|status]         lanza la UI predeterminada")
    print()
    print("Gestión:")
    print("  sayri config ...             get|set|list")
    print("  sayri vault ...              list|add|get|delete")
    print("  sayri skills ...             list|search|install|read|remove")
    print("  sayri plugins ...            list|config|show")
    print("  sayri gateway ...            list|start|stop|create|edit|delete|pin|open")
    print("  sayri routines ...           list|create|edit|toggle|run|delete|show")
    print("  sayri downloads ...          status|model|voice|whisper|piper")
    print()
    print("Sistema:")
    print("  sayri orb [start|stop|status]   UI plugin (orb GTK4)")
    print("  sayri version")
    print("  sayri help [tema]               esta ayuda")
    print("  sayri killall                    termina todos los procesos Sayri")
    print()
    print("Valores: ``clave=valor`` o ``tag valor`` en cualquier orden.")
    print("Ejemplo:  sayri vault add key=API_KEY value=sk-1234")
    return 0


def _print_cmd_help(cmd: _Cmd) -> None:
    print(f"sayri {cmd.name} — {cmd.help}")
    if cmd.actions:
        print("  acciones: " + ", ".join(sorted(cmd.actions)))


def cmd_daemon(pairs: dict, rest: list[str]) -> int:
    action = _pick_action(rest, {
        "start": {"start", "arranca", "iniciar", "go", "serve", "correr"},
        "stop": {"stop", "parar", "apagar", "quit", "down"},
        "status": {"status", "estado", "check"},
        "restart": {"restart", "reiniciar", "reload"},
    }, "start")[0]
    if action == "stop":
        if not daemon_is_running():
            print("daemon is not running")
            return 0
        c = _client()
        try:
            c.request("quit")
        finally:
            c.close()
        print("daemon stopped ✓")
        return 0
    if action == "status":
        print("running" if daemon_is_running() else "not running")
        return 0
    if action == "restart":
        subprocess.Popen(["pkill", "-f", "sayri.daemon|python3 -m sayri daemon"])
        time.sleep(0.5)
    if daemon_is_running():
        print("daemon already running")
        return 0
    if not _ensure_daemon():
        return 1
    print("daemon started in background ✓")
    status = _client()
    try:
        info = status.request("status", timeout=5)
    finally:
        status.close()
    print(f"  version:  {info.get('version')}")
    print(f"  agent:    {info.get('agent')}")
    print(f"  session:  {info.get('session_id')}")
    print("  UIs/CLI  connect over the IPC socket (sayri-daemon.sock).")
    return 0


def cmd_version(pairs: dict, rest: list[str]) -> int:
    from . import __version__
    print(__version__)
    return 0


def cmd_status(pairs: dict, rest: list[str]) -> int:
    c = _client()
    try:
        status = c.request("status", timeout=5)
    finally:
        c.close()
    if any(_norm(t) in ("json", "raw") for t in rest):
        _print_json(status)
    else:
        _print_status(status)
    return 0


def cmd_talk(pairs: dict, rest: list[str]) -> int:
    text = pairs.get("text") or " ".join(rest)
    if not text:
        print("¿qué quieres que diga?  ej: sayri talk hola", file=sys.stderr)
        return 1
    c = _client()
    try:
        c.request("talk", {"text": text}, timeout=10)
    finally:
        c.close()
    print("scheduled ✓ (the daemon will speak it once it processes)")
    return 0


def cmd_ask(pairs: dict, rest: list[str]) -> int:
    text = pairs.get("text") or pairs.get("prompt") or " ".join(rest)
    if not text:
        print("¿qué preguntas?  ej: sayri ask qué hora es", file=sys.stderr)
        return 1
    if not daemon_is_running():
        return _ask_inline(text)
    params: dict = {"text": text, "timeout": 150}
    if pairs.get("agent"):
        params["target_agent_id"] = pairs["agent"]
    c = _client()
    try:
        result = c.request("ask", params, timeout=200)
    finally:
        c.close()
    if not isinstance(result, dict):
        print(result)
        return 0
    if result.get("error"):
        print(f"error: {result['error']}", file=sys.stderr)
        return 1
    text = (result.get("text") or "").strip()
    print(text)
    return 0


def _ask_inline(text: str) -> int:
    """One-shot headless ask without a daemon."""
    from .core import SayriCore

    core = SayriCore()
    try:
        result = core.ask(text, timeout=150)
    finally:
        core.shutdown()
    if result.get("error"):
        print(f"error: {result['error']}", file=sys.stderr)
        return 1
    print((result.get("text") or "").strip())
    return 0


def cmd_listen(pairs: dict, rest: list[str]) -> int:
    timeout = 60.0
    try:
        timeout = float(pairs.get("timeout", 60.0))
    except ValueError:
        pass
    if rest and not daemon_is_running():
        timeout = 60.0
    c = _client()
    try:
        result = c.request("listen", {"timeout": timeout}, timeout=timeout + 10)
    finally:
        c.close()
    text = (result.get("text") or "") if isinstance(result, dict) else ""
    if text:
        print(text)
        return 0
    print("no speech detected", file=sys.stderr)
    return 1


def cmd_stop(pairs: dict, rest: list[str]) -> int:
    c = _client()
    try:
        c.request("stop_listening")
    finally:
        c.close()
    print("stopped ✓")
    return 0


def cmd_toggle(pairs: dict, rest: list[str]) -> int:
    c = _client()
    try:
        c.request("toggle_listening")
    finally:
        c.close()
    print("toggled ✓")
    return 0


def cmd_interrupt(pairs: dict, rest: list[str]) -> int:
    c = _client()
    try:
        c.request("interrupt")
    finally:
        c.close()
    print("interrupted ✓")
    return 0


def cmd_new(pairs: dict, rest: list[str]) -> int:
    c = _client()
    try:
        r = c.request("new_conversation")
        print(f"new conversation: {r.get('session_id')}")
    finally:
        c.close()
    return 0


def _storage():
    from sayri.adapters.storage.sqlite_sessions import SQLiteSessionRepository
    return SQLiteSessionRepository()


def _resolve_session(prefix: str) -> Optional[Any]:
    repo = _storage()
    if not prefix:
        return None
    for s in repo.list_sessions(limit=2000, include_empty=True):
        if s.id == prefix or s.id.startswith(prefix):
            return s
    return None


def cmd_sessions(pairs: dict, rest: list[str]) -> int:
    action, rest = _pick_action(rest, {
        "switch": {"switch", "resume", "open", "abrir", "activar", "use"},
        "rename": {"rename", "renombrar", "title", "titulo"},
        "delete": {"delete", "del", "rm", "remove", "borrar", "eliminar"},
        "show": {"show", "ver", "details", "detalles"},
        "search": {"search", "buscar", "find"},
        "list": {"list", "ls", "mostrar", "all", "msgs"},
    }, "list")
    repo = _storage()
    if action == "switch":
        sid = rest[0] if rest else pairs.get("id", "")
        if not sid:
            print("¿qué sesión?  ej: sayri sessions switch abc12", file=sys.stderr)
            return 1
        c = _client()
        try:
            r = c.request("switch_session", {"session_id": sid})
            print(f"switched to: {r.get('session_id')}")
        finally:
            c.close()
        return 0
    if action == "rename":
        sid = rest[0] if rest else pairs.get("id", "")
        if not sid:
            print("¿qué sesión y qué título?", file=sys.stderr)
            return 1
        sess = _resolve_session(sid)
        if not sess:
            print(f"session not found: {sid}", file=sys.stderr)
            return 1
        title = pairs.get("title") or pairs.get("titulo") or pairs.get("nombre") or " ".join(rest[1:])
        repo.update_session_title(sess.id, title)
        print(f"renamed {sess.id[:8]} → {title}")
        return 0
    if action == "delete":
        sid = rest[0] if rest else pairs.get("id", "")
        if not sid:
            print("¿qué sesión?", file=sys.stderr)
            return 1
        s = _resolve_session(sid)
        if not s:
            print(f"session not found: {sid}", file=sys.stderr)
            return 1
        repo.delete_session(s.id)
        print(f"deleted session {s.id[:8]}")
        return 0
    if action == "show":
        sid = rest[0] if rest else pairs.get("id", "")
        if not sid:
            print("¿qué sesión?", file=sys.stderr)
            return 1
        s = _resolve_session(sid)
        if not s:
            print(f"session not found: {sid}", file=sys.stderr)
            return 1
        print(f"{s.id}  ({s.title})  agent={s.agent_id}")
        for m in s.messages:
            role = getattr(m, "role", "?")
            content = getattr(m, "content", str(m))
            print(f"  {role:9s}  {content}")
        return 0
    if action == "search":
        q = pairs.get("query") or " ".join(rest)
        for row in repo.search_all_messages(query=q, limit=20):
            print(f"{row['session_id'][:8]} [{row.get('role', '')}] {row.get('content', '')[:120]}")
        return 0
    limit = 20
    try:
        limit = int(pairs.get("limit", 20))
    except ValueError:
        pass
    rows = repo.list_sessions(limit=limit, include_empty=False)
    if not rows:
        print("no conversations yet")
        return 0
    active = None
    if daemon_is_running():
        try:
            c = _client()
            active = c.request("status", timeout=5).get("session_id")
            c.close()
        except Exception:
            active = None
    for s in rows:
        mark = "  *" if active and s.id == active else ""
        print(f"{s.id}  {s.title}  [{len(s.messages)} msgs]  agent={s.agent_id}{mark}")
    return 0


def _parse_level(raw: str) -> str:
    """Map user input to a SandboxLevel member name."""
    r = _norm(raw).replace("level_", "").replace("level-", "").replace("LEVEL_", "")
    table = {
        "0": "LEVEL_0_NO_EXEC", "1": "LEVEL_1_READONLY", "2": "LEVEL_2_ISOLATED_DEV",
        "3": "LEVEL_3_HOST_USER", "4": "LEVEL_4_HOST_ROOT",
        "no_exec": "LEVEL_0_NO_EXEC", "noexec": "LEVEL_0_NO_EXEC",
        "readonly": "LEVEL_1_READONLY", "read_only": "LEVEL_1_READONLY",
        "isolated": "LEVEL_2_ISOLATED_DEV", "isolated_dev": "LEVEL_2_ISOLATED_DEV",
        "host": "LEVEL_3_HOST_USER", "host_user": "LEVEL_3_HOST_USER",
        "root": "LEVEL_4_HOST_ROOT", "host_root": "LEVEL_4_HOST_ROOT",
    }
    key = r.replace("-", "_")
    return table.get(key, table.get(r.lower().replace(" ", "_"), "LEVEL_3_HOST_USER"))


def cmd_agents(pairs: dict, rest: list[str]) -> int:
    from sayri.domain.agent_creator import AgentCreator
    from sayri.domain.models import AgentModelConfig, AgentProfile, SandboxConfig, SandboxLevel

    action, rest = _pick_action(rest, {
        "create": {"create", "new", "crear", "add", "añadir", "agregar", "make", "nuevo", "setup"},
        "edit": {"edit", "editar", "update", "modificar", "config", "configure"},
        "use": {"use", "select", "switch", "usar", "activar", "activa", "choose", "cambiar", "cambia"},
        "delete": {"delete", "del", "rm", "remove", "borrar", "eliminar", "trash"},
        "show": {"show", "get", "view", "info", "ver", "detalles", "details"},
        "list": {"list", "ls", "mostrar", "all"},
    }, "list")
    if action == "use":
        aid = rest[0] if rest else pairs.get("agent", "")
        if not aid:
            print("¿qué agente?  ej: sayri agents use compilador", file=sys.stderr)
            return 1
        return _run_agent_switch({}, [aid])
    if action == "show":
        aid = rest[0] if rest else pairs.get("id", "")
        a = AgentCreator.get_agent(aid) if aid else None
        if not a:
            print(f"agent not found: {aid}", file=sys.stderr)
            return 1
        print(f"{a.id}  {a.name}  [{a.sandbox.level.value}]  {a.description}")
        if a.system_prompt:
            print(f"  prompt: {a.system_prompt}")
        if a.custom_instructions:
            print(f"  extra:  {a.custom_instructions}")
        return 0
    if action == "delete":
        aid = rest[0] if rest else pairs.get("id", "")
        if not aid:
            print("¿qué agente?  ej: sayri agents delete compilador", file=sys.stderr)
            return 1
        if AgentCreator.delete_agent(aid):
            print(f"deleted agent: {aid}")
        else:
            print(f"could not delete agent: {aid} (built-in?)", file=sys.stderr)
            return 1
        return 0
    if action in ("create", "edit"):
        aid = rest[0] if rest else (pairs.get("id") or pairs.get("name"))
        if not aid:
            print("¿nombre del agente?  ej: sayri agents create Compilador …", file=sys.stderr)
            return 1
        existing = AgentCreator.get_agent(aid) if action == "edit" else None
        name = existing.name if existing else aid.capitalize()
        level = existing.sandbox.level if existing else None
        if pairs.get("level"):
            try:
                level = getattr(SandboxLevel, _parse_level(pairs["level"]))
            except Exception:
                print(f"invalid level: {pairs['level']}", file=sys.stderr)
                return 1
        profile = AgentProfile(
            id=existing.id if existing else aid,
            name=pairs.get("name") or name,
            description=pairs.get("desc") or pairs.get("description") or
                     (existing.description if existing else ""),
            system_prompt=pairs.get("prompt") or pairs.get("instructions") or
                          (existing.system_prompt if existing else "You are Sayri."),
            model=AgentModelConfig(
                provider=pairs.get("provider") or (existing.model.provider if existing else "default"),
                model_name=pairs.get("model") or (existing.model.model_name if existing else "default"),
                temperature=float(pairs.get("temperature", getattr(existing.model, "temperature", 0.7) if existing else 0.7)),
            ),
            sandbox=SandboxConfig(
                level=level if level is not None else SandboxLevel.LEVEL_3_HOST_USER,
                allow_network=True,
            ),
            allowed_skills=existing.allowed_skills if existing else [],
            allowed_plugins=existing.allowed_plugins if existing else [],
            allowed_tools=existing.allowed_tools if existing else ["bash", "read_skill", "search_history"],
            custom_instructions=pairs.get("instructions") or getattr(existing, "custom_instructions", ""),
            investigation_loop=bool(existing.investigation_loop) if existing else True,
            reinforcement_learning=bool(existing.reinforcement_learning) if existing else True,
            created_at=getattr(existing, "created_at", time.time()),
            is_builtin=bool(getattr(existing, "is_builtin", False)),
        )
        path = AgentCreator.save_agent(profile)
        print(f"{'updated' if action == 'edit' else 'created'} agent '{profile.name}' (id={profile.id}, level={profile.sandbox.level.value})")
        print(f"  → {path}")
        return 0
    for a in AgentCreator.list_agents():
        print(f"{a.id}  {a.name}  [{a.sandbox.level.value}]  {a.description}")
    return 0


def cmd_skills(pairs: dict, rest: list[str]) -> int:
    from sayri import skills

    action, rest = _pick_action(rest, {
        "search": {"search", "buscar", "find", "busca"},
        "install": {"install", "instalar", "instala", "add", "get"},
        "read": {"read", "show", "ver", "details", "detalles", "info"},
        "remove": {"remove", "uninstall", "uninstalar", "delete", "del", "rm", "borrar", "eliminar", "quitar"},
        "list": {"list", "ls", "mostrar", "all"},
    }, "list")
    if action == "search":
        q = pairs.get("query") or " ".join(rest)
        for s in skills.search_skills(q):
            print(f"{s.get('id') or s.get('name')} — {s.get('description', '')}")
        return 0
    if action == "install":
        slug = rest[0] if rest else pairs.get("slug", "")
        if not slug:
            print("¿qué paquete?  ej: sayri skills install sqlite", file=sys.stderr)
            return 1
        try:
            ok = skills.install_skill(slug)
        except Exception as exc:
            print(f"install failed: {exc}", file=sys.stderr)
            return 1
        print("installed ✓" if ok else "failed")
        return 0 if ok else 1
    if action == "read":
        slug = rest[0] if rest else pairs.get("name", "")
        if not slug:
            print("¿qué skill?", file=sys.stderr)
            return 1
        text = skills.read_skill(slug)
        if not text:
            print(f"skill not found: {slug}", file=sys.stderr)
            return 1
        print(text)
        return 0
    if action == "remove":
        slug = rest[0] if rest else pairs.get("name", "")
        if not slug:
            print("¿qué skill?", file=sys.stderr)
            return 1
        ok = skills.uninstall_skill(slug)
        print("removed ✓" if ok else f"not found: {slug}")
        return 0 if ok else 1
    rows = skills.list_skills()
    if not rows:
        print("no skills installed")
        return 0
    for s in rows:
        print(f"{s.get('id') or s.get('name')} — {s.get('description', '')}")
    return 0


def cmd_plugins(pairs: dict, rest: list[str]) -> int:
    from sayri.gateway_supervisor import GatewaySupervisor
    import json as _json

    action, rest = _pick_action(rest, {
        "config": {"config", "edit", "editar", "configure", "configurar", "allow", "permisos", "set", "arandela"},
        "show": {"show", "info", "ver", "details", "detalles"},
        "list": {"list", "ls", "mostrar", "all"},
    }, "list")
    supervisor = GatewaySupervisor.get_instance()
    plugins = supervisor.list_installed_plugins()
    if action == "show":
        pid = rest[0] if rest else pairs.get("id", "")
        pl = None
        if pid:
            pl = _find_plugin(supervisor, pid)
        if not pl:
            print(f"plugin not found: {pid}", file=sys.stderr)
            return 1
        print(f"{pl['id']}  {pl.get('name')}  v{pl.get('version')}")
        print(f"  desc:      {pl.get('description')}")
        print(f"  auth:      {pl.get('auth_mode')}  secrets={pl.get('required_secrets', [])}")
        print(f"  path:      {pl.get('path')}")
        print(f"  chat_url:  {pl.get('chat_url', '')}")
        return 0
    if action == "config":
        pid = rest[0] if rest else pairs.get("id", "")
        pl = _find_plugin(supervisor, pid) if pid else None
        if not pl or not pl.get("path"):
            print(f"plugin not found: {pid}", file=sys.stderr)
            return 1
        manifest_path = Path(pl["path"])
        if manifest_path.is_dir():
            manifest_path = manifest_path / "manifest.json"
        if not manifest_path.is_file():
            print(f"no manifest.json at {manifest_path}", file=sys.stderr)
            return 1
        try:
            data = _json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"could not read manifest: {exc}", file=sys.stderr)
            return 1
        changed = []
        if pairs.get("level"):
            lvl_name = _parse_level(pairs["level"])
            for key in ("min_sandbox_level", "sandbox_level"):
                if key in data:
                    data[key] = lvl_name
                    changed.append(f"{key}={lvl_name}")
        if "allow_in_level_0" in data and pairs.get("level0"):
            data["allow_in_level_0"] = pairs["level0"].lower() in ("1", "yes", "true", "on", "si")
            changed.append(f"allow_in_level_0={data['allow_in_level_0']}")
        elif "allow_in_level_0" not in data and pairs.get("level0"):
            data["allow_in_level_0"] = pairs["level0"].lower() in ("1", "yes", "true", "on", "si")
            changed.append(f"allow_in_level_0={data['allow_in_level_0']}")
        if not changed:
            print("nada que configurar. usa: sayri plugins config <id> level 3 [level0 yes|no]")
            return 0
        try:
            manifest_path.write_text(_json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except PermissionError:
            # /usr/share → mirror plugin into the user dir and edit there
            from sayri import paths
            src = Path(pl["path"]).resolve()
            src_dir = src if src.is_dir() else src.parent
            user_dir = Path(paths.config_dir()) / "plugins" / pl["id"]
            user_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src_dir, user_dir, dirs_exist_ok=True)
            upath = user_dir / "manifest.json"
            data = _json.loads(upath.read_text(encoding="utf-8"))
            if pairs.get("level"):
                lvl_name = _parse_level(pairs["level"])
                for key in ("min_sandbox_level", "sandbox_level"):
                    if key in data:
                        data[key] = lvl_name
            if pairs.get("level0"):
                data["allow_in_level_0"] = pairs["level0"].lower() in ("1", "yes", "true", "on", "si")
            upath.write_text(_json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"copied plugin to {user_dir} and applied: {', '.join(changed)}")
            return 0
        print(f"applied: {', '.join(changed)}")
        return 0
    for p in plugins:
        print(f"{p.get('id')} — {p.get('description', p.get('name', ''))} (v{p.get('version', '?')})")
    return 0


def _resolve_gateway_id(rest: list[str], pairs: dict) -> str:
    if rest:
        return rest[0]
    return pairs.get("id", "")


def _find_plugin(supervisor: Any, token: str) -> Optional[dict]:
    low = _norm(token)
    for p in supervisor.list_installed_plugins():
        if p["id"] == token or p.get("name", "").lower() == low or token in p["id"]:
            return p
    return None


def cmd_gateway(pairs: dict, rest: list[str]) -> int:
    from sayri.gateway_supervisor import GatewaySupervisor
    from sayri.domain.secrets_manager import secrets_manager

    action, rest = _pick_action(rest, {
        "start": {"start", "iniciar", "arranca", "launch", "up", "encender"},
        "stop": {"stop", "parar", "apagar", "down", "matar", "kill"},
        "delete": {"delete", "del", "rm", "remove", "borrar", "eliminar", "quitar"},
        "pin": {"pin", "pair", "pairing", "codigo", "show_pin", "show-pin", "otp"},
        "open": {"open", "abrir", "channel", "abre", "enlace", "link"},
        "edit": {"edit", "editar", "update", "modificar", "configure", "config"},
        "create": {"create", "add", "new", "crear", "nuevo", "añadir", "agregar", "setup"},
        "list": {"list", "ls", "mostrar", "all"},
    }, "list")
    supervisor = GatewaySupervisor.get_instance()
    if action == "start":
        iid = _resolve_gateway_id(rest, pairs)
        if not iid:
            print("¿qué gateway?  ej: sayri gateway start telegram-main", file=sys.stderr)
            return 1
        ok, msg = supervisor.start_instance(iid)
        print(msg)
        return 0 if ok else 1
    if action == "stop":
        iid = _resolve_gateway_id(rest, pairs)
        if not iid:
            print("¿qué gateway?", file=sys.stderr)
            return 1
        supervisor.stop_instance(iid)
        print(f"stopped {iid} ✓")
        return 0
    if action == "delete":
        iid = _resolve_gateway_id(rest, pairs)
        if not iid:
            print("¿qué gateway?", file=sys.stderr)
            return 1
        supervisor.delete_instance(iid)
        print(f"deleted {iid} ✓")
        return 0
    if action == "pin":
        iid = _resolve_gateway_id(rest, pairs)
        if not iid:
            print("¿qué gateway?", file=sys.stderr)
            return 1
        inst = supervisor.get_instance_config(iid)
        if not inst:
            print(f"gateway instance not found: {iid}", file=sys.stderr)
            return 1
        pin = f"{random.randint(100000, 999999)}"
        pin_file = Path.home() / ".config" / "sayri" / f"pairing_pin_{iid}.json"
        pin_file.write_text(json.dumps({
            "pin": pin,
            "created_at": time.time(),
            "expires_at": time.time() + 86400,
        }, indent=2), encoding="utf-8")
        os.chmod(pin_file, 0o600)
        print(f"PIN: {pin}")
        print(f"en el canal: /pair {pin}")
        return 0
    if action == "open":
        iid = _resolve_gateway_id(rest, pairs)
        pl = None
        if iid:
            inst = supervisor.get_instance_config(iid)
            if inst:
                for p in supervisor.list_installed_plugins():
                    if p["id"] == inst.get("plugin_id"):
                        pl = p
                        break
        if not pl:
            for p in supervisor.list_installed_plugins():
                if p["id"] == iid or p.get("name", "").lower() == _norm(iid):
                    pl = p
                    break
        if not pl or not pl.get("chat_url"):
            print("no chat_url for that gateway", file=sys.stderr)
            return 1
        subprocess.Popen(["xdg-open", pl["chat_url"]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"opened {pl['chat_url']}")
        return 0
    if action in ("create", "edit"):
        insts = supervisor.list_instances()
        if action == "edit":
            iid = _resolve_gateway_id(rest, pairs)
            inst = next((i for i in insts if i["id"] == iid), None)
            if not inst:
                print(f"gateway instance not found: {iid}", file=sys.stderr)
                return 1
            plugin_id = inst.get("plugin_id", "")
            iid = inst["id"]
        else:
            plugin_id = pairs.get("plugin") or (rest[0] if rest else "")
            name = pairs.get("name") or (rest[1] if len(rest) > 1 else "")
            if not plugin_id:
                print("¿qué plugin gateway?  ej: sayri gateway create telegram botname", file=sys.stderr)
                print("plugins disponibles:", ", ".join(p["id"] for p in supervisor.list_installed_plugins()))
                return 1
            pl = next((p for p in supervisor.list_installed_plugins()
                       if p["id"] == plugin_id or p.get("name", "").lower() == _norm(plugin_id)), None) or _find_plugin(supervisor, plugin_id)
            if not pl:
                print(f"plugin not found: {plugin_id}", file=sys.stderr)
                return 1
            plugin_id = pl["id"]
            name = name or pl.get("name", plugin_id)
            iid = f"{plugin_id[:12]}-{name[:16]}-{int(time.time())}"
        token = pairs.get("token") or pairs.get("secret")
        required_secret = (pl["required_secrets"] or [None])[0] if action == "create" else None
        if token and action == "create" and required_secret:
            secrets_manager.set_secret(required_secret, token, f"Token for {plugin_id}")
        elif token and action == "edit":
            secrets_manager.set_secret(plugin_id.upper(), token, f"Token for {plugin_id}")
        secret_key = token or (inst.get("secret_key") if action == "edit" else required_secret)
        lvl = pairs.get("level")
        sandbox = _parse_level(lvl) if lvl else inst.get("sandbox_level", "LEVEL_1_READONLY") if action == "edit" else "LEVEL_1_READONLY"
        timeout = pairs.get("timeout")
        instance_data = {
            "id": iid,
            "name": pairs.get("name") or (inst.get("name") if action == "edit" else name),
            "plugin_id": plugin_id,
            "agent_id": pairs.get("agent") or (inst.get("agent_id") if action == "edit" else "default"),
            "sandbox_level": sandbox,
            "secret_key": secret_key,
            "auth_mode": (inst.get("auth_mode") if action == "edit" else (pl.get("auth_mode") or "pairing_otp")),
            "allow_resume_previous": (pairs.get("resume", "yes").lower() in ("1", "yes", "true", "on", "si") if pairs.get("resume") else (inst.get("allow_resume_previous", True) if action == "edit" else True)),
            "inactivity_timeout_minutes": int(timeout) if timeout else (inst.get("inactivity_timeout_minutes", 30) if action == "edit" else 30),
            "enabled": True,
            "created_at": inst.get("created_at", time.time()) if action == "edit" else time.time(),
        }
        try:
            supervisor.save_instance(instance_data)
        except Exception as exc:
            print(f"save failed: {exc}", file=sys.stderr)
            return 1
        ok, msg = supervisor.start_instance(iid)
        print(f"{'updated' if action == 'edit' else 'created'} gateway {iid}")
        if ok:
            print("  started ✓")
        else:
            print(f"  {msg}")
        return 0
    for inst in supervisor.list_instances():
        state = "running" if supervisor.is_instance_running(inst["id"]) else "stopped"
        print(f"{inst['id']}  {inst.get('name', '')}  [{state}]  agent={inst.get('agent_id', 'default')}  sandbox={inst.get('sandbox_level', '-')}")
    return 0


def cmd_vault(pairs: dict, rest: list[str]) -> int:
    from sayri.domain.secrets_manager import secrets_manager

    action, rest = _pick_action(rest, {
        "add": {"add", "set", "guardar", "store", "nuevo", "crear", "añadir", "agregar", "save"},
        "get": {"get", "show", "leer", "print", "ver"},
        "delete": {"delete", "del", "rm", "remove", "borrar", "eliminar", "quitar"},
        "list": {"list", "ls", "mostrar", "all"},
    }, "list")
    if action == "add":
        key = pairs.get("key") or (rest[0] if rest else "")
        value = pairs.get("value") if "value" in pairs else (" ".join(rest[1:]) if rest else "")
        descr = pairs.get("desc") or pairs.get("description") or "secret added via CLI"
        if not key:
            print("¿clave?  ej: sayri vault add API_KEY sk-1234", file=sys.stderr)
            return 1
        if not value:
            print("¿valor?  ej: sayri vault add API_KEY sk-1234", file=sys.stderr)
            return 1
        secrets_manager.set_secret(key, value, descr)
        clean = key.strip().upper().replace(" ", "_")
        print(f"saved ${clean} ({len(value)} chars)")
        return 0
    if action == "get":
        key = pairs.get("key") or (rest[0] if rest else "")
        if not key:
            print("¿clave?  ej: sayri vault get API_KEY", file=sys.stderr)
            return 1
        val = secrets_manager.get_secret(key)
        if val is None:
            print(f"$SECRET:{key.upper()} no está en el vault", file=sys.stderr)
            return 1
        print(val)
        return 0
    if action == "delete":
        key = pairs.get("key") or (rest[0] if rest else "")
        if not key:
            print("¿clave?", file=sys.stderr)
            return 1
        if secrets_manager.delete_secret(key):
            print(f"deleted ${key.upper()}")
            return 0
        print(f"$SECRET:{key.upper()} no existe", file=sys.stderr)
        return 1
    rows = secrets_manager.list_secrets()
    if not rows:
        print("vault vacío. añade uno: sayri vault add CLAVE valor")
        return 0
    for s in rows:
        print(f"{s['key']}  {s['masked']}  {s.get('description', '')}  (${s['key']})")
    print()
    print("handle: $SECRET:CLAVE — nunca se envía en texto plano al LLM.")
    return 0


def cmd_config(pairs: dict, rest: list[str]) -> int:
    from sayri import config as cfg

    action, rest = _pick_action(rest, {
        "set": {"set", "pon", "asignar", "cambiar", "write"},
        "get": {"get", "leer", "ver", "read", "muestra"},
        "list": {"list", "ls", "mostrar", "all"},
    }, "list")
    if action == "get":
        key = pairs.get("key") or (rest[0] if rest else "")
        if not key:
            print("¿clave?  ej: sayri config get stt.mode", file=sys.stderr)
            return 1
        group, _, k = key.partition(".")
        try:
            val = cfg.config.get(group, k or group)
        except Exception:
            print(f"unknown key: {key}", file=sys.stderr)
            return 1
        print(f"{group}.{k} = {val}")
        return 0
    if action == "set":
        key = pairs.get("key") or (rest[0] if rest else "")
        raw = pairs.get("value") if "value" in pairs else (" ".join(rest[1:]) if rest else "")
        if not key or not raw:
            print("¿clave y valor?  ej: sayri config set stt.mode manual", file=sys.stderr)
            return 1
        val: Any = raw
        if raw.lower() in ("true", "false"):
            val = raw.lower() == "true"
        else:
            try:
                val = int(raw)
            except ValueError:
                try:
                    val = float(raw)
                except ValueError:
                    pass
        group, _, k = key.partition(".")
        _write_config(group, k or group, val)
        cfg.config.load()
        if group == "ui" and (k or group) in ("autostart", "autostart_mode"):
            from .autostart import apply_autostart
            try:
                apply_autostart(cfg.config)
            except Exception as exc:  # noqa: BLE001
                print(f"autostart update error: {exc}", file=sys.stderr)
        print(f"{group}.{k} = {cfg.config.get(group, k or group)}")
        return 0
    for g, section in cfg.config.get_all().items() if hasattr(cfg.config, "get_all") else cfg.DEFAULTS.items():
        keys = section.keys() if isinstance(section, dict) else []
        for k in keys:
            try:
                val = cfg.config.get(g, k)
            except Exception:
                continue
            print(f"{g}.{k} = {val}")
    return 0


def cmd_routines(pairs: dict, rest: list[str]) -> int:
    from sayri.domain.cron_scheduler import CronScheduler, Routine

    action, rest = _pick_action(rest, {
        "create": {"create", "add", "new", "crear", "nuevo", "añadir", "agregar", "setup"},
        "edit": {"edit", "editar", "update", "modificar"},
        "toggle": {"toggle", "activar", "desactivar", "on", "off", "enable", "disable", "switch"},
        "run": {"run", "ejecutar", "lanzar", "now", "corre", "execute"},
        "delete": {"delete", "del", "rm", "remove", "borrar", "eliminar", "quitar"},
        "show": {"show", "ver", "info", "details", "detalles"},
        "list": {"list", "ls", "mostrar", "all"},
    }, "list")
    cs = CronScheduler()
    if action == "delete":
        rid = rest[0] if rest else pairs.get("id", "")
        if not rid:
            print("¿qué rutina?", file=sys.stderr)
            return 1
        cs.delete_routine(rid)
        print(f"deleted routine: {rid}")
        return 0
    if action == "toggle":
        rid = rest[0] if rest else pairs.get("id", "")
        if not rid:
            print("¿qué rutina?", file=sys.stderr)
            return 1
        r = next((x for x in cs.list_routines() if x.id == rid), None)
        if not r:
            print(f"routine not found: {rid}", file=sys.stderr)
            return 1
        wanted = pairs.get("value")
        if wanted is None:
            rest_joined = " ".join(rest)
            if any(w in rest_joined for w in ("off", "no", "apagar", "disable", "quitar")):
                wanted = "off"
            elif any(w in rest_joined for w in ("on", "yes", "si", "activate", "activar", "enable")):
                wanted = "on"
        enabled = not r.enabled if wanted is None else (wanted.lower() in ("1", "yes", "true", "on", "si", "activate", "activar", "enable"))
        cs.toggle_routine(rid, enabled)
        print(f"routine {rid} → {'on' if enabled else 'off'}")
        return 0
    if action == "run":
        rid = rest[0] if rest else pairs.get("id", "")
        if not rid:
            print("¿qué rutina?", file=sys.stderr)
            return 1
        if not daemon_is_running():
            print("daemon not running; start it: sayri daemon", file=sys.stderr)
            return 1
        c = _client()
        try:
            r = c.request("routines_run", {"routine_id": rid})
            print(f"scheduled: {r.get('name', rid)}")
        finally:
            c.close()
        return 0
    if action == "show":
        rid = rest[0] if rest else pairs.get("id", "")
        if not rid:
            print("¿qué rutina?", file=sys.stderr)
            return 1
        r = next((x for x in cs.list_routines() if x.id == rid), None)
        if not r:
            print(f"routine not found: {rid}", file=sys.stderr)
            return 1
        print(f"{r.id}  {r.name}  ({r.trigger}:{r.time_spec})  [{('on' if r.enabled else 'off')}]  agent={r.agent_id}")
        print(f"  prompt: {r.prompt}")
        return 0
    if action in ("create", "edit"):
        rid = rest[0] if rest else pairs.get("id", "")
        existing = next((x for x in cs.list_routines() if x.id == rid), None) if action == "edit" else None
        if action == "edit" and not existing:
            print(f"routine not found: {rid}", file=sys.stderr)
            return 1
        name = pairs.get("name") or (existing.name if existing else (rest[0] if rest else ""))
        if not name:
            print("¿nombre?  ej: sayri routines add Despertador at 08:30 prompt …", file=sys.stderr)
            return 1
        trigger = getattr(existing, "trigger", None) if existing else None
        time_spec = getattr(existing, "time_spec", "") if existing else ""
        if pairs.get("at"):
            trigger, time_spec = "daily_at", pairs["at"]
        elif pairs.get("every") or pairs.get("every_hours"):
            trigger, time_spec = "hourly", pairs.get("every") or pairs.get("every_hours")
        else:
            joined = " ".join(rest).lower()
            if any(w in ("on-login", "on_login", "login", "al_iniciar", "inicio") for w in joined.split()):
                trigger, time_spec = "on_login", getattr(existing, "time_spec", "09:00") if existing else "09:00"
        if not trigger:
            trigger, time_spec = "daily_at", "09:00" if not time_spec else time_spec
        rid_final = existing.id if existing else (re.sub(r"[^\w\-]", "_", name.lower())[:24] + f"-{int(time.time())}")
        routine = Routine(
            id=rid_final,
            name=name,
            description=pairs.get("desc") or pairs.get("description") or (existing.description if existing else f"Routine created via CLI"),
            trigger=trigger,
            time_spec=time_spec,
            prompt=pairs.get("prompt") or (existing.prompt if existing else ""),
            agent_id=pairs.get("agent") or (existing.agent_id if existing else "default"),
            speak_tts=(pairs.get("tts", "yes").lower() in ("1", "yes", "true", "on", "si") if pairs.get("tts") else (existing.speak_tts if existing else True)),
            notify_desktop=(pairs.get("notify", "yes").lower() in ("1", "yes", "true", "on", "si") if pairs.get("notify") else (existing.notify_desktop if existing else True)),
            enabled=(pairs.get("enabled", "yes").lower() in ("1", "yes", "true", "on", "si") if pairs.get("enabled") else (existing.enabled if existing else True)),
            last_run=getattr(existing, "last_run", 0.0),
            created_at=getattr(existing, "created_at", time.time()),
        )
        cs.save_routine(routine)
        print(f"{'updated' if action == 'edit' else 'created'} routine '{routine.name}' ({routine.trigger}:{routine.time_spec}) id={routine.id}")
        if not routine.prompt:
            print("  (sin prompt — edítala con: sayri routines edit <id> prompt 'tu instrucción')")
        return 0
    rows = cs.list_routines()
    for r in rows:
        enabled = "on" if r.enabled else "off"
        print(f"{r.id}  {r.name}  ({r.trigger}:{r.time_spec})  [{enabled}]  agent={r.agent_id}")
    return 0


def cmd_downloads(pairs: dict, rest: list[str]) -> int:
    from sayri import downloads, paths

    action, clean = _pick_action(rest, {
        "model": {"model", "modelo", "whisper_model", "stt"},
        "voice": {"voice", "voz", "piper_voice", "tts"},
        "whisper": {"whisper", "whisper-cli", "whisper_cli", "cli"},
        "piper": {"piper", "piper-bin", "piper_bin"},
        "status": {"status", "estado"},
    }, "status")
    if action == "status":
        print("Binarios:")
        print(f"  whisper-cli: {'ok' if shutil.which('whisper-cli') or (Path(paths.bin_dir())/'whisper-cli').is_file() else 'missing'}")
        print(f"  piper:       {'ok' if shutil.which('piper') or (Path(paths.bin_dir())/'piper').is_file() else 'missing'}")
        print("Modelos (stt):")
        base_sizes = tuple(k for k in downloads.WHISPER_MODELS if not k.endswith(".en"))
        for size in base_sizes:
            if downloads.has_whisper_model(size, "en") or downloads.has_whisper_model(size, ""):
                print(f"  {size}: descargado ✓")
        print("Voces (tts):")
        for lang, entries in downloads.PIPER_VOICES.items():
            for entry in entries[:2]:
                if downloads.has_piper_voice(lang, entry["voice"], entry["quality"]):
                    print(f"  {lang}/{entry['voice']}: ok ✓")
        print(f"catálogo: modelos {', '.join(base_sizes)} | idiomas Piper {', '.join(downloads.PIPER_VOICES)}")
        return 0
    if action == "model":
        size = pairs.get("size") or (clean[0] if clean else pairs.get("model", ""))
        lang = pairs.get("language") or (clean[1] if len(clean) > 1 else "") or "en"
        if not size:
            print("¿tamaño?  ej: sayri downloads model tiny en", file=sys.stderr)
            return 1
        size = size.lower().replace(" ", "_")
        if size == "large":
            size = "large-v3"
        valid = tuple(k for k in downloads.WHISPER_MODELS if not k.endswith(".en"))
        if size not in valid:
            print(f"tamaño no válido: {size} ({'|'.join(valid)})", file=sys.stderr)
            return 1
        print(f"descargando modelo whisper {size}/{lang}…")
        ok = downloads.download_whisper_model(size, lang, progress=lambda f: print(f"  {f*100:.0f}%", end="\r"))
        if not ok:
            print("\ndescarga falló", file=sys.stderr)
            return 1
        _write_config("stt", "model_size", size)
        _write_config("stt", "language", lang)
        print(f" ✓ modelo {size} listo y configurado (stt.model_size={size})")
        return 0
    if action == "voice":
        lang = pairs.get("language") or (clean[0] if clean else "")
        voice = pairs.get("voice") or (clean[1] if len(clean) > 1 else "")
        quality = pairs.get("quality") or (clean[2] if len(clean) > 2 else "medium")
        if not lang or not voice:
            print("¿idioma y voz?  ej: sayri downloads voice es_ES sharvard [quality]", file=sys.stderr)
            print("idiomas: " + ", ".join(downloads.PIPER_VOICES))
            return 1
        resolved = _resolve_piper_lang(lang, downloads.PIPER_VOICES)
        if not resolved:
            print(f"idioma no válido: {lang}  ({', '.join(downloads.PIPER_VOICES)})", file=sys.stderr)
            return 1
        entries = {e["voice"]: e for e in downloads.PIPER_VOICES[resolved]}
        if voice not in entries:
            print(f"voz no válida para {resolved}: {voice}  ({', '.join(entries)})", file=sys.stderr)
            return 1
        qualities = {"low", "x_low", "medium", "high", "x_high"}
        if quality not in qualities:
            print(f"calidad no válida: {quality}  ({'|'.join(sorted(qualities))})", file=sys.stderr)
            return 1
        print(f"descargando voz piper {resolved}/{voice}/{quality}…")
        ok = downloads.download_piper_voice(resolved, voice, quality, progress=lambda f: print(f"  {f*100:.0f}%", end="\r"))
        if not ok:
            print("\ndescarga falló", file=sys.stderr)
            return 1
        _write_config("tts", "language", resolved)
        _write_config("tts", "voice", voice)
        _write_config("tts", "quality", quality)
        _write_config("tts", "enabled", True)
        print(f" ✓ voz {resolved}/{voice} lista y configurada (tts.voice={voice})")
        return 0
    if action == "whisper":
        print("instalando binario whisper-cli…")
        path = downloads.install_whisper_cli(progress=lambda f: print(f"  {f*100:.0f}%", end="\r"))
        print(f" ✓ whisper-cli en {path}")
        return 0
    if action == "piper":
        print("instalando binario piper…")
        path = downloads.install_piper(progress=lambda f: print(f"  {f*100:.0f}%", end="\r"))
        print(f" ✓ piper en {path}")
        return 0
    return 0


cmd_downloads_area = None  # (removed; handled by _pick_action)


def _resolve_piper_lang(lang: str, catalog: dict[str, Any]) -> Optional[str]:
    clean = lang.strip().lower().replace("-", "_")
    if clean in catalog:
        return clean
    for key in catalog:
        if key.lower() == clean or key.lower().startswith(clean + "_"):
            return key
    for key in catalog:
        if clean.startswith(key.lower().split("_")[0]):
            return key
    return None


def _write_config(group: str, key: str, value: Any) -> None:
    """Persist config through the daemon when possible, else directly."""
    try:
        c = _client()
        try:
            c.request("config_set", {"key": f"{group}.{key}", "value": value}, timeout=10)
            return
        finally:
            c.close()
    except Exception:
        pass
    from sayri import config as cfg
    cfg.config.set(group, key, value, persist=True)


def cmd_orb(pairs: dict, rest: list[str]) -> int:
    """Launch/stop the Sayri orb UI plugin."""
    action, _ = _pick_action(rest, {
        "stop": {"stop", "parar", "apagar", "quitar"},
        "status": {"status", "estado", "check"},
        "start": {"start", "launch", "arranca", "iniciar", "abrir", "open"},
    }, "start")
    orb_gateway = Path(__file__).resolve().parent.parent.parent.parent.parent.parent.parent.parent / "packages" / "plugins" / "sayri-orb" / "gateway.py"
    if not orb_gateway.is_file():
        for candidate in (
            Path.home() / ".config" / "sayri" / "plugins" / "sayri-orb" / "gateway.py",
            Path("/usr/share/sayri/plugins") / "sayri-orb" / "gateway.py",
        ):
            if candidate.is_file():
                orb_gateway = candidate
                break
    state_dir = Path(os.environ.get("SAYRI_STATE_DIR") or str(Path.home() / ".local" / "share" / "sayri"))
    pid_file = state_dir / "orb.pid"

    if action == "stop":
        if pid_file.is_file():
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, signal.SIGTERM)
                print(f"stopped orb (pid {pid})")
            except (OSError, ValueError):
                print("orb was not running")
            finally:
                try:
                    pid_file.unlink(missing_ok=True)
                except Exception:
                    pass
        else:
            print("orb is not running")
        return 0

    if action == "status":
        if pid_file.is_file():
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, 0)
                print(f"orb running (pid {pid})")
            except (OSError, ValueError):
                print("orb pid file exists but process is dead")
        else:
            print("orb is not running")
        return 0

    # start
    if not orb_gateway.is_file():
        print(f"orb plugin not found at {orb_gateway}", file=sys.stderr)
        return 1
    if not daemon_is_running():
        print("sayri daemon is not running; start it first: sayri daemon", file=sys.stderr)
        return 1
    proc = subprocess.Popen(
        [sys.executable, str(orb_gateway)],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print(f"orb started (pid {proc.pid})")
    return 0


def _is_ui_plugin(data: dict) -> bool:
    return isinstance(data.get("ui"), dict)


def _ui_plugin_gateway(ui_id: str) -> Optional[Path]:
    """Find a UI-type plugin gateway by id (user config dir first, then /usr)."""
    roots = [
        Path.home() / ".config" / "sayri" / "plugins",
        Path("/usr/share/sayri/plugins"),
    ]
    for root in roots:
        if not root.is_dir():
            continue
        for candidate in root.glob("*/manifest.json"):
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if not _is_ui_plugin(data) or not data.get("entrypoint"):
                continue
            if data.get("id") == ui_id or data.get("id") == f"sayri-{ui_id}" or data.get("name", "").lower() == ui_id.lower():
                return candidate.parent / data["entrypoint"]
    return None


def cmd_ui(pairs: dict, rest: list[str]) -> int:
    """Launch/stop/query the configured default UI (ui.default_ui)."""
    from sayri import config as cfg

    action, rest = _pick_action(rest, {
        "start": {"start", "launch", "abrir", "open", "iniciar", "arranca", "show", "mostrar"},
        "stop": {"stop", "parar", "cerrar", "quit", "quitar", "close"},
        "status": {"status", "estado", "check", "running"},
    }, "status")
    ui_id = pairs.get("ui") or pairs.get("id") or (rest[0] if rest else "")
    if not ui_id:
        try:
            ui_id = (cfg.config.get_string("ui", "default_ui") or "orb").strip()
        except Exception:  # noqa: BLE001
            ui_id = "orb"
    if action == "status":
        print(f"default_ui = {ui_id}  daemon={'running' if daemon_is_running() else 'stopped'}")
        return 0
    if ui_id in ("orb", ""):
        if action == "stop":
            subprocess.Popen(["pkill", "-f", "python3 -m sayri --launch-ui"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print("orb ui: stop requested")
            return 0
        _ensure_daemon()
        proc = subprocess.Popen(
            [sys.executable, "-m", "sayri", "--launch-ui"],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"orb ui launching (pid {proc.pid}, daemon running)")
        return 0
    gateway = _ui_plugin_gateway(ui_id)
    if not gateway or not gateway.is_file():
        print(f"UI plugin not found: {ui_id}  (configured in ui.default_ui)", file=sys.stderr)
        return 1
    if action == "stop":
        pid_file = Path(os.environ.get("SAYRI_STATE_DIR") or str(Path.home() / ".local" / "share" / "sayri")) / f"{ui_id}.pid"
        if pid_file.is_file():
            try:
                os.kill(int(pid_file.read_text().strip()), signal.SIGTERM)
                print(f"stopped ui {ui_id}")
            except (OSError, ValueError):
                print(f"ui {ui_id} was not running")
            finally:
                try:
                    pid_file.unlink(missing_ok=True)
                except Exception:  # noqa: BLE001
                    pass
        return 0
    _ensure_daemon()
    proc = subprocess.Popen(
        [sys.executable, str(gateway)],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print(f"ui plugin {ui_id} started (pid {proc.pid})")
    return 0


def cmd_killall(pairs: dict, rest: list[str]) -> int:
    for pat in ("gateway.py", "sayri.indicator", "python3 -m sayri"):
        subprocess.Popen(["pkill", "-9", "-f", pat], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("terminated all Sayri processes ✓")
    return 0


# ------------------------------------------------------------------ registry

_COMMANDS: list[_Cmd] = [
    _Cmd("help", 100, {"help", "ayuda", "ayudame", "aiuda", "info", "manual", "comandos", "commands", "-h", "--help"},
         cmd_help, "Qué puedo hacer."),
    _Cmd("vault", 90, {"vault", "boveda", "secrets", "secretos", "keychain"},
         cmd_vault, "Secretos cifrados (variables $SECRET:CLAVE).",
         {
             "add": {"add", "set", "guardar", "store", "nuevo", "crear", "añadir", "agregar", "save"},
             "get": {"get", "show", "leer", "print"},
             "delete": {"delete", "del", "rm", "remove", "borrar", "eliminar", "quitar"},
             "list": {"list", "ls", "mostrar"},
         }, "list"),
    _Cmd("agents", 90, {"agents", "agent", "agentes", "agente", "subagent", "subagentes", "subagente"},
         cmd_agents, "Sub-agentes: list|create|edit|use|delete|show.",
         {
             "create": {"create", "new", "crear", "add", "añadir", "agregar", "make", "setup"},
             "edit": {"edit", "editar", "update", "modificar", "configure"},
             "use": {"use", "select", "switch", "usar", "activar", "choose"},
             "delete": {"delete", "del", "rm", "remove", "borrar", "eliminar"},
             "show": {"show", "get", "view", "info"},
             "list": {"list", "ls", "mostrar"},
         }, "list"),
    _Cmd("skills", 90, {"skills", "skill", "habilidades", "habilidad"},
         cmd_skills, "Skills: list|search|install|read|remove.",
         {
             "search": {"search", "buscar", "find"},
             "install": {"install", "instalar", "instala", "add"},
             "read": {"read", "show", "view", "info", "details"},
             "remove": {"remove", "uninstall", "uninstalar", "delete", "del", "rm", "borrar", "eliminar"},
             "list": {"list", "ls", "mostrar"},
         }, "list"),
    _Cmd("plugins", 90, {"plugins", "plugin", "extensiones", "extension"},
         cmd_plugins, "Plugins: list|config (nivel de sandbox)|show.",
         {
             "config": {"config", "configure", "edit", "editar", "configurar", "allow", "permisos", "set"},
             "show": {"show", "view", "info", "ver", "details"},
             "list": {"list", "ls", "mostrar"},
         }, "list"),
    _Cmd("gateway", 90, {"gateway", "gateways", "puerta", "puertas", "gate", "canales", "canal", "channel", "channels"},
         cmd_gateway, "Gateways de canal: list|start|stop|create|edit|delete|pin|open.",
         {
             "start": {"start", "iniciar", "arranca", "launch", "up", "encender"},
             "stop": {"stop", "parar", "apagar", "down"},
             "delete": {"delete", "del", "rm", "remove", "borrar", "eliminar", "quitar"},
             "pin": {"pin", "pair", "pairing", "codigo", "otp", "show_pin"},
             "open": {"open", "abrir", "channel", "enlace", "link"},
             "edit": {"edit", "editar", "update", "modificar", "configure"},
             "create": {"create", "add", "new", "crear", "nuevo", "añadir", "agregar", "setup"},
             "list": {"list", "ls", "mostrar"},
         }, "list"),
    _Cmd("routines", 90, {"routines", "routine", "rutinas", "rutina", "cron", "automatizaciones"},
         cmd_routines, "Rutinas/cron: list|create|edit|toggle|run|delete|show.",
         {
             "create": {"create", "add", "new", "crear", "nuevo", "añadir", "agregar", "setup"},
             "edit": {"edit", "editar", "update", "modificar"},
             "toggle": {"toggle", "activar", "desactivar", "enable", "disable"},
             "run": {"run", "ejecutar", "lanzar", "now", "execute"},
             "delete": {"delete", "del", "rm", "remove", "borrar", "eliminar"},
             "show": {"show", "view", "info"},
             "list": {"list", "ls", "mostrar"},
         }, "list"),
    _Cmd("sessions", 90, {"sessions", "session", "conversation", "conversations", "conversacion", "conversaciones", "hilos", "threads", "thread"},
         cmd_sessions, "Conversaciones: list|switch|rename|delete|show|search.",
         {
             "switch": {"switch", "resume", "open", "abrir", "activar", "use"},
             "rename": {"rename", "renombrar", "title", "titulo"},
             "delete": {"delete", "del", "rm", "remove", "borrar", "eliminar"},
             "show": {"show", "view", "ver", "details"},
             "search": {"search", "buscar", "find"},
             "list": {"list", "ls", "mostrar", "all"},
         }, "list"),
    _Cmd("downloads", 90, {"downloads", "download", "descargas", "descargar", "install", "instalar", "model", "modelo", "voice", "voz", "voces", "piper", "whisper", "whisper-cli"},
         cmd_downloads, "Descargas: status|model|voice|whisper|piper.",
         {
             "model": {"model", "modelo", "whisper_model", "stt"},
             "voice": {"voice", "voz", "piper_voice", "tts"},
             "whisper": {"whisper", "whisper-cli", "whisper_cli", "cli"},
             "piper": {"piper", "piper-bin", "piper_bin"},
             "status": {"status", "estado"},
         }, "status"),
    _Cmd("config", 90, {"config", "settings", "ajustes", "configuracion", "preferencias"},
         cmd_config, "Configuración: list|get|set.",
         {
             "set": {"set", "pon", "asignar", "cambiar", "write"},
             "get": {"get", "leer", "ver"},
             "list": {"list", "ls", "mostrar"},
         }, "list"),
    _Cmd("orb", 95, {"orb", "burbuja", "esfera", "orbe"},
         cmd_orb, "UI plugin (orb GTK4): start|stop|status.", None, "start"),
    _Cmd("ui", 96, {"ui", "interface", "interfaz", "vista", "launcher"},
         cmd_ui, "Lanza la UI predeterminada (ui.default_ui): start|stop|status."),
    _Cmd("daemon", 80, {"daemon", "demonio", "serve", "servidor", "server", "background", "fondo"},
         cmd_daemon, "Daemon headless: start|stop|status|restart.", None, "start"),
    _Cmd("killall", 75, {"killall", "terminate", "mata", "matar", "salir_todo"},
         cmd_killall, "Termina todos los procesos Sayri."),
    _Cmd("status", 70, {"status", "estado", "status_info"},
         cmd_status, "Estado del core/proveedor/sesión."),
    _Cmd("version", 60, {"version", "versión", "versionar?", "v"},
         cmd_version, "Versión instalada."),
    _Cmd("new", 55, {"new", "nueva", "nuevo", "reset", "fresca", "fresh"},
         cmd_new, "Nueva conversación.", None, "start"),
    _Cmd("talk", 45, {"talk", "say", "dime", "di", "hablar", "habla", "text", "mensaje", "manda", "speak"},
         cmd_talk, "Enviar texto (async, responde con TTS)."),
    _Cmd("ask", 45, {"ask", "pregunta", "preguntar", "question", "consulta", "quiero_saber"},
         cmd_ask, "Preguntar y esperar la respuesta completa."),
    _Cmd("listen", 45, {"listen", "escuchar", "escucha", "mic", "microfono", "transcribe", "transcribir"},
         cmd_listen, "Transcribir la siguiente frase."),
    _Cmd("stop", 40, {"stop", "silencio", "calla", "stfu", "para_escuchar", "parar"},
         cmd_stop, "Dejar de escuchar."),
    _Cmd("interrupt", 40, {"interrupt", "cancel", "cancela", "alto", "basta", "corta"},
         cmd_interrupt, "Cortar TTS/generación."),
    _Cmd("toggle", 40, {"toggle", "alternar", "mirco", "micro", "switch_mic", "click"},
         cmd_toggle, "Activa/desactiva el micrófono."),
]


def main(argv: Optional[list[str]] = None) -> int:
    raw = list(argv) if argv is not None else sys.argv[1:]
    tokens = _normalize(raw)
    if not tokens:
        return cmd_help({}, [])
    pairs, rest = _extract_pairs(tokens)
    matched = _match_command(rest)
    if matched is None:
        print(f"no entiendo: {' '.join(raw)}  — prueba 'sayri help'", file=sys.stderr)
        return 2
    cmd, meta, cleaned = matched
    try:
        return cmd.handler(pairs, cleaned)
    except SayriError as exc:
        print(exc, file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())