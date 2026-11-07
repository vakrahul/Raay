"""PHASE 0041: windows/security/storage modules expose their contracts."""
import importlib

MODULES = (
    "src.windows_integration.app_manager",
    "src.windows_integration.file_service",
    "src.windows_integration.process_runner",
    "src.windows_integration.browser_session_manager",
    "src.windows_integration.execution_engine",
    "src.security.policy_engine",
    "src.security.action_validator",
    "src.security.approval_broker",
    "src.security.dynamic_defense",
    "src.security.audit_logger",
    "src.storage.credential_vault",
    "src.storage.task_state_engine",
    "src.storage.git_recovery",
)


def test_security_surface_modules_importable():
    for name in MODULES:
        assert importlib.import_module(name) is not None


def test_core_contracts_present():
    from src.security.policy_engine import PolicyDecision, SecurityPolicyEngine
    from src.windows_integration.file_service import ScopedFileService

    assert {d.value for d in PolicyDecision} >= {"ALLOW", "DENY", "REQUIRE_APPROVAL"}
    assert hasattr(ScopedFileService, "rollback")
