"""Domain Models for Sayri Hexagonal Architecture."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
import time
import uuid


class SandboxLevel(str, Enum):
    LEVEL_0_NO_EXEC = "LEVEL_0_NO_EXEC"          # Pure conversational / No commands allowed
    LEVEL_1_READONLY = "LEVEL_1_READONLY"        # Read-only filesystem (/), ephemeral tmp
    LEVEL_2_ISOLATED_DEV = "LEVEL_2_ISOLATED_DEV"# Read-only system, isolated workspace dir
    LEVEL_3_HOST_USER = "LEVEL_3_HOST_USER"      # Normal user host execution ($HOME)
    LEVEL_4_HOST_ROOT = "LEVEL_4_HOST_ROOT"      # Elevated via pkexec / Polkit


class ToolCallStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    DENIED = "denied"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class ToolCall:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = "bash"
    arguments: Dict[str, Any] = field(default_factory=dict)
    status: ToolCallStatus = ToolCallStatus.PENDING
    output: Optional[str] = None
    exit_code: Optional[int] = None
    duration_ms: float = 0.0


@dataclass
class Message:
    role: str  # "system", "user", "assistant", "tool"
    content: str
    tool_calls: List[ToolCall] = field(default_factory=list)
    tool_call_id: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "role": self.role,
            "content": self.content,
        }
        if self.tool_call_id:
            data["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            data["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": tc.arguments if isinstance(tc.arguments, str) else str(tc.arguments),
                    },
                }
                for tc in self.tool_calls
            ]
        return data


@dataclass
class AgentModelConfig:
    provider: str = "default"  # openai, anthropic, ollama, custom
    model_name: str = "default"
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    temperature: float = 0.7
    max_tokens: Optional[int] = None


@dataclass
class SandboxConfig:
    level: SandboxLevel = SandboxLevel.LEVEL_3_HOST_USER
    timeout_seconds: int = 10
    isolated_dir: Optional[str] = None
    allow_network: bool = True
    allowed_binaries: List[str] = field(default_factory=list)
    blocked_binaries: List[str] = field(default_factory=lambda: ["mkfs", "dd", "shutdown", "reboot"])


@dataclass
class AgentProfile:
    id: str
    name: str
    description: str
    system_prompt: str
    model: AgentModelConfig = field(default_factory=AgentModelConfig)
    sandbox: SandboxConfig = field(default_factory=SandboxConfig)
    allowed_skills: List[str] = field(default_factory=list)
    allowed_plugins: List[str] = field(default_factory=list)
    allowed_tools: List[str] = field(default_factory=lambda: ["bash", "read_skill", "search_history"])
    custom_instructions: str = ""
    created_at: float = field(default_factory=time.time)
    is_builtin: bool = False


@dataclass
class Session:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    title: str = "New Conversation"
    agent_id: str = "default"
    messages: List[Message] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    token_usage: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SecurityAuditReport:
    target_name: str
    target_type: str  # "skill" or "plugin"
    is_safe: bool
    risk_score: int  # 0 to 100 (0 = completely safe, 100 = malicious)
    warnings: List[str] = field(default_factory=list)
    detected_patterns: List[str] = field(default_factory=list)
    declared_capabilities: List[str] = field(default_factory=list)
    recommendation: str = "ALLOW"  # ALLOW, WARN, BLOCK


@dataclass
class PluginManifest:
    id: str
    name: str
    version: str
    author: str
    description: str
    entrypoint: str
    plugin_type: str = "tool"  # "tool", "gateway", "mcp"
    capabilities: List[str] = field(default_factory=list)
    allowed_domains: List[str] = field(default_factory=list)
    target_agent_id: str = "default"
    sandbox_level: SandboxLevel = SandboxLevel.LEVEL_1_READONLY
    min_sandbox_level: SandboxLevel = SandboxLevel.LEVEL_1_READONLY
    allow_in_level_0: bool = False
    is_authorized: bool = False

