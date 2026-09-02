"""Static Security Auditor for Skills and Plugins in Sayri / Pulsar OS."""

from __future__ import annotations

import os
import re
from typing import List

from sayri.domain.models import SecurityAuditReport

DANGEROUS_PATTERNS = [
    (r"curl\s+[^\|]+\|\s*(?:bash|sh|zsh)", "Remote download and execution of scripts (curl | bash)", 80),
    (r"wget\s+[^\|]+\|\s*(?:bash|sh|zsh)", "Remote download and execution of scripts (wget | bash)", 80),
    (r"eval\s*\$\([^\)]+\)", "Unverified dynamic execution with eval", 60),
    (r"base64\s+(?:-d|--decode)\s*\|\s*(?:bash|sh)", "Base64 code obfuscation", 90),
    (r"nc\s+-[a-zA-Z0-9]*e\s+/bin/(?:bash|sh)", "Reverse Shell / backdoor with Netcat", 100),
    (r"(?:python|python3)\s+-c\s+['\"].*socket.*subprocess.*['\"]", "Reverse Shell in Python", 100),
    (r"rm\s+-rf\s+/(?:$|\s+|\*)", "Destructive deletion attempt of system root (rm -rf /)", 100),
    (r"mkfs\.(?:ext4|vfat|btrfs)", "Disk partition formatting", 95),
    (r"dd\s+if=\S+\s+of=/dev/\S+", "Raw write to /dev/ block devices", 95),
    (r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", "Fork Bomb / resource exhaustion attack", 90),
    (r"~/\.ssh/(?:id_rsa|authorized_keys|id_ed25519)", "Access to or exfiltration of private SSH keys", 85),
    (r"~/\.gnupg/", "Access to private GPG keys", 85),
    (r"(?:OPENAI|ANTHROPIC|MISTRAL|DEEPSEEK|GROQ)_API_KEY", "Attempt to read API Key environment variables directly", 75),
]


class SkillsScanner:
    """Performs pre-flight static analysis on Skills and Plugins before installation."""

    @staticmethod
    def audit_skill_content(content: str, name: str = "unknown") -> SecurityAuditReport:
        warnings: List[str] = []
        detected: List[str] = []
        total_risk = 0

        for pattern, desc, weight in DANGEROUS_PATTERNS:
            if re.search(pattern, content, re.IGNORECASE):
                warnings.append(f"⚠️ {desc}")
                detected.append(pattern)
                total_risk += weight

        total_risk = min(100, total_risk)
        is_safe = total_risk < 50
        recommendation = "ALLOW"
        if total_risk >= 80:
            recommendation = "BLOCK"
        elif total_risk >= 40:
            recommendation = "WARN"

        return SecurityAuditReport(
            target_name=name,
            target_type="skill",
            is_safe=is_safe,
            risk_score=total_risk,
            warnings=warnings,
            detected_patterns=detected,
            recommendation=recommendation,
        )

    @classmethod
    def audit_skill_file(cls, file_path: str) -> SecurityAuditReport:
        name = os.path.basename(os.path.dirname(file_path)) or os.path.basename(file_path)
        if not os.path.isfile(file_path):
            return SecurityAuditReport(
                target_name=name,
                target_type="skill",
                is_safe=False,
                risk_score=100,
                warnings=["The skill file does not exist."],
                recommendation="BLOCK",
            )
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            return cls.audit_skill_content(content, name)
        except Exception as exc:
            return SecurityAuditReport(
                target_name=name,
                target_type="skill",
                is_safe=False,
                risk_score=100,
                warnings=[f"Error reading file: {exc}"],
                recommendation="BLOCK",
            )
