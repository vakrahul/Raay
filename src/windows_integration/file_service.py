"""Scoped filesystem service with atomic backups and rollback (Phase 67)."""
import os
from pathlib import Path
import shutil
import time
from typing import Dict, List, Optional, Tuple
from pydantic import BaseModel


class FileMetadata(BaseModel):
    path: str
    size_bytes: int
    is_directory: bool
    modified_at: float


class ScopedFileService:
    """Manages workspace-confined file operations with automatic backups and rollback."""

    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root.resolve()
        self.backup_dir = self.workspace_root / ".winai" / "backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        # backup_id -> (original_relative_path, backup_file_path)
        self._backups: Dict[str, Tuple[str, Path]] = {}

    def _resolve_safe_path(self, rel_path: str) -> Path:
        """Resolve path and verify confinement strictly within workspace_root."""
        target = (self.workspace_root / rel_path).resolve()
        if target != self.workspace_root and self.workspace_root not in target.parents:
            raise PermissionError(f"Path traversal blocked: '{target}' escapes workspace '{self.workspace_root}'")
        return target

    def safe_join(self, *parts: str) -> Path:
        """Join path parts and verify confinement without touching the filesystem."""
        rel = str(Path(*parts)) if parts else "."
        return self._resolve_safe_path(rel)

    def read_file(self, rel_path: str, max_bytes: int = 1048576) -> str:
        safe_path = self._resolve_safe_path(rel_path)
        if not safe_path.exists():
            raise FileNotFoundError(f"File '{rel_path}' does not exist")
        with open(safe_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(max_bytes)

    def write_file(self, rel_path: str, content: str, mode: str = "overwrite") -> str:
        """Write file content, creating an atomic backup if file already exists."""
        safe_path = self._resolve_safe_path(rel_path)
        safe_path.parent.mkdir(parents=True, exist_ok=True)

        backup_id = f"bak_{int(time.time() * 1000)}"
        if safe_path.exists():
            backup_file = self.backup_dir / f"{safe_path.name}_{backup_id}.bak"
            shutil.copy2(safe_path, backup_file)
            self._backups[backup_id] = (rel_path, backup_file)

        write_mode = "a" if mode == "append" else "w"
        with open(safe_path, write_mode, encoding="utf-8") as f:
            f.write(content)

        return backup_id

    def rollback(self, backup_id: str) -> bool:
        """Restore file from a previously created backup snapshot."""
        if backup_id not in self._backups:
            return False

        rel_path, backup_file = self._backups[backup_id]
        safe_path = self._resolve_safe_path(rel_path)
        if backup_file.exists():
            shutil.copy2(backup_file, safe_path)
            return True
        return False

    def list_directory(self, rel_path: str = ".") -> List[FileMetadata]:
        safe_path = self._resolve_safe_path(rel_path)
        if not safe_path.is_dir():
            raise NotADirectoryError(f"'{rel_path}' is not a directory")

        results = []
        for entry in safe_path.iterdir():
            # Skip hidden .winai folder from general listings
            if entry.name == ".winai":
                continue
            stat = entry.stat()
            results.append(
                FileMetadata(
                    path=entry.name,
                    size_bytes=stat.st_size,
                    is_directory=entry.is_dir(),
                    modified_at=stat.st_mtime,
                )
            )
        return results
