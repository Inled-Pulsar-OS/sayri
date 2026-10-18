#!/usr/bin/env python3
"""Tests for Sayri sandbox security, privilege escalation prevention,
restricted commands allowlist, and single-instance indicator protection.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Add lib to path
HERE = Path(__file__).resolve().parent
LIB_DIR = HERE.parent / "usr" / "share" / "sayri" / "lib"
BIN_DIR = HERE.parent / "usr" / "bin"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from sayri.domain.models import AgentProfile, SandboxLevel, SandboxConfig
from sayri.domain.permissions import is_command_in_allowlist, default_effect_for_level, DENY, ALLOW
from sayri.domain.agent_creator import AgentCreator
from sayri.adapters.sandbox.executor import SandboxExecutor
from sayri import sysinfo, paths


class TestSandboxSecurity(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="sayri_sec_test_")
        self.orig_env = dict(os.environ)
        os.environ["SAYRI_CONFIG_DIR"] = os.path.join(self.tmp_dir, "config")
        os.environ["SAYRI_STATE_DIR"] = os.path.join(self.tmp_dir, "state")
        os.makedirs(os.environ["SAYRI_CONFIG_DIR"], exist_ok=True)
        os.makedirs(os.environ["SAYRI_STATE_DIR"], exist_ok=True)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.orig_env)
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_restricted_commands_allowlist(self):
        """Verify is_command_in_allowlist matches patterns accurately."""
        allowed = ["gh *", "git status", "echo hello", "npm test"]
        
        # Allowed commands
        self.assertTrue(is_command_in_allowlist("gh issue list", allowed))
        self.assertTrue(is_command_in_allowlist("gh pr create --title 'Test'", allowed))
        self.assertTrue(is_command_in_allowlist("git status", allowed))
        self.assertTrue(is_command_in_allowlist("echo hello", allowed))
        self.assertTrue(is_command_in_allowlist("npm test", allowed))

        # Disallowed commands
        self.assertFalse(is_command_in_allowlist("git push", allowed))
        self.assertFalse(is_command_in_allowlist("ls -la", allowed))
        self.assertFalse(is_command_in_allowlist("cat /etc/passwd", allowed))
        self.assertFalse(is_command_in_allowlist("sayri-agents create", allowed))
        self.assertFalse(is_command_in_allowlist("sayri-gateway save", allowed))

    def test_default_effect_for_restricted_commands(self):
        """LEVEL_RESTRICTED_COMMANDS must default to DENY for commands."""
        effect = default_effect_for_level(SandboxLevel.LEVEL_RESTRICTED_COMMANDS)
        self.assertEqual(effect, DENY)

    def test_executor_enforces_restricted_commands(self):
        """SandboxExecutor blocks any command not in allowed_commands with code 126."""
        sb = SandboxConfig(
            level=SandboxLevel.LEVEL_RESTRICTED_COMMANDS,
            allowed_binaries=["gh *", "echo allowed*"],
        )
        executor = SandboxExecutor()

        # Disallowed command
        code, out, _ = executor.execute("id", config=sb)
        self.assertEqual(code, 126)
        self.assertIn("restricted command execution", out)

        # Internal administrative tools are strictly blocked even if wildcarded
        sb_loose = SandboxConfig(
            level=SandboxLevel.LEVEL_RESTRICTED_COMMANDS,
            allowed_binaries=["*"],
        )
        code_admin, out_admin, _ = executor.execute("sayri-agents list", config=sb_loose)
        self.assertEqual(code_admin, 126)
        self.assertIn("internal management tool", out_admin)
        self.assertIn("prevent privilege escalation", out_admin)

    def test_prevent_subagent_privilege_escalation(self):
        """A sandboxed caller cannot create agents or subagents with elevated privileges."""
        # Simulated sandboxed caller in LEVEL_1_READONLY
        os.environ["SAYRI_SANDBOX_LEVEL"] = "LEVEL_1_READONLY"

        # Attempt to create an agent from prompt
        with self.assertRaises(PermissionError):
            AgentCreator.create_agent_from_prompt("Create an assistant that manages my system")

        # Attempt to save a Level 3 agent
        elevated_agent = AgentProfile(
            id="evil-agent",
            name="Evil Agent",
            description="Escalation test",
            system_prompt="Elevate",
            sandbox=SandboxConfig(level=SandboxLevel.LEVEL_3_HOST_USER),
        )
        with self.assertRaises(PermissionError):
            AgentCreator.save_agent(elevated_agent)

        # Attempt to delete an agent from sandbox
        with self.assertRaises(PermissionError):
            AgentCreator.delete_agent("default")

    def test_self_configuration_only_at_level_3_or_higher(self):
        """Level 3 agents CAN save profiles and configure agents."""
        os.environ["SAYRI_SANDBOX_LEVEL"] = "LEVEL_3_HOST_USER"

        agent = AgentProfile(
            id="task-bot",
            name="Task Bot",
            description="Automated task bot",
            system_prompt="Monitor and create gh issues",
            sandbox=SandboxConfig(
                level=SandboxLevel.LEVEL_RESTRICTED_COMMANDS,
                allowed_binaries=["gh *"],
            ),
            allowed_commands=["gh *"],
        )
        # Should succeed without error
        AgentCreator.save_agent(agent)
        retrieved = AgentCreator.get_agent("task-bot")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.sandbox.level, SandboxLevel.LEVEL_RESTRICTED_COMMANDS)
        self.assertEqual(retrieved.allowed_commands, ["gh *"])

    def test_cli_tools_prevent_sandboxed_tampering(self):
        """CLI utilities (sayri-agents, sayri-gateway, sayri-config) refuse execution when sandboxed."""
        env = dict(os.environ)
        env["PYTHONPATH"] = str(LIB_DIR)
        env["SAYRI_SANDBOX_LEVEL"] = "LEVEL_1_READONLY"

        # sayri-agents create
        proc_agents = subprocess.run(
            [sys.executable, str(BIN_DIR / "sayri-agents"), "create", '{"id":"bad","name":"Bad"}'],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertNotEqual(proc_agents.returncode, 0)
        self.assertIn("Security error", proc_agents.stderr + proc_agents.stdout)

        # sayri-gateway save
        proc_gw = subprocess.run(
            [sys.executable, str(BIN_DIR / "sayri-gateway"), "save", '{"id":"bad-gw","name":"Bad GW"}'],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertNotEqual(proc_gw.returncode, 0)
        self.assertIn("Security error", proc_gw.stderr + proc_gw.stdout)

        # sayri-config set
        proc_cfg = subprocess.run(
            [sys.executable, str(BIN_DIR / "sayri-config"), "set", "provider.model", "evil"],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertNotEqual(proc_cfg.returncode, 0)
        self.assertIn("Security error", proc_cfg.stderr + proc_cfg.stdout)

    def test_gateway_inherits_agent_sandbox(self):
        """When saving a gateway via sayri-gateway, sandbox level is bound to the agent."""
        env = dict(os.environ)
        env["PYTHONPATH"] = str(LIB_DIR)
        env["SAYRI_SANDBOX_LEVEL"] = "LEVEL_3_HOST_USER"

        # Create a restricted agent first
        restricted_agent = AgentProfile(
            id="discord-agent",
            name="Discord Bot Agent",
            description="Discord bot agent",
            system_prompt="Discord worker",
            sandbox=SandboxConfig(
                level=SandboxLevel.LEVEL_RESTRICTED_COMMANDS,
                allowed_binaries=["gh *"],
            ),
        )
        AgentCreator.save_agent(restricted_agent)

        # Save gateway bound to this agent
        proc_gw = subprocess.run(
            [
                sys.executable,
                str(BIN_DIR / "sayri-gateway"),
                "save",
                '{"id":"gw1","name":"Discord GW","plugin_id":"sayri-gateway-discord","agent_id":"discord-agent"}',
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(proc_gw.returncode, 0)

        # Inspect instances file
        from sayri.gateway_supervisor import gateway_supervisor
        instances = gateway_supervisor.list_instances()
        gw1 = next((i for i in instances if i["id"] == "gw1"), None)
        self.assertIsNotNone(gw1)
        self.assertEqual(gw1.get("sandbox_level"), SandboxLevel.LEVEL_RESTRICTED_COMMANDS.value)

    def test_single_instance_indicator_detection(self):
        """is_indicator_running detects active indicator lockfile."""
        import fcntl

        test_id = "test_indicator_companion"
        lock_path = os.path.join(paths.state_dir(), f"sayri-indicator-{test_id}.lock")
        self.assertFalse(sysinfo.is_indicator_running(test_id))

        # Simulate running indicator holding flock
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        try:
            self.assertTrue(sysinfo.is_indicator_running(test_id))
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def test_create_agent_from_prompt_restricted_commands_spanish(self):
        """create_agent_from_prompt correctly parses Spanish prompts and sets LEVEL_RESTRICTED_COMMANDS."""
        os.environ["SAYRI_SANDBOX_LEVEL"] = "LEVEL_3_HOST_USER"

        ok, msg, profile = AgentCreator.create_agent_from_prompt(
            "crea un subagente con el nivel de restricted commands para guardar tareas como propuestas con gh cli",
            max_allowed_level=SandboxLevel.LEVEL_3_HOST_USER,
        )
        self.assertTrue(ok)
        self.assertIsNotNone(profile)
        self.assertEqual(profile.sandbox.level, SandboxLevel.LEVEL_RESTRICTED_COMMANDS)
        self.assertIn("gh *", profile.allowed_commands)

        # Another variation: "comandos restringidos"
        ok2, msg2, profile2 = AgentCreator.create_agent_from_prompt(
            "crear subagente con comandos restringidos solo gh y git",
            max_allowed_level=SandboxLevel.LEVEL_3_HOST_USER,
        )
        self.assertTrue(ok2)
        self.assertIsNotNone(profile2)
        self.assertEqual(profile2.sandbox.level, SandboxLevel.LEVEL_RESTRICTED_COMMANDS)
        self.assertIn("gh *", profile2.allowed_commands)
        self.assertIn("git *", profile2.allowed_commands)

    def test_cajita_subagent_creation_with_restricted_commands(self):
        """Subagents with LEVEL_RESTRICTED_COMMANDS can be created via desktop UI caller."""
        rc_agent = AgentProfile(
            id="restricted-cajita-agent",
            name="Restricted Cajita Agent",
            description="Testing UI subagent creation",
            system_prompt="Only execute allowlisted commands",
            sandbox=SandboxConfig(
                level=SandboxLevel.LEVEL_RESTRICTED_COMMANDS,
                allowed_binaries=["gh *", "git status"],
            ),
            allowed_commands=["gh *", "git status"],
        )
        path = AgentCreator.save_agent(rc_agent, caller_level=SandboxLevel.LEVEL_3_HOST_USER)
        self.assertTrue(os.path.isfile(path))
        loaded = AgentCreator.get_agent("restricted-cajita-agent")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.sandbox.level, SandboxLevel.LEVEL_RESTRICTED_COMMANDS)
        self.assertEqual(loaded.allowed_commands, ["gh *", "git status"])


if __name__ == "__main__":
    unittest.main()
