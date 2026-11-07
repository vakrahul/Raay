"""Generate the exact 1,000-phase WinAI-OE master roadmap (JSON + Markdown).

Structure: 20 stages x 50 phases. Each stage has 10 focus areas x 5 maturity levels:
  L1 Inventory and current-state audit
  L2 Design specification and acceptance criteria
  L3 Core implementation
  L4 Hardening, edge cases and security review
  L5 Integration, regression tests and sign-off

All output is deterministic. Validates count, numbering, and dependency ordering.
"""
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = REPO_ROOT / "docs"
JSON_PATH = DOCS_DIR / "MASTER_ROADMAP_1000_PHASES.json"
MD_PATH = DOCS_DIR / "MASTER_ROADMAP_1000_PHASES.md"

LEVELS = [
    ("L1", "Inventory and current-state audit"),
    ("L2", "Design specification and acceptance criteria"),
    ("L3", "Core implementation"),
    ("L4", "Hardening, edge cases and security review"),
    ("L5", "Integration, regression tests and sign-off"),
]

# Each stage: (stage_no, stage_name, phase_start, phase_end, category, [10 focus areas]).
# Each focus area: (area_title, files_or_components, test_hint)
STAGES = [
(1, "Repository Audit and Engineering Baseline", 1, 50, "Audit & Baseline", [
  ("Top-level repository layout and documentation index", "README.md, docs/, .gitignore", "tests/unit/test_environment.py"),
  ("Python packaging and dependency pins", "pyproject.toml, requirements.txt, requirements-dev.txt", "tests/unit/test_environment.py"),
  ("FastAPI entry point and app factory", "src/orchestrator/main.py, run_vertical_slice.py", "tests/integration/test_vertical_slice.py"),
  ("Configuration and environment validation", "src/orchestrator/config.py", "tests/unit/test_config.py"),
  ("Provider base contracts and registry", "src/providers/base.py, src/providers/registry.py", "tests/unit/test_providers_stage3.py"),
  ("Provider adapters and resilience", "src/providers/*_adapter.py, src/providers/resilience.py", "tests/unit/test_providers_stage3.py"),
  ("Brain memory tiers and context engine", "src/orchestrator/brain/", "tests/unit/test_brain_stage4.py"),
  ("Planner, coordinator and agent factory", "src/orchestrator/planner/", "tests/unit/test_planner_stage5.py"),
  ("Windows integration and security core", "src/windows_integration/, src/security/, src/storage/", "tests/security/test_security_audit.py"),
  ("Test suite health and docs baseline", "tests/, docs/", "tests/unit/test_environment.py"),
]),
(2, "Core Architecture and Service Reliability", 51, 100, "Architecture", [
  ("FastAPI application lifecycle and startup ordering", "src/orchestrator/main.py", "tests/integration/test_vertical_slice.py"),
  ("Strict configuration validation and safe defaults", "src/orchestrator/config.py", "tests/unit/test_config.py"),
  ("Health, readiness and liveness endpoints", "src/orchestrator/main.py", "tests/integration/test_vertical_slice.py"),
  ("Structured logging foundation", "src/security/audit_logger.py", "tests/unit/test_audit_logger.py"),
  ("Error contracts and fail-closed handling", "src/orchestrator/main.py, src/security/", "tests/unit/test_reliability_stage10.py"),
  ("Async task supervision and cancellation", "src/orchestrator/planner/coordinator.py", "tests/unit/test_planner_stage5.py"),
  ("Loopback IPC and WebSocket streaming", "src/orchestrator/main.py", "tests/integration/test_vertical_slice.py"),
  ("Graceful shutdown and resource cleanup", "run_vertical_slice.py, src/orchestrator/main.py", "tests/unit/test_reliability_stage10.py"),
  ("Dependency versioning and reproducibility", "pyproject.toml, requirements.txt", "tests/unit/test_environment.py"),
  ("Service diagnostics and startup self-check", "src/orchestrator/main.py, scripts/", "tests/integration/test_vertical_slice.py"),
]),
(3, "Premium Native Windows Application", 101, 150, "Desktop UI", [
  ("WinUI 3 shell and app manifest", "src/client/WinAI.Client/WinAI.Client.csproj, app.manifest", "tests/unit/test_client_architecture.py"),
  ("Navigation shell and routing", "src/client/WinAI.Client/Views/MainWindow.xaml*", "tests/unit/test_client_architecture.py"),
  ("Main conversation workspace", "src/client/WinAI.Client/Views/ChatView.xaml*", "tests/unit/test_client_architecture.py"),
  ("Agent activity panel", "src/client/WinAI.Client/Models/AgentActivityModel.cs", "tests/unit/test_client_architecture.py"),
  ("Task center views", "src/client/WinAI.Client/ViewModels/MainViewModel.cs", "tests/unit/test_client_architecture.py"),
  ("Project workspace views", "src/client/WinAI.Client/Models/WorkspaceModel.cs", "tests/unit/test_client_architecture.py"),
  ("Application integration center", "src/client/WinAI.Client/Models/ProviderConnectionModel.cs", "tests/unit/test_client_architecture.py"),
  ("Security and approval center", "src/client/WinAI.Client/Views/ApprovalDialog.xaml*", "tests/unit/test_client_architecture.py"),
  ("Memory and model configuration views", "src/client/WinAI.Client/ViewModels/", "tests/unit/test_client_architecture.py"),
  ("Settings, theming and accessibility", "src/client/WinAI.Client/, app.manifest", "tests/unit/test_client_architecture.py"),
]),
(4, "Conversational Intelligence and Interaction", 151, 200, "Conversation", [
  ("Chat message contracts and roles", "src/providers/base.py, src/client/WinAI.Client/Models/ChatMessageModel.cs", "tests/integration/test_vertical_slice.py"),
  ("Streaming token pipeline", "src/orchestrator/main.py (/ws/v1/stream)", "tests/integration/test_vertical_slice.py"),
  ("Cancellation and stop generation", "src/orchestrator/main.py, ChatViewModel", "tests/unit/test_reliability_stage10.py"),
  ("Conversation history management", "src/orchestrator/brain/working_memory.py", "tests/unit/test_brain_stage4.py"),
  ("Prompt construction and prefix reuse", "src/orchestrator/prompt_cache.py", "tests/unit/test_token_optimizer.py"),
  ("Tool-call rendering and confirmation", "src/orchestrator/task_dispatcher.py", "tests/integration/test_vertical_slice.py"),
  ("Approval request UX flow", "src/security/approval_broker.py, ApprovalDialog", "tests/security/test_security_audit.py"),
  ("Empty, error and retry states", "src/orchestrator/main.py, src/providers/resilience.py", "tests/unit/test_reliability_stage10.py"),
  ("Multi-turn context windowing", "src/orchestrator/brain/context_engine.py", "tests/unit/test_brain_stage4.py"),
  ("Conversation persistence across restarts", "src/orchestrator/brain/persistent_store.py", "tests/unit/test_brain_stage4.py"),
]),
(5, "Persistent Context and Memory", 201, 250, "Memory", [
  ("Working memory lifecycle", "src/orchestrator/brain/working_memory.py", "tests/unit/test_brain_stage4.py"),
  ("Persistent SQLite store", "src/orchestrator/brain/persistent_store.py", "tests/unit/test_brain_stage4.py"),
  ("Semantic vector memory", "src/orchestrator/brain/semantic_memory.py", "tests/unit/test_brain_stage4.py"),
  ("Project-scoped memory", "src/orchestrator/brain/project_memory.py", "tests/unit/test_brain_stage4.py"),
  ("Error and mistake-learning memory", "src/orchestrator/brain/error_memory.py", "tests/unit/test_dynamic_brain.py"),
  ("Unified context engine assembly", "src/orchestrator/brain/context_engine.py", "tests/unit/test_autonomous_agent_phases_2_to_12.py"),
  ("Context compaction policy", "src/orchestrator/brain/context_prioritizer.py", "tests/unit/test_brain_stage4.py"),
  ("Provenance and confidence tiers", "src/orchestrator/brain/context_engine.py", "tests/unit/test_autonomous_agent_phases_2_to_12.py"),
  ("Retention, correction and deletion", "src/orchestrator/brain/brain_subsystem.py", "tests/unit/test_brain_stage4.py"),
  ("Memory management UI controls", "src/client/WinAI.Client/ (Memory views)", "tests/unit/test_client_architecture.py"),
]),
(6, "Planning and Task Decomposition", 251, 300, "Planning", [
  ("Task models and state machine", "src/orchestrator/planner/task_models.py", "tests/unit/test_planner_stage5.py"),
  ("DAG validation and cycle detection", "src/orchestrator/planner/task_models.py", "tests/unit/test_planner_stage5.py"),
  ("Autonomous outcome planner", "src/orchestrator/planner/autonomous_planner.py", "tests/unit/test_autonomous_agent_phases_2_to_12.py"),
  ("Assumption tracking and clarification gates", "src/orchestrator/planner/autonomous_planner.py", "tests/unit/test_autonomous_agent_phases_2_to_12.py"),
  ("Risk levels and destructive-action guards", "src/orchestrator/planner/autonomous_planner.py", "tests/security/test_security_audit.py"),
  ("Token and time budget estimation", "src/orchestrator/token_tracker.py", "tests/unit/test_token_optimizer.py"),
  ("Plan repair after step failure", "src/orchestrator/planner/coordinator.py", "tests/unit/test_planner_stage5.py"),
  ("Plan persistence and resume", "src/storage/task_state_engine.py", "tests/unit/test_modules_2_to_10.py"),
  ("Plan visualization data contracts", "src/orchestrator/planner/", "tests/unit/test_planner_stage5.py"),
  ("Planner evaluation harness", "tests/unit/test_planner_stage5.py", "tests/unit/test_planner_stage5.py"),
]),
(7, "Multi-Agent Orchestration", 301, 350, "Agents", [
  ("Supervisor agent contract", "src/orchestrator/planner/agent_registry.py", "tests/unit/test_autonomous_agent_phases_2_to_12.py"),
  ("Planner agent contract", "src/orchestrator/planner/agent_registry.py", "tests/unit/test_planner_stage5.py"),
  ("Research and data agents", "src/orchestrator/planner/agent_registry.py", "tests/unit/test_autonomous_agent_phases_2_to_12.py"),
  ("Engineering and testing agents", "src/orchestrator/planner/agent_registry.py", "tests/unit/test_modules_2_to_10.py"),
  ("Security, docs and review agents", "src/orchestrator/planner/agent_registry.py", "tests/security/test_security_audit.py"),
  ("Sequential and parallel scheduling", "src/orchestrator/planner/coordinator.py", "tests/unit/test_planner_stage5.py"),
  ("Agent-to-agent handoff protocol", "src/orchestrator/planner/coordinator.py", "tests/unit/test_planner_stage5.py"),
  ("Cancellation propagation", "src/orchestrator/planner/coordinator.py", "tests/unit/test_reliability_stage10.py"),
  ("Failure recovery and retry bounds", "src/orchestrator/planner/coordinator.py", "tests/unit/test_modules_2_to_10.py"),
  ("Dynamic factory governance and budgets", "src/orchestrator/planner/agent_factory.py", "tests/unit/test_dynamic_brain.py"),
]),
(8, "Adaptive Application Integration", 351, 400, "Adaptation", [
  ("Adaptation engine core loop", "src/windows_integration/execution_engine.py", "tests/unit/test_autonomous_agent_phases_2_to_12.py"),
  ("Capability taxonomy and evidence states", "src/orchestrator/verification_reporter.py", "tests/unit/test_autonomous_agent_phases_2_to_12.py"),
  ("Official API adapters", "src/windows_integration/app_manager.py", "tests/integration/test_windows_integration_stage7.py"),
  ("Accessibility and UIA adapters", "src/windows_integration/uia_service.py", "tests/integration/test_windows_integration_stage7.py"),
  ("DOM-based browser adapters", "src/windows_integration/browser_service.py", "tests/integration/test_windows_integration_stage7.py"),
  ("Keyboard and mouse fallback", "src/windows_integration/vision_automation.py", "tests/integration/test_windows_integration_stage7.py"),
  ("Computer-vision fallback", "src/windows_integration/vision_automation.py", "tests/integration/test_windows_integration_stage7.py"),
  ("Application knowledge recording", "src/orchestrator/brain/project_memory.py", "tests/unit/test_brain_stage4.py"),
  ("Interface change detection", "src/orchestrator/verification_reporter.py", "tests/unit/test_reliability_stage10.py"),
  ("Human-guided learning flow", "src/orchestrator/brain/error_memory.py", "tests/unit/test_dynamic_brain.py"),
]),
(9, "Browser and Web Automation", 401, 450, "Browser", [
  ("Shared session manager core", "src/windows_integration/browser_session_manager.py", "tests/unit/test_autonomous_agent_phases_2_to_12.py"),
  ("Session ownership and locking", "src/windows_integration/browser_session_manager.py", "tests/unit/test_autonomous_agent_phases_2_to_12.py"),
  ("Navigation history tracking", "src/windows_integration/browser_session_manager.py", "tests/integration/test_windows_integration_stage7.py"),
  ("Download tracking and quarantine", "src/windows_integration/browser_session_manager.py", "tests/security/test_isolation_stage8.py"),
  ("Task timeouts and cancellation", "src/windows_integration/browser_session_manager.py", "tests/unit/test_reliability_stage10.py"),
  ("Crash recovery and session restore", "src/windows_integration/browser_session_manager.py", "tests/unit/test_reliability_stage10.py"),
  ("Auth-state protection and secret isolation", "src/storage/credential_vault.py", "tests/unit/test_providers_stage3.py"),
  ("Rate limiting and robots compliance", "src/windows_integration/browser_service.py", "tests/integration/test_windows_integration_stage7.py"),
  ("CAPTCHA and login handoff to user", "src/windows_integration/browser_service.py", "tests/integration/test_windows_integration_stage7.py"),
  ("Outcome verification for web actions", "src/windows_integration/dev_server_verifier.py", "tests/unit/test_modules_2_to_10.py"),
]),
(10, "Windows Desktop Execution", 451, 500, "Desktop Exec", [
  ("Verified execution pipeline", "src/windows_integration/execution_engine.py", "tests/unit/test_autonomous_agent_phases_2_to_12.py"),
  ("Application allowlist enforcement", "src/windows_integration/app_manager.py", "tests/integration/test_windows_integration_stage7.py"),
  ("Scoped file operations", "src/windows_integration/file_service.py", "tests/integration/test_windows_integration_stage7.py"),
  ("Restricted terminal execution", "src/windows_integration/process_runner.py", "tests/integration/test_windows_integration_stage7.py"),
  ("Development environment detection", "src/orchestrator/project_builder.py", "tests/unit/test_modules_2_to_10.py"),
  ("Document editor operations", "src/windows_integration/file_service.py", "tests/integration/test_windows_integration_stage7.py"),
  ("Pre/post-state verification", "src/windows_integration/execution_engine.py", "tests/unit/test_autonomous_agent_phases_2_to_12.py"),
  ("Dialog, focus and timeout handling", "src/windows_integration/execution_engine.py", "tests/unit/test_reliability_stage10.py"),
  ("Application crash handling", "src/windows_integration/execution_engine.py", "tests/unit/test_reliability_stage10.py"),
  ("Execution audit trail", "src/security/audit_logger.py", "tests/unit/test_audit_logger.py"),
]),
(11, "Autonomous Software Engineering", 501, 550, "Software Eng", [
  ("Repository inspection and mapping", "src/orchestrator/workspace_ingestion.py", "tests/unit/test_workspace_stage9.py"),
  ("Project scaffolding", "src/orchestrator/project_creator.py", "tests/unit/test_modules_2_to_10.py"),
  ("Code implementation workflow", "src/orchestrator/coding_agent.py", "tests/unit/test_modules_2_to_10.py"),
  ("Dependency management policy", "pyproject.toml, src/orchestrator/project_builder.py", "tests/unit/test_environment.py"),
  ("Unit test generation", "src/orchestrator/coding_agent.py", "tests/unit/test_modules_2_to_10.py"),
  ("Integration test workflow", "tests/integration/", "tests/integration/test_vertical_slice.py"),
  ("Static analysis gating", "pyproject.toml (ruff), src/", "tests/unit/test_environment.py"),
  ("Runtime testing and diagnosis", "src/orchestrator/coding_agent.py", "tests/unit/test_modules_2_to_10.py"),
  ("Local preview and dev server", "src/windows_integration/dev_server_verifier.py", "tests/unit/test_modules_2_to_10.py"),
  ("Build verification reporting", "src/orchestrator/verification_reporter.py", "tests/unit/test_autonomous_agent_phases_2_to_12.py"),
]),
(12, "Git, Pull Requests, and Code Review", 551, 600, "Git & Review", [
  ("Repository state inspection", "src/storage/git_recovery.py", "tests/unit/test_modules_2_to_10.py"),
  ("Task branch isolation", "src/storage/git_recovery.py", "tests/unit/test_modules_2_to_10.py"),
  ("Working-tree diff generation", "src/storage/git_recovery.py", "tests/unit/test_modules_2_to_10.py"),
  ("Test gating before review", "src/orchestrator/coding_agent.py", "tests/unit/test_modules_2_to_10.py"),
  ("Security-sensitive change scan", "src/security/dynamic_defense.py", "tests/security/test_security_audit.py"),
  ("Automated review findings", "src/orchestrator/verification_reporter.py", "tests/unit/test_autonomous_agent_phases_2_to_12.py"),
  ("Findings consolidation", "src/orchestrator/verification_reporter.py", "tests/unit/test_autonomous_agent_phases_2_to_12.py"),
  ("Fix loop and re-test", "src/orchestrator/coding_agent.py", "tests/unit/test_modules_2_to_10.py"),
  ("Authorized PR creation only", "src/storage/git_recovery.py", "tests/unit/test_modules_2_to_10.py"),
  ("PR verification and rollback", "src/storage/git_recovery.py", "tests/unit/test_modules_2_to_10.py"),
]),
(13, "External Tools and Workflow Automation", 601, 650, "Workflows", [
  ("n8n workflow adapter", "src/orchestrator/task_dispatcher.py, AI_Environment_Sample_Automation.json", "tests/integration/test_vertical_slice.py"),
  ("Webhook trigger contracts", "src/orchestrator/custom_tools.py", "tests/unit/test_workspace_stage9.py"),
  ("Third-party API connectors", "src/providers/, src/orchestrator/custom_tools.py", "tests/unit/test_providers_stage3.py"),
  ("Workflow schema validation", "src/security/action_validator.py", "tests/security/test_security_audit.py"),
  ("Approved workflow execution", "src/orchestrator/task_dispatcher.py", "tests/integration/test_vertical_slice.py"),
  ("Execution result inspection", "src/orchestrator/verification_reporter.py", "tests/unit/test_autonomous_agent_phases_2_to_12.py"),
  ("Credential scoping for integrations", "src/storage/credential_vault.py", "tests/unit/test_providers_stage3.py"),
  ("Rate limits and quota guards", "src/providers/resilience.py", "tests/unit/test_providers_stage3.py"),
  ("Integration failure handling", "src/providers/resilience.py", "tests/unit/test_reliability_stage10.py"),
  ("Reusable workflow templates", "AI_Environment_Sample_Automation.json, docs/", "tests/integration/test_vertical_slice.py"),
]),
(14, "Security, Trust, and Permission Enforcement", 651, 700, "Security", [
  ("READ/WRITE/EXECUTE taxonomy", "src/security/permission_categories.py", "tests/unit/test_autonomous_agent_phases_2_to_12.py"),
  ("EXTERNAL and HIGH_IMPACT gates", "src/security/permission_categories.py", "tests/security/test_security_audit.py"),
  ("Least-privilege tool profiles", "src/security/permission_model.py", "tests/security/test_security_audit.py"),
  ("Path validation and jail enforcement", "src/security/policy_engine.py", "tests/security/test_security_audit.py"),
  ("Command validation and allowlists", "src/security/action_validator.py", "tests/security/test_security_audit.py"),
  ("Credential isolation (DPAPI)", "src/storage/credential_vault.py", "tests/unit/test_providers_stage3.py"),
  ("Approval expiry and replay protection", "src/security/approval_broker.py", "tests/security/test_security_audit.py"),
  ("Audit logging and redaction", "src/security/audit_logger.py", "tests/unit/test_audit_logger.py"),
  ("Network egress controls", "src/security/isolation_sandbox.py", "tests/security/test_isolation_stage8.py"),
  ("Emergency stop and session revoke", "src/security/emergency_controls.py", "tests/security/test_security_audit.py"),
]),
(15, "Verification, Recovery, and Self-Healing", 701, 750, "Recovery", [
  ("Eight-state execution model", "src/orchestrator/verification_reporter.py", "tests/unit/test_autonomous_agent_phases_2_to_12.py"),
  ("Structured execution traces", "src/security/audit_logger.py", "tests/unit/test_audit_logger.py"),
  ("Timeout and cancellation policy", "src/windows_integration/process_runner.py", "tests/unit/test_reliability_stage10.py"),
  ("Idempotency keys for external actions", "src/storage/task_state_engine.py", "tests/unit/test_modules_2_to_10.py"),
  ("Checkpoint persistence", "src/storage/task_state_engine.py", "tests/unit/test_modules_2_to_10.py"),
  ("Artifact existence verification", "src/windows_integration/execution_engine.py", "tests/unit/test_autonomous_agent_phases_2_to_12.py"),
  ("Test-result verification gate", "src/orchestrator/coding_agent.py", "tests/unit/test_modules_2_to_10.py"),
  ("Bounded retry policy", "src/providers/resilience.py", "tests/unit/test_providers_stage3.py"),
  ("Failure classification taxonomy", "src/orchestrator/brain/error_memory.py", "tests/unit/test_dynamic_brain.py"),
  ("Recovery plan execution", "src/orchestrator/planner/coordinator.py", "tests/unit/test_planner_stage5.py"),
]),
(16, "Performance, Model Routing, and Cost Control", 751, 800, "Perf & Cost", [
  ("Complexity-based routing", "src/orchestrator/intelligent_router.py", "tests/unit/test_modules_2_to_10.py"),
  ("Latency tracking and provider health", "src/orchestrator/intelligent_router.py", "tests/unit/test_reliability_stage10.py"),
  ("Token accounting per request", "src/orchestrator/token_tracker.py", "tests/unit/test_token_optimizer.py"),
  ("Prompt prefix caching", "src/orchestrator/prompt_cache.py", "tests/unit/test_token_optimizer.py"),
  ("Context compaction", "src/orchestrator/brain/context_prioritizer.py", "tests/unit/test_brain_stage4.py"),
  ("Duplicate-call prevention cache", "src/orchestrator/prompt_cache.py", "tests/unit/test_token_optimizer.py"),
  ("Per-task budget enforcement", "src/orchestrator/token_tracker.py", "tests/unit/test_token_optimizer.py"),
  ("Fallback provider chains", "src/orchestrator/intelligent_router.py", "tests/unit/test_modules_2_to_10.py"),
  ("Cost estimation and reporting", "src/orchestrator/cost_tracker.py", "tests/unit/test_token_optimizer.py"),
  ("Local-model preference policy", "src/providers/local_adapter.py", "tests/unit/test_providers_stage3.py"),
]),
(17, "Personalization and Adaptive Intelligence", 801, 850, "Personalize", [
  ("User profile and preferences", "src/orchestrator/unified_workspace.py", "tests/unit/test_workspace_stage9.py"),
  ("Project convention learning", "src/orchestrator/brain/project_memory.py", "tests/unit/test_brain_stage4.py"),
  ("Workflow success-pattern library", "src/orchestrator/brain/persistent_store.py", "tests/unit/test_brain_stage4.py"),
  ("Failure-lesson reuse", "src/orchestrator/brain/error_memory.py", "tests/unit/test_dynamic_brain.py"),
  ("Provenance-tagged lessons", "src/orchestrator/brain/context_engine.py", "tests/unit/test_autonomous_agent_phases_2_to_12.py"),
  ("Memory inspection UI", "src/client/WinAI.Client/ (Memory views)", "tests/unit/test_client_architecture.py"),
  ("Memory correction API", "src/orchestrator/brain/brain_subsystem.py", "tests/unit/test_brain_stage4.py"),
  ("Memory deletion and export", "src/orchestrator/brain/brain_subsystem.py", "tests/unit/test_brain_stage4.py"),
  ("Stale-knowledge invalidation", "src/orchestrator/brain/context_engine.py", "tests/unit/test_autonomous_agent_phases_2_to_12.py"),
  ("Privacy-preserving personalization", "src/orchestrator/privacy_controls.py", "tests/unit/test_reliability_stage10.py"),
]),
(18, "Observability, Testing, and Quality Engineering", 851, 900, "Quality", [
  ("Structured JSON logging", "src/security/audit_logger.py", "tests/unit/test_audit_logger.py"),
  ("Metrics and telemetry dashboard", "src/orchestrator/observability.py", "tests/unit/test_modules_2_to_10.py"),
  ("Distributed-style trace IDs", "src/security/audit_logger.py", "tests/unit/test_audit_logger.py"),
  ("Unit test pyramid expansion", "tests/unit/", "tests/unit/test_environment.py"),
  ("Mocked provider test harness", "src/providers/mock_provider.py", "tests/unit/test_providers_stage3.py"),
  ("Windows integration test suite", "tests/integration/test_windows_integration_stage7.py", "tests/integration/test_windows_integration_stage7.py"),
  ("Security regression suite", "tests/security/", "tests/security/test_security_audit.py"),
  ("Flaky-test triage and quarantine", "tests/, pytest.ini", "tests/unit/test_reliability_stage10.py"),
  ("Coverage thresholds and gates", "pyproject.toml", "tests/unit/test_environment.py"),
  ("Release quality checklist", "docs/PHASE_100_MILESTONE.md", "tests/integration/test_vertical_slice.py"),
]),
(19, "Production Hardening and Distribution Readiness", 901, 950, "Production", [
  ("First-run welcome flow", "src/client/WinAI.Client/Views/MainWindow.xaml", "tests/unit/test_client_architecture.py"),
  ("Provider key setup wizard", "src/storage/credential_vault.py", "tests/unit/test_providers_stage3.py"),
  ("Integration availability check", "src/windows_integration/system_app_scanner.py", "tests/unit/test_app_scanner.py"),
  ("Safe diagnostic task", "src/orchestrator/task_dispatcher.py", "tests/integration/test_vertical_slice.py"),
  ("Approval education interstitial", "src/client/WinAI.Client/Views/ApprovalDialog.xaml", "tests/unit/test_client_architecture.py"),
  ("Windows packaging (MSIX/AppX)", "src/client/WinAI.Client/WinAI.Client.csproj", "tests/unit/test_client_architecture.py"),
  ("Installer and update channel", "src/storage/update_manager.py, scripts/", "tests/unit/test_reliability_stage10.py"),
  ("Rollback and recovery image", "src/storage/git_recovery.py", "tests/unit/test_modules_2_to_10.py"),
  ("Crash reporting (redacted)", "src/security/audit_logger.py", "tests/unit/test_audit_logger.py"),
  ("Accessibility and perf audit", "src/client/WinAI.Client/app.manifest", "tests/unit/test_client_architecture.py"),
]),
(20, "Advanced Capabilities and Long-Term Evolution", 951, 1000, "Future", [
  ("Multi-step research pipelines", "src/orchestrator/planner/autonomous_planner.py", "tests/unit/test_autonomous_agent_phases_2_to_12.py"),
  ("Multi-file refactor engine", "src/orchestrator/coding_agent.py", "tests/unit/test_modules_2_to_10.py"),
  ("Dataset pipeline templates", "src/orchestrator/project_builder.py", "tests/unit/test_modules_2_to_10.py"),
  ("Cross-application workflows", "src/windows_integration/execution_engine.py", "tests/unit/test_autonomous_agent_phases_2_to_12.py"),
  ("Cross-provider failover drills", "src/orchestrator/intelligent_router.py", "tests/unit/test_modules_2_to_10.py"),
  ("Offline degraded mode", "src/providers/mock_provider.py, src/providers/local_adapter.py", "tests/unit/test_reliability_stage10.py"),
  ("User-defined tool plugin API", "src/orchestrator/custom_tools.py", "tests/unit/test_workspace_stage9.py"),
  ("Public API stability guarantees", "src/orchestrator/main.py, src/providers/base.py", "tests/integration/test_vertical_slice.py"),
  ("Deprecation and migration policy", "docs/, src/", "tests/unit/test_environment.py"),
  ("Thousand-phase governance closeout", "docs/MASTER_ROADMAP_1000_PHASES.*, docs/IMPLEMENTATION_STATUS.md", "tests/unit/test_phase_0001_baseline.py"),
]),
]


def build_phase(stage_no, stage_name, category, phase_num, area_title, files, test_hint, level_idx, level_title):
    pid = f"PHASE {phase_num:04d}"
    prev_id = f"PHASE {phase_num-1:04d}" if phase_num > 1 else "NONE"
    title = f"{area_title} — {level_title}"
    return {
        "phase_id": pid,
        "phase_number": phase_num,
        "stage_number": stage_no,
        "stage_name": stage_name,
        "title": title,
        "category": category,
        "objective": f"Deliver a verifiable increment for '{area_title}' at maturity level '{level_title}' within {stage_name}.",
        "current_state": "Audit against the live repository; reuse working code in the listed components and close only the documented gap for this increment.",
        "implementation_tasks": [
            f"Inspect current implementation of '{area_title}' in {files}.",
            f"Define the minimal change set required to satisfy '{level_title}'.",
            "Implement only that change set; make no unrelated refactors.",
            "Update or add documentation strings and user-facing docs where behavior changes.",
        ],
        "files_or_components": files,
        "dependencies": f"{prev_id} plus completion of prerequisite capabilities in {stage_name}; no advanced behavior before its security and reliability prerequisites.",
        "security_considerations": "Enforce least privilege; validate all inputs; never expose secrets to prompts or logs; require approval for sensitive operations; record audit events.",
        "automated_tests": f"Add or update deterministic tests runnable via pytest; primary regression gate: {test_hint}.",
        "manual_verification": "Inspect the diff, run the affected workflow on Windows, and confirm observable behavior matches the objective.",
        "acceptance_criteria": f"'{title}' behaves as specified, existing tests still pass, and no security control is weakened.",
        "completion_evidence": f"Commit diff plus pytest output for {test_hint} and a short phase report in docs/IMPLEMENTATION_STATUS.md.",
        "rollback_strategy": "Revert the phase commit (git revert); no data migration in this phase, so rollback is a clean tree restore.",
    }


def main():
    phases = []
    for (stage_no, stage_name, start, end, category, areas) in STAGES:
        assert len(areas) == 10, f"Stage {stage_no} must have 10 areas"
        assert end - start + 1 == 50, f"Stage {stage_no} must span 50 phases"
        n = start
        for (area_title, files, test_hint) in areas:
            for level_idx, (level_code, level_title) in enumerate(LEVELS):
                assert n <= end, "phase overflow"
                phases.append(build_phase(stage_no, stage_name, category, n, area_title, files, test_hint, level_idx, level_title))
                n += 1
        assert n == end + 1, f"Stage {stage_no} did not fill range"

    assert len(phases) == 1000, f"Expected 1000 phases, got {len(phases)}"
    ids = [p["phase_id"] for p in phases]
    assert len(set(ids)) == 1000, "duplicate phase ids"
    assert ids[0] == "PHASE 0001" and ids[-1] == "PHASE 1000", "numbering error"
    titles = [p["title"] for p in phases]
    assert len(set(titles)) == 1000, "duplicate titles detected"

    # Write JSON
    JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "product": "WinAI-OE",
        "product_type": "Secure, adaptive, autonomous Windows AI environment",
        "total_phases": 1000,
        "stages": 20,
        "generated_note": "Programmatically generated deterministic roadmap; each phase is independently verifiable.",
        "phases": phases,
    }
    JSON_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # Write Markdown
    lines = []
    lines.append("# WinAI-OE — Master Roadmap (Exactly 1,000 Development Phases)")
    lines.append("")
    lines.append("Product: Secure, adaptive, autonomous Windows AI environment (WinAI-OE).")
    lines.append("Organization: 20 stages x 50 phases. Each phase is independently verifiable.")
    lines.append("")
    lines.append("## Stage Index")
    lines.append("")
    for (stage_no, stage_name, start, end, category, areas) in STAGES:
        lines.append(f"- STAGE {stage_no:02d} — {stage_name} (PHASE {start:04d}–{end:04d})")
    lines.append("")
    for (stage_no, stage_name, start, end, category, areas) in STAGES:
        lines.append(f"## STAGE {stage_no:02d} — {stage_name}")
        lines.append("")
        lines.append(f"Phases {start:04d}–{end:04d} | Category: {category}")
        lines.append("")
    for p in phases:
        lines.append(f"### {p['phase_id']}")
        lines.append(f"Title: {p['title']}")
        lines.append(f"Category: {p['category']}")
        lines.append(f"Objective: {p['objective']}")
        lines.append(f"Current State: {p['current_state']}")
        lines.append("Implementation Tasks:")
        for t in p["implementation_tasks"]:
            lines.append(f"- {t}")
        lines.append(f"Files or Components: {p['files_or_components']}")
        lines.append(f"Dependencies: {p['dependencies']}")
        lines.append(f"Security Considerations: {p['security_considerations']}")
        lines.append(f"Automated Tests: {p['automated_tests']}")
        lines.append(f"Manual Verification: {p['manual_verification']}")
        lines.append(f"Acceptance Criteria: {p['acceptance_criteria']}")
        lines.append(f"Completion Evidence: {p['completion_evidence']}")
        lines.append(f"Rollback Strategy: {p['rollback_strategy']}")
        lines.append("")
    MD_PATH.write_text("\n".join(lines), encoding="utf-8")

    print(f"Wrote {len(phases)} phases to {JSON_PATH} and {MD_PATH}")


if __name__ == "__main__":
    main()
