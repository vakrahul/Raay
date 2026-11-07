"""Document and project understanding with untrusted taint tagging (Phases 84-86)."""
import os
from pathlib import Path
from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class IngestedDocument(BaseModel):
    document_id: str
    file_path: str
    content: str
    is_untrusted_content: bool = True  # Phase 84: All external documents are untrusted
    taint_tag: str = "TAINT_UNTRUSTED"


class ProjectContextSummary(BaseModel):
    workspace_name: str
    manifest_types: List[str]
    detected_frameworks: List[str]
    file_count: int
    top_directories: List[str]


class WorkspaceIngestionService:
    """Ingests user documents and scans project workspaces to produce structured developer context."""

    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root.resolve()

    def ingest_document(self, rel_path: str, max_chars: int = 50000) -> IngestedDocument:
        """Read and tag document content with untrusted taint metadata."""
        target_path = (self.workspace_root / rel_path).resolve()
        if not target_path.exists():
            raise FileNotFoundError(f"Document '{rel_path}' does not exist")

        with open(target_path, "r", encoding="utf-8", errors="replace") as f:
            raw_text = f.read(max_chars)

        return IngestedDocument(
            document_id=f"doc_{target_path.stem}",
            file_path=str(target_path),
            content=raw_text,
            is_untrusted_content=True,
            taint_tag="TAINT_UNTRUSTED",
        )

    def scan_project(self) -> ProjectContextSummary:
        """Scan project structure and detect build manifests and languages."""
        manifests = []
        frameworks = []
        top_dirs = []
        file_count = 0

        manifest_mapping = {
            "package.json": "Node.js / JavaScript",
            "pyproject.toml": "Python (pyproject)",
            "requirements.txt": "Python (pip)",
            "Cargo.toml": "Rust",
            "WinAI.Client.csproj": ".NET / C# WinUI 3",
        }

        for item in self.workspace_root.iterdir():
            if item.name.startswith("."):
                continue
            if item.is_dir():
                top_dirs.append(item.name)
            elif item.is_file():
                file_count += 1
                if item.name in manifest_mapping:
                    manifests.append(item.name)
                    frameworks.append(manifest_mapping[item.name])

        return ProjectContextSummary(
            workspace_name=self.workspace_root.name,
            manifest_types=manifests,
            detected_frameworks=frameworks,
            file_count=file_count,
            top_directories=top_dirs,
        )
