"""Workspace descriptor mirror with validation (Phase 0128).

Mirrors the C# WorkspaceModel for pre-dispatch validation on the
orchestrator side. No filesystem side effects.
"""
from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator

KNOWN_TOOLS = (
    "fs_read_file",
    "fs_list_files",
    "fs_write_file",
    "fs_delete_file",
    "terminal_run",
    "cmd_exec",
    "browser_open_x",
    "browser_navigate",
    "app_launch",
    "deploy_project",
)


class WorkspaceDescriptor(BaseModel):
    """Validated workspace descriptor mirroring the desktop client model."""

    model_config = {"extra": "forbid"}

    workspace_id: str = Field(default="default")
    name: str = Field(default="Default Workspace", min_length=1)
    root_path: Path
    security_level: str = Field(default="strict", pattern="^(strict|moderate|permissive)$")
    allowed_tools: List[str] = Field(default_factory=list)

    @field_validator("root_path")
    @classmethod
    def root_must_be_absolute(cls, v: Path) -> Path:
        if not v.is_absolute():
            raise ValueError("Workspace root_path must be absolute.")
        return v.resolve()

    @field_validator("allowed_tools")
    @classmethod
    def tools_must_be_known(cls, v: List[str]) -> List[str]:
        unknown = [t for t in v if t not in KNOWN_TOOLS]
        if unknown:
            raise ValueError(f"Unknown tools in workspace descriptor: {unknown}")
        return v

    def allows_tool(self, tool_name: str) -> bool:
        return tool_name in self.allowed_tools
