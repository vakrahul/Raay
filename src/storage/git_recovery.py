"""Git task branch isolation, diff preview, and rollback recovery (Module 7)."""
from pathlib import Path
import subprocess
from typing import Dict, List, Optional, Tuple


class GitRecoveryManager:
    """Manages Git branch-per-task workflows, diff inspection, and atomic rollbacks."""

    def __init__(self, repo_dir: Path):
        self.repo_dir = repo_dir.resolve()

    def _run_git(self, args: List[str]) -> Tuple[int, str, str]:
        cmd = ["git"] + args
        proc = subprocess.run(
            cmd,
            cwd=str(self.repo_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()

    def is_git_repo(self) -> bool:
        ret, _, _ = self._run_git(["rev-parse", "--is-inside-work-tree"])
        return ret == 0

    def init_repo(self) -> bool:
        if not self.is_git_repo():
            ret, _, _ = self._run_git(["init"])
            return ret == 0
        return True

    def get_current_branch(self) -> str:
        ret, out, _ = self._run_git(["branch", "--show-current"])
        return out if ret == 0 else "main"

    def create_task_branch(self, task_id: str) -> str:
        """Create and switch to an isolated feature branch for the active task."""
        branch_name = f"task/{task_id}"
        # Create branch
        self._run_git(["checkout", "-b", branch_name])
        return branch_name

    def get_diff(self) -> str:
        """Inspect uncommitted modifications in the working tree."""
        _, out, _ = self._run_git(["diff"])
        return out

    def stage_changes(self, file_paths: Optional[List[str]] = None) -> bool:
        if file_paths:
            ret, _, _ = self._run_git(["add"] + file_paths)
        else:
            ret, _, _ = self._run_git(["add", "."])
        return ret == 0

    def commit_task(self, message: str) -> Tuple[bool, str]:
        """Commit staged task changes with a structured commit message."""
        ret, out, err = self._run_git(["commit", "-m", message])
        return ret == 0, out if ret == 0 else err

    def rollback_task(self, fallback_branch: str = "main") -> bool:
        """Atomically discard working changes and return to fallback branch."""
        # Discard working tree changes
        self._run_git(["reset", "--hard", "HEAD"])
        self._run_git(["clean", "-fd"])
        # Return to main branch
        ret, _, _ = self._run_git(["checkout", fallback_branch])
        return ret == 0
