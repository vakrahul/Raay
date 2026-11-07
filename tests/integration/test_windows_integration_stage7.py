"""Integration tests for Stage VII: Controlled Windows Integration (Phases 61-70)."""
from pathlib import Path
import sys
import pytest

from src.windows_integration.app_manager import AppManager, ApprovedApp
from src.windows_integration.file_service import ScopedFileService
from src.windows_integration.process_runner import RestrictedProcessRunner
from src.windows_integration.uia_service import UIAutomationService


@pytest.mark.integration
def test_app_manager_discovery_and_policy():
    """Verify application whitelist discovery and rejection of unapproved binaries."""
    manager = AppManager()
    approved = manager.list_approved_apps()
    assert len(approved) >= 2
    app_ids = [a.app_id for a in approved]
    assert "notepad" in app_ids
    assert "calc" in app_ids

    # Unauthorized app launch attempt
    with pytest.raises(PermissionError) as excinfo:
        manager.launch_app("unauthorized_malware_app")
    assert "not in the approved whitelist" in str(excinfo.value)


@pytest.mark.integration
def test_uia_accessible_tree_and_actions():
    """Verify accessible element discovery and control pattern actions using real Windows Notepad."""
    import subprocess
    import time

    proc = subprocess.Popen(["notepad.exe"])
    time.sleep(1.5)
    try:
        uia = UIAutomationService()
        win = uia.find_window("Notepad", timeout_seconds=4.0)
        if win is None and (subprocess.os.environ.get("CI") or subprocess.os.environ.get("GITHUB_ACTIONS")):
            pytest.skip("Interactive GUI window not available in headless CI session")
        assert win is not None, "Real Notepad window was not found on desktop"

        elements = uia.inspect_window_elements("Notepad", max_depth=3)
        assert len(elements) > 0, "No accessible elements enumerated from real Notepad"

        # Set and read text via real UIA ValuePattern / SendKeys
        success = uia.set_text_value("Notepad", "Hello via UI Automation")
        assert success is True
        val = uia.read_text_value("Notepad")
        assert val is not None
        assert "Hello via UI Automation" in val
    finally:
        proc.terminate()
        time.sleep(0.5)


@pytest.mark.integration
def test_scoped_file_service_backup_and_rollback(temp_workspace: Path):
    """Verify atomic backup and rollback capability on file modifications."""
    file_service = ScopedFileService(workspace_root=temp_workspace)

    # 1. Initial write
    rel_file = "config/app_settings.json"
    file_service.write_file(rel_file, '{"version": 1}')
    assert file_service.read_file(rel_file) == '{"version": 1}'

    # 2. Modify file (triggers automatic backup)
    backup_id = file_service.write_file(rel_file, '{"version": 2, "corrupt": true}')
    assert file_service.read_file(rel_file) == '{"version": 2, "corrupt": true}'
    assert backup_id in file_service._backups

    # 3. Rollback
    restored = file_service.rollback(backup_id)
    assert restored is True
    assert file_service.read_file(rel_file) == '{"version": 1}'

    # 4. Path traversal attempt
    with pytest.raises(PermissionError):
        file_service.read_file("../../../secret_system_file.txt")


@pytest.mark.asyncio
async def test_restricted_process_runner_scrubbed_env(temp_workspace: Path, monkeypatch):
    """Verify subprocess executes cleanly with scrubbed sensitive environment variables."""
    runner = RestrictedProcessRunner(workspace_root=temp_workspace, default_timeout_seconds=5)

    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-secret-leaked-key")
    monkeypatch.setenv("GNANI_API_KEY", "vach_dummy_test_key_0123456789abcdef0123456789")
    monkeypatch.setenv("WINAI_IPC_TOKEN", "super-secret-token")
    monkeypatch.setenv("NORMAL_USER_VAR", "public_value")

    # Command prints environment variables
    code = (
        "import os; "
        "print('KEY_EXISTS:', 'OPENAI_API_KEY' in os.environ); "
        "print('GNANI_EXISTS:', 'GNANI_API_KEY' in os.environ); "
        "print('NORMAL_EXISTS:', 'NORMAL_USER_VAR' in os.environ)"
    )
    res = await runner.run_command([sys.executable, "-c", code])
    assert res.exit_code == 0
    assert "KEY_EXISTS: False" in res.stdout
    assert "GNANI_EXISTS: False" in res.stdout
    assert "NORMAL_EXISTS: True" in res.stdout


@pytest.mark.asyncio
async def test_restricted_process_runner_timeout(temp_workspace: Path):
    """Verify process runner terminates commands exceeding timeout."""
    runner = RestrictedProcessRunner(workspace_root=temp_workspace, default_timeout_seconds=1)

    # Command sleeps for 5 seconds
    res = await runner.run_command(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        timeout_seconds=1,
    )
    assert res.timed_out is True
    assert "timed out" in res.stderr.lower()
