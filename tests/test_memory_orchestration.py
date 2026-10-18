"""Tests for sayri-memory CLI, memory-ideas-agenda skill, and gateway owner host mode."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "usr" / "share" / "sayri" / "lib"
BIN_DIR = REPO_ROOT / "usr" / "bin"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from sayri import config, paths, skills
from sayri.domain.models import AgentProfile, SandboxConfig, SandboxLevel


class TestMemoryOrchestration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sayri_mem_test_")
        self.mem_dir = Path(self.tmp) / "memory"
        self.cfg_dir = Path(self.tmp) / "config"
        self.state_dir = Path(self.tmp) / "state"

        os.environ["SAYRI_CONFIG_DIR"] = str(self.cfg_dir)
        os.environ["SAYRI_STATE_DIR"] = str(self.state_dir)
        os.environ["SAYRI_MEMORY_DIR"] = str(self.mem_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_sayri_memory_cli_operations(self):
        """Test add-idea, get-ideas, add-tomorrow, get-tomorrow and traversal prevention."""
        cmd = [sys.executable, str(BIN_DIR / "sayri-memory")]
        env = dict(os.environ)

        # 1. Add and get idea
        res = subprocess.run(cmd + ["add-idea", "Build mobile companion app"], capture_output=True, text=True, env=env)
        self.assertEqual(res.returncode, 0)
        self.assertIn("Idea recorded", res.stdout)

        res_get = subprocess.run(cmd + ["get-ideas"], capture_output=True, text=True, env=env)
        self.assertEqual(res_get.returncode, 0)
        self.assertIn("Build mobile companion app", res_get.stdout)

        # 2. Add and get tomorrow reminder
        res_tom = subprocess.run(cmd + ["add-tomorrow", "Verify apt repository sync"], capture_output=True, text=True, env=env)
        self.assertEqual(res_tom.returncode, 0)
        self.assertIn("Task scheduled for tomorrow", res_tom.stdout)

        res_get_tom = subprocess.run(cmd + ["get-tomorrow"], capture_output=True, text=True, env=env)
        self.assertEqual(res_get_tom.returncode, 0)
        self.assertIn("Verify apt repository sync", res_get_tom.stdout)

        # 3. Path traversal attack rejection
        res_trav = subprocess.run(cmd + ["read", "../../etc/shadow"], capture_output=True, text=True, env=env)
        # Should sanitize to shadow.md and not read /etc/shadow
        self.assertNotIn("root:", res_trav.stdout)
        self.assertIn("does not exist", res_trav.stderr)

    def test_memory_skill_discovery(self):
        """Test that memory-ideas-agenda skill is recognized and readable."""
        installed = skills.list_skills()
        names = [s["name"] for s in installed]
        self.assertIn("memory-ideas-agenda", names)

        content = skills.read_skill("memory-ideas-agenda")
        self.assertIsNotNone(content)
        self.assertIn("sayri-memory", content)

    def test_config_gateway_owner_host_mode_default(self):
        """Verify default configuration for gateway_owner_host_mode."""
        cfg = config.Config()
        self.assertTrue(cfg.get_boolean("security", "gateway_owner_host_mode"))


if __name__ == "__main__":
    unittest.main()
