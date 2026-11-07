"""Comprehensive Specialized Agent Registry with 12 Domain Roles (Phase 4)."""
from enum import Enum
from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class AgentRole(str, Enum):
    RESEARCHER = "researcher"
    DATA_SCIENTIST = "data_scientist"
    DATA_ANALYST = "data_analyst"
    SOFTWARE_ENGINEER = "software_engineer"
    FRONTEND_DEVELOPER = "frontend_developer"
    BACKEND_DEVELOPER = "backend_developer"
    DATABASE_SPECIALIST = "database_specialist"
    BROWSER_AUTOMATOR = "browser_automator"
    QA_TESTER = "qa_tester"
    SECURITY_REVIEWER = "security_reviewer"
    DOCUMENTATION_SPECIALIST = "documentation_specialist"
    DEPLOYMENT_PREPARER = "deployment_preparer"

    # Compatibility Aliases
    CODER = "coder"
    FILE_ANALYST = "file_analyst"
    TEST_RUNNER = "test_runner"
    COORDINATOR = "coordinator"


class SpecializedAgent(BaseModel):
    """Definition and permission envelope of an individual specialized agent."""
    role: AgentRole
    agent_id: str
    display_name: str
    system_prompt: str
    authorized_tools: List[str]
    max_tokens: int = 4096
    max_duration_seconds: int = 60
    verification_requirements: str = "Self-contained execution result with 0 security exceptions"


class AgentRegistry:
    """Registry maintaining available specialized agent definitions."""

    def __init__(self):
        self._agents: Dict[str, SpecializedAgent] = {}
        self._register_default_agents()

    def _register_default_agents(self) -> None:
        # 1. Research Agent (Read-only discovery)
        self.register_agent(SpecializedAgent(
            role=AgentRole.RESEARCHER,
            agent_id="agt_researcher",
            display_name="Research Specialist",
            system_prompt="You are a research agent. Search verified facts, papers, and documents.",
            authorized_tools=["fs_read_file", "fs_list_files", "browser_navigate"],
        ))

        # 2. Data Scientist Agent (Modeling & statistical evaluation)
        self.register_agent(SpecializedAgent(
            role=AgentRole.DATA_SCIENTIST,
            agent_id="agt_datascientist",
            display_name="Data Science Specialist",
            system_prompt="You analyze datasets, engineer features, and train/evaluate reproducible ML models.",
            authorized_tools=["fs_read_file", "fs_write_file", "terminal_run"],
        ))

        # 3. Data Analyst Agent (EDA & visualization)
        self.register_agent(SpecializedAgent(
            role=AgentRole.DATA_ANALYST,
            agent_id="agt_dataanalyst",
            display_name="Data Analyst",
            system_prompt="You inspect tabular datasets, check distributions, and create charts and insights.",
            authorized_tools=["fs_read_file", "fs_list_files"],
        ))

        # 4. Software Engineering Agent (Core architecture & system code)
        self.register_agent(SpecializedAgent(
            role=AgentRole.SOFTWARE_ENGINEER,
            agent_id="agt_engineer",
            display_name="Software Engineer",
            system_prompt="You design, implement, and refactor clean, modular software solutions.",
            authorized_tools=["fs_read_file", "fs_write_file", "terminal_run"],
        ))

        # 5. Frontend Development Agent (UI/UX, HTML/CSS/React/WinUI)
        self.register_agent(SpecializedAgent(
            role=AgentRole.FRONTEND_DEVELOPER,
            agent_id="agt_frontend",
            display_name="Frontend Developer",
            system_prompt="You implement accessible, responsive user interfaces and client interactions.",
            authorized_tools=["fs_read_file", "fs_write_file"],
        ))

        # 6. Backend Development Agent (REST APIs, WebSockets, business logic)
        self.register_agent(SpecializedAgent(
            role=AgentRole.BACKEND_DEVELOPER,
            agent_id="agt_backend",
            display_name="Backend Developer",
            system_prompt="You construct robust APIs, async services, and microservices in FastAPI and Python.",
            authorized_tools=["fs_read_file", "fs_write_file", "terminal_run"],
        ))

        # 7. Database Specialist Agent (Schemas, SQL, indexing, graph DBs)
        self.register_agent(SpecializedAgent(
            role=AgentRole.DATABASE_SPECIALIST,
            agent_id="agt_database",
            display_name="Database Specialist",
            system_prompt="You manage relational schemas, SQLite WAL migrations, and vector stores.",
            authorized_tools=["fs_read_file", "fs_write_file"],
        ))

        # 8. Browser Automation Agent (Controlled web extraction)
        self.register_agent(SpecializedAgent(
            role=AgentRole.BROWSER_AUTOMATOR,
            agent_id="agt_browser",
            display_name="Browser Automation Agent",
            system_prompt="You operate permitted browser sessions to read pages, test UI rendering, and extract listings.",
            authorized_tools=["browser_open_x", "browser_navigate", "uia_control"],
        ))

        # 9. QA and Testing Agent (Pytest, test suites, regression fixes)
        self.register_agent(SpecializedAgent(
            role=AgentRole.QA_TESTER,
            agent_id="agt_qa",
            display_name="QA Test Engineer",
            system_prompt="You execute test harnesses, diagnose failure traces, and verify test nets.",
            authorized_tools=["terminal_run", "fs_read_file", "fs_write_file"],
        ))

        # 10. Security Review Agent (Vulnerability scans, dependency review)
        self.register_agent(SpecializedAgent(
            role=AgentRole.SECURITY_REVIEWER,
            agent_id="agt_security",
            display_name="Security Auditor",
            system_prompt="You audit code against OWASP Top 10, check for path traversal, secret leaks, and command escapes.",
            authorized_tools=["fs_read_file", "fs_list_files"],
        ))

        # 11. Documentation Agent (PRDs, architecture diagrams, summaries)
        self.register_agent(SpecializedAgent(
            role=AgentRole.DOCUMENTATION_SPECIALIST,
            agent_id="agt_docs",
            display_name="Technical Writer",
            system_prompt="You write clear, accurate technical documentation, READMEs, and execution reports.",
            authorized_tools=["fs_write_file", "fs_read_file"],
        ))

        # 12. Deployment Preparation Agent (Build validation, package generation)
        self.register_agent(SpecializedAgent(
            role=AgentRole.DEPLOYMENT_PREPARER,
            agent_id="agt_deployment",
            display_name="Deployment Specialist",
            system_prompt="You prepare deployment plans and submit them for user authorization.",
            authorized_tools=["fs_read_file", "deploy_project"],
        ))

        # Compatibility Roles
        self.register_agent(SpecializedAgent(
            role=AgentRole.CODER,
            agent_id="agt_coder",
            display_name="Code Engineer",
            system_prompt="You implement precise code modifications.",
            authorized_tools=["fs_read_file", "fs_write_file"],
        ))
        self.register_agent(SpecializedAgent(
            role=AgentRole.FILE_ANALYST,
            agent_id="agt_analyst",
            display_name="Repository Analyst",
            system_prompt="You analyze project structures, schemas, and dependencies.",
            authorized_tools=["fs_list_files", "fs_read_file"],
        ))
        self.register_agent(SpecializedAgent(
            role=AgentRole.TEST_RUNNER,
            agent_id="agt_tester",
            display_name="Automated Test Runner",
            system_prompt="You execute test suites and analyze test outputs.",
            authorized_tools=["terminal_run", "fs_read_file"],
        ))

    def register_agent(self, agent: SpecializedAgent) -> None:
        self._agents[agent.agent_id] = agent

    def get_agent(self, agent_id: str) -> Optional[SpecializedAgent]:
        return self._agents.get(agent_id)

    def get_by_role(self, role: AgentRole) -> Optional[SpecializedAgent]:
        for agt in self._agents.values():
            if agt.role == role:
                return agt
        return None

    def list_agents(self) -> List[SpecializedAgent]:
        return list(self._agents.values())
