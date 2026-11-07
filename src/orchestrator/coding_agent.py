"""Controlled coding and autonomous test-driven verification agent (Module 5)."""
import asyncio
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel

from src.windows_integration.process_runner import CommandResult, RestrictedProcessRunner


class TestExecutionSummary(BaseModel):
    tests_passed: bool
    total_run: int
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float


class CodingAgent:
    """Performs controlled code generation, editing, test execution, and self-healing fixes."""

    def __init__(self, project_dir: Path):
        self.project_dir = project_dir.resolve()
        self.runner = RestrictedProcessRunner(
            workspace_root=self.project_dir,
            default_timeout_seconds=30,
        )

    def read_file(self, rel_path: str) -> str:
        target = (self.project_dir / rel_path).resolve()
        if not target.is_relative_to(self.project_dir):
            raise PermissionError(f"Path jail violation: '{target}' escapes project '{self.project_dir}'")
        return target.read_text(encoding="utf-8")

    def write_file(self, rel_path: str, content: str) -> None:
        target = (self.project_dir / rel_path).resolve()
        if not target.is_relative_to(self.project_dir):
            raise PermissionError(f"Path jail violation: '{target}' escapes project '{self.project_dir}'")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    async def run_tests(self, test_path: str = "tests") -> TestExecutionSummary:
        """Run project tests via python -m pytest in isolated subprocess."""
        cmd = [sys.executable, "-m", "pytest", test_path, "-q"]
        result: CommandResult = await self.runner.run_command(cmd)

        passed = (result.exit_code == 0)
        return TestExecutionSummary(
            tests_passed=passed,
            total_run=result.stdout.count("passed") + result.stdout.count("failed"),
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_ms=result.duration_ms,
        )

    async def self_healing_cycle(
        self,
        fix_generator_func: Any,
        max_iterations: int = 3,
    ) -> Tuple[bool, List[TestExecutionSummary]]:
        """Run tests, and if failing, invoke fix generator until passing or iterations exhausted."""
        history = []

        for i in range(max_iterations):
            summary = await self.run_tests()
            history.append(summary)

            if summary.tests_passed:
                return True, history

            # Failure encountered: request fix
            print(f"[*] Test cycle {i+1} failed (exit code {summary.exit_code}). Generating correction...")
            fixes = await fix_generator_func(summary.stdout, summary.stderr)
            for file_path, corrected_content in fixes.items():
                self.write_file(file_path, corrected_content)

        # Final check
        final_summary = await self.run_tests()
        history.append(final_summary)
        return final_summary.tests_passed, history
