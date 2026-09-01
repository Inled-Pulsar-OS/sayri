"""Bubblewrap (bwrap) and Host Sandbox Executor for Sayri."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from typing import Tuple

from sayri.domain.models import SandboxConfig, SandboxLevel
from sayri.domain.secrets_manager import secrets_manager


class SandboxExecutionError(Exception):
    pass


class SandboxExecutor:
    """Executes commands adhering to fine-grained SandboxLevel configurations."""

    def __init__(self, sandboxes_root: str | None = None) -> None:
        self.sandboxes_root = sandboxes_root or os.path.expanduser("~/.local/share/sayri/sandboxes")
        os.makedirs(self.sandboxes_root, exist_ok=True)
        self.bwrap_available = bool(shutil.which("bwrap"))

    def execute(
        self,
        command: str,
        config: SandboxConfig,
        agent_id: str = "default",
    ) -> Tuple[int, str, float]:
        """Executes a command under the specified sandbox level.

        Returns: (exit_code, output, duration_ms)
        """
        start_time = time.monotonic()
        raw_cmd = command.strip()

        # 1. Level 0: Total Prohibition of command execution
        if config.level == SandboxLevel.LEVEL_0_NO_EXEC:
            return (
                126,
                "Error de seguridad (LEVEL_0_NO_EXEC): Este subagente tiene el nivel LEVEL_0_NO_EXEC configurado. "
                "No tiene permisos para ejecutar comandos en el sistema.",
                0.0,
            )

        # 2. Prevent Privilege Escalation (sudo / pkexec / su) for non-Level 4 sandboxes
        if ("sudo " in raw_cmd or "pkexec " in raw_cmd or raw_cmd.startswith("su ") or " su " in raw_cmd) and config.level != SandboxLevel.LEVEL_4_HOST_ROOT:
            return (
                126,
                f"Error de seguridad: Intento de escalada de privilegios bloqueado. "
                f"El nivel de sandbox '{config.level.value}' no permite elevación administrativa (sudo/pkexec).",
                0.0,
            )

        # 3. Block internal manager binaries in isolated sandboxes (LEVEL_1_READONLY & LEVEL_2_ISOLATED_DEV)
        is_isolated = config.level in (SandboxLevel.LEVEL_1_READONLY, SandboxLevel.LEVEL_2_ISOLATED_DEV)
        if is_isolated:
            internal_blocked = ("sayri-skills", "sayri-plugins", "sayri-settings", "sayri", "pkill", "killall")
            cmd_words = set(raw_cmd.split())
            for b in internal_blocked:
                if b in cmd_words or f"/{b}" in raw_cmd:
                    return (
                        126,
                        f"Error de seguridad: La herramienta interna de gestión '{b}' está bloqueada en el sandbox '{config.level.value}' para evitar escalada de privilegios.",
                        0.0,
                    )

        # 4. Explicit blocked binaries check
        for blocked in config.blocked_binaries:
            if blocked in raw_cmd.split():
                return (
                    126,
                    f"Error de seguridad: El comando '{blocked}' está explícitamente bloqueado en la política del agente.",
                    0.0,
                )

        timeout = max(1, config.timeout_seconds)

        # 5. Level 4: Elevated Host with Polkit (pkexec)
        if config.level == SandboxLevel.LEVEL_4_HOST_ROOT:
            if raw_cmd.startswith("sudo "):
                raw_cmd = "pkexec " + raw_cmd[5:]
            elif not raw_cmd.startswith("pkexec "):
                raw_cmd = "pkexec " + raw_cmd
            return self._run_host(raw_cmd, timeout, start_time, elevated=True)

        # 6. Level 3: Host as Current User
        if config.level == SandboxLevel.LEVEL_3_HOST_USER or not self.bwrap_available:
            return self._run_host(raw_cmd, timeout, start_time, elevated=False)

        # 7. Level 1 & 2: Sandboxed with Bubblewrap (bwrap)
        return self._run_bwrap(raw_cmd, config, agent_id, timeout, start_time)

    def _run_host(
        self, command: str, timeout: int, start_time: float, elevated: bool = False
    ) -> Tuple[int, str, float]:
        env = secrets_manager.inject_environment()
        # Preserve user Wayland/X11 and desktop environment variables for GUI apps
        for k in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "XDG_CURRENT_DESKTOP", "HOME", "USER"):
            if k in os.environ and k not in env:
                env[k] = os.environ[k]

        # If it is an explicit background or GUI launcher command
        is_bg = command.rstrip().endswith("&") or command.startswith("gtk-launch ") or command.startswith("xdg-open ")
        if is_bg and not elevated:
            try:
                proc = subprocess.Popen(
                    command,
                    shell=True,
                    env=env,
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                time.sleep(0.1)
                duration = (time.monotonic() - start_time) * 1000.0
                return (0, f"Comando lanzado en segundo plano en el host (PID: {proc.pid})", duration)
            except Exception as exc:
                duration = (time.monotonic() - start_time) * 1000.0
                return (1, f"Error iniciando proceso en segundo plano: {exc}", duration)

        try:
            res = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
            out = (res.stdout + "\n" + res.stderr).strip()
            retcode = res.returncode
            if retcode in (126, 127) and elevated:
                out = "El usuario canceló o denegó la autorización gráfica de administrador (Polkit)."
            elif not out:
                out = f"(Comando completado con código de salida {res.returncode})"
            duration = (time.monotonic() - start_time) * 1000.0
            return (retcode, out, duration)
        except subprocess.TimeoutExpired as exc:
            raw_out = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            raw_err = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            partial = (raw_out + "\n" + raw_err).strip()
            duration = (time.monotonic() - start_time) * 1000.0
            return (
                0,
                partial or "(El comando continúa ejecutándose en segundo plano en el host)",
                duration,
            )
        except Exception as exc:
            duration = (time.monotonic() - start_time) * 1000.0
            return (1, f"Error de ejecución en el host: {exc}", duration)

    def _run_bwrap(
        self,
        command: str,
        config: SandboxConfig,
        agent_id: str,
        timeout: int,
        start_time: float,
    ) -> Tuple[int, str, float]:
        workspace = config.isolated_dir or os.path.join(self.sandboxes_root, agent_id)
        os.makedirs(workspace, exist_ok=True)

        bwrap_args = [
            "bwrap",
            "--ro-bind", "/", "/",
            "--dev", "/dev",
            "--proc", "/proc",
            "--tmpfs", "/tmp",
            "--tmpfs", "/run",
            "--bind", workspace, workspace,
            "--chdir", workspace,
            "--die-with-parent",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
        ]

        if not config.allow_network:
            bwrap_args.append("--unshare-net")

        # Strip trailing '&' in sandboxed execution to prevent silently masking background failures
        sync_command = command.strip()
        if sync_command.endswith("&"):
            sync_command = sync_command[:-1].strip()

        # Wrap the command in a clean bash shell inside the container
        bwrap_args.extend(["--", "bash", "-c", sync_command])

        try:
            res = subprocess.run(
                bwrap_args,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=secrets_manager.inject_environment(allowed_keys=[]),
            )
            out = (res.stdout + "\n" + res.stderr).strip()
            retcode = res.returncode

            # Check for graphical display connection failures inside the sandbox
            display_error_indicators = (
                "Failed to open display",
                "Cannot open display",
                "Unable to init server",
                "Authorization required, but no authorization protocol specified",
                "could not connect to display",
                "No protocol specified",
            )
            if any(ind in out for ind in display_error_indicators):
                retcode = 126
                out = (
                    f"Error de Seguridad/Sandbox ({config.level.value}): No se puede abrir una ventana gráfica "
                    f"desde este contenedor aislado (sin acceso a Wayland/X11).\n"
                    f"Para abrir aplicaciones de escritorio en la pantalla del usuario se requiere nivel LEVEL_3_HOST_USER.\n"
                    f"Detalle técnico: {out}"
                )
            elif not out:
                out = f"(Sandbox bwrap finalizado con código de salida {retcode})"

            duration = (time.monotonic() - start_time) * 1000.0
            return (retcode, out, duration)
        except subprocess.TimeoutExpired as exc:
            duration = (time.monotonic() - start_time) * 1000.0
            return (124, f"Error: Límite de tiempo en sandbox ({timeout}s) excedido.", duration)
        except Exception as exc:
            duration = (time.monotonic() - start_time) * 1000.0
            return (1, f"Error de sandbox bwrap: {exc}", duration)
