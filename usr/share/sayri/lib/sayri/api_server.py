"""Sayri Remote Control and OpenAI-Compatible HTTP/REST Server.

Provides a zero-dependency, asynchronous multi-threaded API server exposing:
1. Standard OpenAI v1 endpoints (/v1/models, /v1/chat/completions with SSE streaming)
   for seamless integration with mobile apps, home automation, and third-party AI clients.
2. Sayri native REST endpoints (/api/v1/status, /api/v1/query, /api/v1/memory, /api/v1/mobile)
   for complete remote system management and Android Accessibility perception.
"""

from __future__ import annotations

import base64
import datetime
import json
import os
import random
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from sayri import config as sayri_config
from sayri import paths, sysinfo
from sayri.domain.agent_creator import AgentCreator
from sayri.domain.models import SandboxLevel

_SERVER_INSTANCE: Optional["SayriApiServer"] = None


class PairingManager:
    """Manages 6-digit PIN desktop pairing and authorized device tokens."""
    _current_pin: str = ""
    _pin_created_at: float = 0.0

    @classmethod
    def get_paired_devices_file(cls) -> Path:
        d = Path(paths.config_dir())
        d.mkdir(parents=True, exist_ok=True)
        return d / "paired_devices.json"

    @classmethod
    def load_paired_devices(cls) -> List[Dict[str, Any]]:
        f = cls.get_paired_devices_file()
        if f.is_file():
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                return data.get("devices", [])
            except Exception:
                pass
        return []

    @classmethod
    def save_paired_devices(cls, devices: List[Dict[str, Any]]) -> None:
        f = cls.get_paired_devices_file()
        f.write_text(json.dumps({"devices": devices}, indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def get_pin_file(cls) -> Path:
        d = Path(paths.config_dir())
        d.mkdir(parents=True, exist_ok=True)
        return d / "pairing_pin.json"

    @classmethod
    def get_or_create_pin(cls) -> str:
        f = cls.get_pin_file()
        now = time.time()
        if f.is_file():
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                pin = str(data.get("pin", "")).strip()
                created = float(data.get("created_at", 0))
                if pin and (now - created) < 86400:
                    return pin
            except Exception:
                pass
        return cls.regenerate_pin()

    @classmethod
    def regenerate_pin(cls) -> str:
        pin = f"{random.randint(100000, 999999):06d}"
        now = time.time()
        f = cls.get_pin_file()
        f.write_text(json.dumps({"pin": pin, "created_at": now}), encoding="utf-8")
        return pin

    @classmethod
    def pair_device(cls, pin: str, device_name: str, device_id: str = "") -> Optional[str]:
        current = cls.get_or_create_pin()
        if not pin or pin.strip() != current:
            return None
        token = f"sayri_tok_{secrets.token_hex(20)}"
        devices = cls.load_paired_devices()
        devices = [d for d in devices if d.get("device_id") != device_id or not device_id]
        devices.append({
            "token": token,
            "name": device_name or "Android Device",
            "device_id": device_id,
            "paired_at": datetime.datetime.now().isoformat(),
        })
        cls.save_paired_devices(devices)
        return token

    @classmethod
    def is_token_valid(cls, token: str) -> bool:
        if not token:
            return False
        devices = cls.load_paired_devices()
        return any(d.get("token") == token for d in devices)

    @classmethod
    def revoke_device(cls, token: str) -> None:
        devices = cls.load_paired_devices()
        devices = [d for d in devices if d.get("token") != token]
        cls.save_paired_devices(devices)


class SayriApiHandler(BaseHTTPRequestHandler):
    """Handles incoming HTTP requests for OpenAI API and Sayri Native Endpoints."""

    server_core: Any = None
    server_cfg: Any = None

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress standard BaseHTTPRequestHandler stderr noise."""
        pass

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With, X-Session-ID, X-Device-Name")

    def do_OPTIONS(self) -> None:
        """Handle CORS pre-flight requests."""
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def _is_authorized(self) -> bool:
        cfg = self.server_cfg or getattr(sayri_config, "config", None)
        # If API is disabled in config, reject requests
        if cfg and not cfg.get_boolean("api", "enabled", True):
            return False

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        # Status and pairing endpoints are public for discovery & pairing
        if path in ("/api/v1/status", "/api/v1/pair"):
            return True

        expected_key = cfg.get_string("api", "api_key", "").strip() if cfg else ""

        # Check Authorization: Bearer <key>
        auth_hdr = self.headers.get("Authorization", "")
        token = ""
        if auth_hdr.startswith("Bearer "):
            token = auth_hdr.split("Bearer ", 1)[1].strip()

        # Check query string parameter: ?key=<key>
        if not token:
            qs = urllib.parse.parse_qs(parsed.query)
            if qs.get("key"):
                token = qs["key"][0]

        # 1. Matches static api_key if configured
        if expected_key and token == expected_key:
            return True

        # 2. Matches paired device bearer token
        if PairingManager.is_token_valid(token):
            return True

        # 3. If pairing is disabled and no static key is configured
        require_pairing = cfg.get_boolean("api", "require_pairing", True) if cfg else True
        if not require_pairing and not expected_key:
            return True

        return False

    def _send_json(self, status_code: int, data: Any) -> None:
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(payload)

    def _send_error(self, status_code: int, message: str) -> None:
        self._send_json(status_code, {"error": {"message": message, "code": status_code}})

    def _read_json_body(self) -> Optional[Dict[str, Any]]:
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length <= 0:
                return {}
            body = self.rfile.read(content_length).decode("utf-8")
            return json.loads(body)
        except Exception:
            return None

    # ------------------------------------------------------------- GET
    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        if not self._is_authorized():
            self._send_error(401, "Unauthorized: Invalid or missing API Key.")
            return

        # 1. OpenAI Models Endpoint: /v1/models
        if path in ("/v1/models", "/api/v1/models"):
            self._handle_get_models()
            return

        # 2. Sayri Status Endpoint: /api/v1/status
        if path == "/api/v1/status":
            self._handle_get_status()
            return

        # 3. Sayri Agents: /api/v1/agents
        if path == "/api/v1/agents":
            self._handle_get_agents()
            return

        # 4. Sayri Memory Ideas: /api/v1/memory/ideas
        if path == "/api/v1/memory/ideas":
            self._handle_get_ideas()
            return

        # 5. Sayri Memory Tomorrow: /api/v1/memory/tomorrow
        if path == "/api/v1/memory/tomorrow":
            self._handle_get_tomorrow()
            return

        # 6. Audio Speech synthesis: /v1/audio/speech
        if path == "/v1/audio/speech":
            params = urllib.parse.parse_qs(parsed.query)
            text = params.get("text", [""])[0] or params.get("input", [""])[0]
            self._handle_audio_speech(text)
            return

        # 7. Sessions List: /api/v1/sessions
        if path == "/api/v1/sessions":
            self._handle_get_sessions()
            return

        # 8. Session Detail: /api/v1/sessions/<id>
        if path.startswith("/api/v1/sessions/"):
            session_id = path[len("/api/v1/sessions/"):]
            self._handle_get_session_by_id(session_id)
            return

        self._send_error(404, f"Endpoint not found: {path}")

    # ------------------------------------------------------------- DELETE
    def do_DELETE(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        if not self._is_authorized():
            self._send_error(401, "Unauthorized: Invalid or missing API Key.")
            return

        if path.startswith("/api/v1/sessions/"):
            session_id = path[len("/api/v1/sessions/"):]
            self._handle_delete_session(session_id)
            return

        self._send_error(404, f"Endpoint not found: {path}")

    # ------------------------------------------------------------- POST
    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        if not self._is_authorized():
            self._send_error(401, "Unauthorized: Invalid or missing API Key.")
            return

        # Audio Transcription (STT with Whisper): /v1/audio/transcriptions
        if path == "/v1/audio/transcriptions":
            content_type = self.headers.get("Content-Type", "")
            if "application/json" in content_type:
                body = self._read_json_body()
                self._handle_audio_transcription(b"", is_json=True, json_obj=body)
            else:
                try:
                    content_length = int(self.headers.get("Content-Length", 0))
                    raw_audio = self.rfile.read(content_length) if content_length > 0 else b""
                    self._handle_audio_transcription(raw_audio, is_json=False)
                except Exception as e:
                    self._send_error(400, f"Failed reading audio stream: {e}")
            return

        body = self._read_json_body()
        if body is None:
            self._send_error(400, "Invalid JSON body.")
            return

        # 1. OpenAI Chat Completions: /v1/chat/completions
        if path == "/v1/chat/completions":
            self._handle_chat_completions(body)
            return

        # 1B. Create Session: /api/v1/sessions
        if path == "/api/v1/sessions":
            self._handle_create_session(body)
            return

        # 2. Native Query Endpoint: /api/v1/query
        if path == "/api/v1/query":
            self._handle_native_query(body)
            return

        # 3. Audio Speech synthesis: /v1/audio/speech
        if path == "/v1/audio/speech":
            text = str(body.get("input") or body.get("text") or "")
            self._handle_audio_speech(text)
            return

        # 4. Native Memory Ideas Append: /api/v1/memory/ideas
        if path == "/api/v1/memory/ideas":
            self._handle_post_ideas(body)
            return

        # 5. Native Memory Tomorrow Append: /api/v1/memory/tomorrow
        if path == "/api/v1/memory/tomorrow":
            self._handle_post_tomorrow(body)
            return

        # 6. Mobile Accessibility Action: /api/v1/mobile/action
        if path == "/api/v1/mobile/action":
            self._handle_mobile_action(body)
            return

        # 7. Device PIN Pairing: /api/v1/pair
        if path == "/api/v1/pair":
            self._handle_post_pair(body)
            return

        self._send_error(404, f"Endpoint not found: {path}")

    # ------------------------------------------------------------- Handlers
    def _handle_get_models(self) -> None:
        agents = AgentCreator.list_agents()
        model_list = [
            {"id": "sayri", "object": "model", "created": int(time.time()), "owned_by": "sayri"},
            {"id": "default", "object": "model", "created": int(time.time()), "owned_by": "sayri"},
        ]
        for a in agents:
            if a.id not in ("sayri", "default"):
                model_list.append({
                    "id": a.id,
                    "object": "model",
                    "created": int(getattr(a, "created_at", time.time())),
                    "owned_by": "sayri",
                })
        self._send_json(200, {"object": "list", "data": model_list})

    def _handle_get_status(self) -> None:
        agents = AgentCreator.list_agents()
        from sayri import skills
        installed_skills = skills.list_skills()

        uptime = 0.0
        if hasattr(self.server, "start_time"):
            uptime = time.time() - self.server.start_time

        data = {
            "status": "online",
            "version": "0.1.38",
            "active_agent": "default",
            "agents_count": len(agents),
            "skills_count": len(installed_skills),
            "uptime_seconds": round(uptime, 1),
            "platform": sysinfo.os_name(),
            "pairing_required": True,
        }
        self._send_json(200, data)

    def _handle_post_pair(self, body: Dict[str, Any]) -> None:
        pin = str(body.get("pin") or "").strip()
        device_name = str(body.get("device_name") or "Android Device").strip()
        device_id = str(body.get("device_id") or "").strip()

        token = PairingManager.pair_device(pin, device_name, device_id)
        if not token:
            self._send_error(401, "PIN incorrecto o caducado. Comprueba el código mostrado en Sayri Desktop.")
            return

        self._send_json(200, {
            "ok": True,
            "token": token,
            "device_name": device_name,
            "server_name": "Pulsar OS",
            "message": "Dispositivo emparejado con éxito.",
        })

    def _handle_get_agents(self) -> None:
        agents = AgentCreator.list_agents()
        res = []
        for a in agents:
            res.append({
                "id": a.id,
                "name": a.name,
                "description": a.description,
                "sandbox_level": a.sandbox.level.value,
                "allowed_commands": getattr(a, "allowed_commands", []),
            })
        self._send_json(200, {"agents": res})

    def _handle_get_ideas(self) -> None:
        ideas_file = Path(paths.memory_dir()) / "ideas.md"
        content = ""
        if ideas_file.is_file():
            content = ideas_file.read_text(encoding="utf-8")
        self._send_json(200, {"ideas_markdown": content})

    def _handle_post_ideas(self, body: Dict[str, Any]) -> None:
        idea_text = str(body.get("idea") or body.get("text") or "").strip()
        if not idea_text:
            self._send_error(400, "Field 'idea' cannot be empty.")
            return

        mem_dir = Path(paths.memory_dir())
        mem_dir.mkdir(parents=True, exist_ok=True)
        ideas_file = mem_dir / "ideas.md"
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

        if not ideas_file.is_file():
            ideas_file.write_text("# 💡 Ideas & Thoughts Memory Journal\n\n", encoding="utf-8")

        entry = f"- [ ] **[{now_str}]** {idea_text}\n"
        with open(ideas_file, "a", encoding="utf-8") as f:
            f.write(entry)

        self._send_json(200, {"ok": True, "message": f"Idea recorded: {idea_text}"})

    def _handle_get_tomorrow(self) -> None:
        tom_file = Path(paths.memory_dir()) / "tomorrow.md"
        content = ""
        if tom_file.is_file():
            content = tom_file.read_text(encoding="utf-8")
        self._send_json(200, {"tomorrow_markdown": content})

    def _handle_post_tomorrow(self, body: Dict[str, Any]) -> None:
        task_text = str(body.get("task") or body.get("text") or "").strip()
        if not task_text:
            self._send_error(400, "Field 'task' cannot be empty.")
            return

        mem_dir = Path(paths.memory_dir())
        mem_dir.mkdir(parents=True, exist_ok=True)
        tom_file = mem_dir / "tomorrow.md"
        tomorrow_date = (datetime.date.today() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")

        if not tom_file.is_file():
            tom_file.write_text(f"# 📅 Tomorrow Agenda & Reminders ({tomorrow_date})\n\n", encoding="utf-8")

        entry = f"- [ ] {task_text}\n"
        with open(tom_file, "a", encoding="utf-8") as f:
            f.write(entry)

        self._send_json(200, {"ok": True, "message": f"Task scheduled for tomorrow: {task_text}"})

    def _handle_get_sessions(self) -> None:
        core = self.server_core
        if not core:
            from sayri.core import SayriCore
            core = SayriCore.get_instance()
        if not core:
            self._send_error(503, "Sayri Core is not running.")
            return

        sessions = core.storage.list_sessions(limit=50, include_empty=False)
        data = []
        for s in sessions:
            data.append({
                "id": s.id,
                "title": s.title,
                "agent_id": s.agent_id,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
                "msg_count": len(s.messages),
            })
        self._send_json(200, {"sessions": data})

    def _handle_get_session_by_id(self, session_id: str) -> None:
        core = self.server_core
        if not core:
            from sayri.core import SayriCore
            core = SayriCore.get_instance()
        if not core:
            self._send_error(503, "Sayri Core is not running.")
            return

        session = core.storage.get_session(session_id)
        if not session:
            self._send_error(404, f"Session not found: {session_id}")
            return

        msgs = []
        for m in session.messages:
            msgs.append({
                "role": m.role,
                "content": m.content,
                "timestamp": getattr(m, "timestamp", 0),
            })
        self._send_json(200, {
            "session": {
                "id": session.id,
                "title": session.title,
                "agent_id": session.agent_id,
                "created_at": session.created_at,
                "updated_at": session.updated_at,
                "messages": msgs,
            }
        })

    def _handle_create_session(self, body: Dict[str, Any]) -> None:
        core = self.server_core
        if not core:
            from sayri.core import SayriCore
            core = SayriCore.get_instance()
        if not core:
            self._send_error(503, "Sayri Core is not running.")
            return

        title = str(body.get("title") or "New Mobile Chat").strip()
        custom_id = str(body.get("session_id") or "").strip()
        sid = custom_id if custom_id else f"mobile-{int(time.time()*1000)}"
        if not sid.startswith("mobile-"):
            sid = f"mobile-{sid}"
        sess = core.storage.create_session(agent_id="default", title=title, session_id=sid)
        self._send_json(200, {"session_id": sess.id, "title": sess.title})

    def _handle_delete_session(self, session_id: str) -> None:
        core = self.server_core
        if not core:
            from sayri.core import SayriCore
            core = SayriCore.get_instance()
        if not core:
            self._send_error(503, "Sayri Core is not running.")
            return

        core.storage.delete_session(session_id)
        self._send_json(200, {"ok": True, "deleted": session_id})

    def _handle_chat_completions(self, body: Dict[str, Any]) -> None:
        messages = body.get("messages", [])
        if not messages:
            self._send_error(400, "Field 'messages' is required.")
            return

        # Extract or assign session_id
        session_id = str(body.get("session_id") or "").strip()
        if not session_id:
            session_id = self.headers.get("X-Session-ID", "").strip()
        if not session_id:
            session_id = f"mobile-{int(time.time()*1000)}"
        elif not session_id.startswith("mobile-"):
            session_id = f"mobile-{session_id}"

        def _extract_text_and_images(content: Any) -> Tuple[str, List[str]]:
            if isinstance(content, str):
                return content, []
            if isinstance(content, list):
                parts = []
                imgs = []
                for p in content:
                    if isinstance(p, dict):
                        if p.get("type") == "text":
                            parts.append(p.get("text", ""))
                        elif p.get("type") == "image_url":
                            url = p.get("image_url", {}).get("url", "")
                            if url:
                                imgs.append(url)
                    elif isinstance(p, str):
                        parts.append(p)
                return " ".join(parts), imgs
            return str(content or ""), []

        last_user_msg = ""
        last_images: List[str] = []
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user_msg, last_images = _extract_text_and_images(m.get("content", ""))
                break

        target_model = str(body.get("model", "default")).lower().strip()
        stream = bool(body.get("stream", False))
        created_ts = int(time.time())
        chat_id = session_id

        target_agent = target_model if target_model not in ("sayri", "default") else "default"

        # Determine sandbox level based on gateway_owner_host_mode
        cfg = self.server_cfg or getattr(sayri_config, "config", None)
        allow_owner_host = cfg.get_boolean("security", "gateway_owner_host_mode") if cfg else True
        sandbox_level = "LEVEL_3_HOST_USER" if allow_owner_host else "LEVEL_RESTRICTED_COMMANDS"

        core = self.server_core
        if not core:
            from sayri.core import SayriCore
            core = SayriCore.get_instance()

        if not core:
            self._send_error(503, "Sayri Core is not running or available.")
            return

        final_prompt = last_user_msg
        if last_images:
            img_prefix = f"[Image Attached: {len(last_images)} photo(s) provided for mobile vision analysis]\n"
            final_prompt = f"{img_prefix}{final_prompt}"

        if stream:
            self._handle_chat_completions_stream(core, final_prompt, target_agent, sandbox_level, chat_id, target_model, created_ts)
        else:
            self._handle_chat_completions_sync(core, final_prompt, target_agent, sandbox_level, chat_id, target_model, created_ts)

    def _handle_chat_completions_stream(
        self,
        core: Any,
        prompt: str,
        agent_id: str,
        sandbox_level: str,
        chat_id: str,
        model_name: str,
        created_ts: int,
    ) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self._send_cors_headers()
        self.end_headers()

        def _send_sse(chunk_dict: Any) -> None:
            try:
                line = f"data: {json.dumps(chunk_dict)}\n\n"
                self.wfile.write(line.encode("utf-8"))
                self.wfile.flush()
            except OSError:
                pass

        total_deltas: list[str] = []

        def _on_event(ev: dict) -> None:
            ev_name = ev.get("event")
            if ev_name == "delta":
                delta_content = ev.get("delta", "")
                if delta_content:
                    total_deltas.append(delta_content)
                    chunk = {
                        "id": chat_id,
                        "object": "chat.completion.chunk",
                        "created": created_ts,
                        "model": model_name,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": delta_content},
                                "finish_reason": None,
                            }
                        ],
                    }
                    _send_sse(chunk)
            elif ev_name == "done":
                done_text = ev.get("text", "")
                if not total_deltas and done_text:
                    total_deltas.append(done_text)
                    chunk = {
                        "id": chat_id,
                        "object": "chat.completion.chunk",
                        "created": created_ts,
                        "model": model_name,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": done_text},
                                "finish_reason": None,
                            }
                        ],
                    }
                    _send_sse(chunk)

        core.process_remote_message(
            text=prompt,
            author="mobile_user",
            target_agent_id=agent_id,
            sandbox_level=sandbox_level,
            instance_id="sayri-mobile",
            session_id=chat_id,
            on_event=_on_event,
        )

        # Final stop chunk + [DONE]
        final_chunk = {
            "id": chat_id,
            "object": "chat.completion.chunk",
            "created": created_ts,
            "model": model_name,
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                }
            ],
        }
        _send_sse(final_chunk)
        try:
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except OSError:
            pass
        self.close_connection = True

    def _handle_chat_completions_sync(
        self,
        core: Any,
        prompt: str,
        agent_id: str,
        sandbox_level: str,
        chat_id: str,
        model_name: str,
        created_ts: int,
    ) -> None:
        result = core.process_remote_message(
            text=prompt,
            author="mobile_user",
            target_agent_id=agent_id,
            sandbox_level=sandbox_level,
            instance_id="sayri-mobile",
            session_id=chat_id,
        )

        response = {
            "id": chat_id,
            "object": "chat.completion",
            "created": created_ts,
            "model": model_name,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": result or "",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": len(prompt.split()),
                "completion_tokens": len((result or "").split()),
                "total_tokens": len(prompt.split()) + len((result or "").split()),
            },
        }
        self._send_json(200, response)

    def _handle_audio_speech(self, text: str) -> None:
        clean_text = text.strip()
        if not clean_text:
            self._send_error(400, "Parameter 'text' or 'input' is required.")
            return

        from sayri.tts import TTSEngine
        cfg = self.server_cfg or getattr(sayri_config, "config", None)
        tts = TTSEngine(cfg)

        binary = tts.binary
        voices = tts.voice_files
        if not binary or not voices:
            self._send_error(503, "TTS Engine (Piper) not ready on Sayri host.")
            return

        onnx, json_path = voices
        speed = max(0.1, float(cfg.get_float("tts", "speed"))) if cfg else 1.0
        length_scale = 1.0 / speed
        wav_out = os.path.join(paths.tmp_dir(), f"api-tts-{secrets.token_hex(4)}.wav")

        bin_dir = paths.bin_dir()
        env = dict(os.environ)
        cur_ld = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = f"{bin_dir}:{os.path.dirname(binary)}:{cur_ld}".rstrip(":")
        espeak_data = os.path.join(bin_dir, "espeak-ng-data")

        try:
            cmd = [binary, "--model", onnx, "--output_file", wav_out]
            if json_path:
                cmd += ["--config", json_path]
            if os.path.isdir(espeak_data):
                cmd += ["--espeak_data", espeak_data]
            cmd += ["--length_scale", f"{length_scale:.3f}"]

            proc = subprocess.run(
                cmd,
                input=clean_text.encode("utf-8"),
                capture_output=True,
                env=env,
                timeout=30,
            )
            if proc.returncode != 0 or not os.path.isfile(wav_out):
                err = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
                self._send_error(500, f"Piper synthesis failed: {err[:200]}")
                return

            with open(wav_out, "rb") as f:
                data = f.read()

            try:
                os.remove(wav_out)
            except Exception:
                pass

            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(data)))
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self._send_error(500, f"Error generating speech: {e}")

    def _handle_audio_transcription(self, body_bytes: bytes, is_json: bool = False, json_obj: Optional[Dict[str, Any]] = None) -> None:
        """Transcribes incoming audio using local whisper-cli and model."""
        audio_data = b""
        language = "auto"
        if is_json and json_obj:
            b64 = json_obj.get("audio", "")
            if b64:
                try:
                    audio_data = base64.b64decode(b64)
                except Exception:
                    pass
            language = json_obj.get("language", "auto")
        else:
            audio_data = body_bytes

        if not audio_data:
            self._send_error(400, "No audio data provided.")
            return

        whisper_bin = (
            paths.find_binary("whisper-cli")
            or shutil.which("whisper-cli")
            or os.path.expanduser("~/.local/share/sayri/bin/whisper-cli")
        )
        model_path = os.path.expanduser("~/.local/share/sayri/models/ggml-tiny.bin")
        if not os.path.isfile(model_path):
            from sayri import downloads
            model_path = downloads.whisper_model_path("tiny", "es")

        if not os.path.isfile(whisper_bin) or not os.path.isfile(model_path):
            self._send_error(503, "Whisper binary or model not installed on Sayri server.")
            return

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_f:
            tmp_f.write(audio_data)
            tmp_wav = tmp_f.name

        try:
            cmd = [
                whisper_bin,
                "-m", model_path,
                "-f", tmp_wav,
                "--no-timestamps",
                "-np",
                "-l", language
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            raw_text = res.stdout.strip()
            # Filter hallucinations / ghosts
            clean_text = raw_text
            for ghost in ["[BLANK_AUDIO]", "Subtítulos por", "Subtítulos realizados", "Amara.org", "(música)", "[música]", "you"]:
                clean_text = clean_text.replace(ghost, "").strip()
            self._send_json(200, {"text": clean_text})
        except Exception as e:
            self._send_error(500, f"Error transcribing audio: {e}")
        finally:
            try:
                os.remove(tmp_wav)
            except Exception:
                pass

    def _handle_native_query(self, body: Dict[str, Any]) -> None:
        prompt = str(body.get("prompt") or body.get("text") or "").strip()
        if not prompt:
            self._send_error(400, "Field 'prompt' is required.")
            return

        target_agent = str(body.get("agent_id") or "default")
        session_id = body.get("session_id")

        core = self.server_core
        if not core:
            from sayri.core import SayriCore
            core = SayriCore.get_instance()

        if not core:
            self._send_error(503, "Sayri Core is not running or available.")
            return

        cfg = self.server_cfg or getattr(sayri_config, "config", None)
        allow_owner_host = cfg.get_boolean("security", "gateway_owner_host_mode") if cfg else True
        sandbox_level = "LEVEL_3_HOST_USER" if allow_owner_host else "LEVEL_RESTRICTED_COMMANDS"

        result = core.process_remote_message(
            text=prompt,
            author="api_client",
            target_agent_id=target_agent,
            sandbox_level=sandbox_level,
            instance_id="sayri-api",
            session_id=session_id,
        )

        self._send_json(200, {"ok": True, "response": result, "agent_id": target_agent})

    def _handle_mobile_action(self, body: Dict[str, Any]) -> None:
        """Processes screen hierarchy perceived by Android Accessibility Service and decides action."""
        pkg = body.get("package_name", "unknown")
        elements = body.get("elements", [])
        user_intent = str(body.get("intent", ""))

        prompt = (
            f"[Mobile Screen Perception for package '{pkg}']\n"
            f"Interactive UI Elements:\n"
            f"{json.dumps(elements, indent=2)}\n\n"
            f"[User Goal]: {user_intent or 'Analyze screen and advise action'}"
        )

        core = self.server_core
        if not core:
            from sayri.core import SayriCore
            core = SayriCore.get_instance()

        if not core:
            self._send_error(503, "Sayri Core is not running or available.")
            return

        result = core.process_remote_message(
            text=prompt,
            author="android_accessibility",
            target_agent_id="default",
            sandbox_level="LEVEL_RESTRICTED_COMMANDS",
            instance_id="sayri-android",
        )

        self._send_json(200, {
            "ok": True,
            "decision": result,
            "package_name": pkg,
        })


class SayriApiServer:
    """Manages the background HTTP and OpenAI API daemon."""

    def __init__(self, core: Any, cfg: Any = None) -> None:
        self.core = core
        self.cfg = cfg or getattr(sayri_config, "config", None)
        self.httpd: Optional[ThreadingHTTPServer] = None
        self.thread: Optional[threading.Thread] = None
        self.running: bool = False
        self.start_time: float = 0.0

    def start(self) -> bool:
        if self.running:
            return True

        if self.cfg and not self.cfg.get_boolean("api", "enabled", True):
            print("[Sayri API] ⏸️ Remote API & OpenAI server is disabled in settings.")
            return False

        host = "0.0.0.0"
        port = 8765
        if self.cfg:
            host = self.cfg.get_string("api", "host", "0.0.0.0").strip() or "0.0.0.0"
            port = self.cfg.get_int("api", "port", 8765)

        SayriApiHandler.server_core = self.core
        SayriApiHandler.server_cfg = self.cfg

        try:
            self.httpd = ThreadingHTTPServer((host, port), SayriApiHandler)
            self.httpd.start_time = time.time()
            self.start_time = time.time()
            self.running = True

            self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
            self.thread.start()
            print(f"[Sayri API] 🌐 Remote API & OpenAI server listening on http://{host}:{port}")

            # Start UDP LAN discovery responder
            try:
                import socket
                self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self.udp_sock.bind(("0.0.0.0", port))
                self.udp_running = True

                def _udp_worker():
                    while self.udp_running and self.udp_sock:
                        try:
                            data, addr = self.udp_sock.recvfrom(1024)
                            if b"SAYRI_DISCOVER" in data:
                                reply = json.dumps({
                                    "service": "sayri",
                                    "name": "Pulsar OS",
                                    "version": "0.1.38",
                                    "port": port,
                                    "agent": "default"
                                }).encode("utf-8")
                                self.udp_sock.sendto(reply, addr)
                        except Exception:
                            break

                self.udp_thread = threading.Thread(target=_udp_worker, daemon=True)
                self.udp_thread.start()
            except Exception as e:
                print(f"[Sayri API] UDP discovery note: {e}")

            return True
        except Exception as exc:
            print(f"[Sayri API] ❌ Failed to bind API server to {host}:{port}: {exc}", file=sys.stderr)
            self.running = False
            return False

    def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        if hasattr(self, "udp_running"):
            self.udp_running = False
        if hasattr(self, "udp_sock") and self.udp_sock:
            try:
                self.udp_sock.close()
            except Exception:
                pass
            self.udp_sock = None
        if self.httpd:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
            except Exception:
                pass
            self.httpd = None
        print("[Sayri API] ⏹️ API server stopped.")


def get_api_server(core: Any = None, cfg: Any = None) -> SayriApiServer:
    global _SERVER_INSTANCE
    if _SERVER_INSTANCE is None:
        if core is None:
            from sayri.core import SayriCore
            core = SayriCore.get_instance()
        _SERVER_INSTANCE = SayriApiServer(core, cfg)
    else:
        if core is not None:
            _SERVER_INSTANCE.core = core
            SayriApiHandler.server_core = core
        if cfg is not None:
            _SERVER_INSTANCE.cfg = cfg
            SayriApiHandler.server_cfg = cfg
    return _SERVER_INSTANCE
