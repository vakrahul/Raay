"""Restricted subprocess runner with environment scrubbing and output limits (Phases 68-69)."""
import asyncio
import os
from pathlib import Path
import subprocess
import time
from typing import Dict, List, Optional
from pydantic import BaseModel


class CommandResult(BaseModel):
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float
    timed_out: bool = False


class RestrictedProcessRunner:
    """Spawns and monitors commands in an isolated subprocess container."""

    SENSITIVE_ENV_VARS = [
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
        "GNANI_API_KEY", "VACHANA_API_KEY",
        "WINAI_IPC_TOKEN", "AWS_SECRET_ACCESS_KEY", "AZURE_CLIENT_SECRET",
        "GITHUB_TOKEN", "SSH_AUTH_SOCK",
    ]

    def __init__(
        self,
        workspace_root: Path,
        default_timeout_seconds: int = 60,
        max_output_bytes: int = 51200,
    ):
        self.workspace_root = workspace_root.resolve()
        self.timeout = default_timeout_seconds
        self.max_output_bytes = max_output_bytes

    def _get_scrubbed_env(self) -> Dict[str, str]:
        """Produce a clean environment dict free of API tokens or credentials."""
        clean_env = {}
        for k, v in os.environ.items():
            if k in self.SENSITIVE_ENV_VARS or any(
                s in k.lower() for s in ["secret", "token", "password", "api_key"]
            ):
                continue
            clean_env[k] = v
        return clean_env

    async def run_command(
        self,
        command_args: List[str],
        timeout_seconds: Optional[int] = None,
    ) -> CommandResult:
        """Run command with parameter array, scrubbed env, timeout, and output truncation."""
        if not command_args:
            raise ValueError("Command args cannot be empty")

        timeout = timeout_seconds or self.timeout
        start_time = time.time()
        env = self._get_scrubbed_env()

        try:
            process = await asyncio.create_subprocess_exec(
                *command_args,
                cwd=str(self.workspace_root),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout_data, stderr_data = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout,
                )
                duration = (time.time() - start_time) * 1000
                stdout_str = stdout_data.decode("utf-8", errors="replace")[: self.max_output_bytes]
                stderr_str = stderr_data.decode("utf-8", errors="replace")[: self.max_output_bytes]

                return CommandResult(
                    exit_code=process.returncode or 0,
                    stdout=stdout_str,
                    stderr=stderr_str,
                    duration_ms=duration,
                    timed_out=False,
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                duration = (time.time() - start_time) * 1000
                return CommandResult(
                    exit_code=-1,
                    stdout="",
                    stderr="Execution timed out and process was terminated.",
                    duration_ms=duration,
                    timed_out=True,
                )

        except Exception as e:
            duration = (time.time() - start_time) * 1000
            return CommandResult(
                exit_code=-1,
                stdout="",
                stderr=str(e),
                duration_ms=duration,
                timed_out=False,
            )
