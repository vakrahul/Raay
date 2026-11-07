"""Autonomous Software Project Scaffolder and Architecture Proposer (Module 4)."""
from pathlib import Path
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ProjectPlan(BaseModel):
    project_name: str
    description: str
    tech_stack: List[str]
    architecture_summary: str
    directory_structure: List[str]
    starter_files: Dict[str, str] = Field(default_factory=dict)
    clarified_requirements: List[str] = Field(default_factory=list)


class ProjectCreator:
    """Creates isolated software projects from natural language specifications."""

    def __init__(self, base_workspace_dir: Path):
        self.base_dir = (base_workspace_dir / "projects").resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def generate_plan(self, prompt: str) -> ProjectPlan:
        """Analyze natural language request and propose architecture, stack, and structure."""
        # Clean project name
        slug = re.sub(r"[^a-zA-Z0-9_-]", "_", prompt.lower().split()[0:3].__repr__()).strip("[]'_ ")
        if not slug:
            slug = "ai_project"
        project_name = f"proj_{slug}"

        # Determine stack based on keywords
        is_web = any(k in prompt.lower() for k in ["api", "web", "backend", "fastapi", "rest"])
        stack = ["Python 3.12", "FastAPI", "Uvicorn", "Pytest", "Pydantic v2"] if is_web else ["Python 3.12", "Pytest"]

        dirs = ["src", "tests", "docs"]
        starter_files = {
            "README.md": f"# {project_name}\n\n{prompt}\n\n## Architecture\nModular service created by WinAI-OE.",
            "requirements.txt": "pytest>=8.0.0\npydantic>=2.10.0\n" + ("fastapi>=0.115.0\nuvicorn>=0.30.0\n" if is_web else ""),
            "tests/__init__.py": "",
            "src/__init__.py": "",
        }

        if is_web:
            starter_files["src/main.py"] = (
                "from fastapi import FastAPI\n\n"
                "app = FastAPI(title='" + project_name + "')\n\n"
                "@app.get('/health')\ndef health():\n    return {'status': 'healthy'}\n"
            )
            starter_files["tests/test_api.py"] = (
                "import pytest\nfrom starlette.testclient import TestClient\n"
                "from src.main import app\n\n"
                "def test_health():\n    client = TestClient(app)\n"
                "    res = client.get('/health')\n    assert res.status_code == 200\n"
                "    assert res.json()['status'] == 'healthy'\n"
            )
        else:
            starter_files["src/core.py"] = "def execute_logic(x: int) -> int:\n    return x * 2\n"
            starter_files["tests/test_core.py"] = (
                "from src.core import execute_logic\n\n"
                "def test_core():\n    assert execute_logic(5) == 10\n"
            )

        return ProjectPlan(
            project_name=project_name,
            description=prompt,
            tech_stack=stack,
            architecture_summary="Modular architecture with isolated source and test directories.",
            directory_structure=dirs,
            starter_files=starter_files,
            clarified_requirements=["Python 3.12+ runtime", "Automated Pytest verification net"],
        )

    def scaffold_project(self, plan: ProjectPlan, overwrite: bool = False) -> Path:
        """Create the project directory and files safely, preventing unauthorized overwrites."""
        project_dir = (self.base_dir / plan.project_name).resolve()

        if project_dir.exists() and not overwrite:
            raise FileExistsError(
                f"Security Guard: Target project directory '{project_dir}' already exists. "
                "Overwriting requires explicit user authorization."
            )

        project_dir.mkdir(parents=True, exist_ok=True)

        for d in plan.directory_structure:
            (project_dir / d).mkdir(parents=True, exist_ok=True)

        for file_rel_path, content in plan.starter_files.items():
            file_path = project_dir / file_rel_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")

        return project_dir
