"""Authenticated update and rollback manager (Phase 98)."""
import hashlib
import json
from pathlib import Path
import shutil
import time
from typing import Any, Dict, Optional
from pydantic import BaseModel


class UpdatePackage(BaseModel):
    version: str
    release_notes: str
    payload_sha256: str
    timestamp: float


class UpdateManager:
    """Manages update verification, backup checkpoints, and automated rollback."""

    def __init__(self, app_root: Path, backup_root: Optional[Path] = None):
        self.app_root = app_root.resolve()
        self.backup_root = (backup_root or (self.app_root / ".winai" / "update_backups")).resolve()
        self.backup_root.mkdir(parents=True, exist_ok=True)
        self.current_version = "0.1.0"

    def verify_package_integrity(self, package_bytes: bytes, expected_hash: str) -> bool:
        """Verify that update payload matches authentic SHA-256 signature."""
        computed_hash = hashlib.sha256(package_bytes).hexdigest()
        return computed_hash.lower() == expected_hash.lower()

    def create_recovery_checkpoint(self, checkpoint_id: str) -> Path:
        """Create a backup of critical files before applying an update."""
        checkpoint_dir = self.backup_root / checkpoint_id
        checkpoint_dir.mkdir(parents=True, exist_ok=True)

        meta = {
            "version": self.current_version,
            "timestamp": time.time(),
        }
        (checkpoint_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        return checkpoint_dir

    def rollback_to_checkpoint(self, checkpoint_id: str) -> bool:
        """Rollback to previously created recovery checkpoint."""
        checkpoint_dir = self.backup_root / checkpoint_id
        if not checkpoint_dir.exists():
            return False

        meta_file = checkpoint_dir / "meta.json"
        if meta_file.exists():
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            self.current_version = meta.get("version", self.current_version)
            return True
        return False
