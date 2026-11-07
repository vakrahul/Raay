"""Real Windows OS Intelligence & Control Integration Tests (Phases 1-9).

These tests run against the live Windows operating system:
- Real Process Intelligence (psutil + native Win32 process handles)
- Real Memory Intelligence (GlobalMemoryStatusEx + virtual/swap memory)
- Real Window Intelligence (EnumWindows + window placement + foreground tracking)
- Real System Overview (CPU hardware, cores, disk partitions, boot uptime)
- Process Safeguards (Strict protection of critical system services like System, csrss, explorer)
- OS-Aware Context Tracking (Volatile state refresh, PID/HWND validity, resource guard)
- Task Dispatcher Integration (Real memory queries, top consumer tables, window enumeration)
Zero mocks. Zero remote vision calls. Zero simulations.
"""

from pathlib import Path
import platform
import subprocess
import time
import pytest

from src.orchestrator.task_dispatcher import AutonomousTaskDispatcher
from src.windows_integration.os_context import OSContextTracker
from src.windows_integration.process_manager import WindowsProcessManager
from src.windows_integration.system_intelligence import (
    ProcessInfo,
    SystemMemoryInfo,
    SystemOverviewInfo,
    WindowInfo,
    WindowsSystemIntelligence,
)


@pytest.fixture(scope="module")
def intelligence() -> WindowsSystemIntelligence:
    return WindowsSystemIntelligence()


@pytest.fixture(scope="module")
def process_mgr() -> WindowsProcessManager:
    return WindowsProcessManager()


@pytest.fixture
def context_tracker(intelligence) -> OSContextTracker:
    return OSContextTracker(intelligence=intelligence)


# -----------------------------------------------------------------------------
# 1. PROCESS INTELLIGENCE TESTS
# -----------------------------------------------------------------------------
@pytest.mark.integration
def test_real_process_enumeration(intelligence: WindowsSystemIntelligence):
    """Verify live process discovery on Windows with genuine memory and status."""
    procs = intelligence.list_processes(sort_by="memory", limit=25)
    assert len(procs) > 0, "No running processes enumerated on Windows"

    top = procs[0]
    assert top.pid > 0
    assert len(top.name) > 0
    assert top.memory_rss_bytes > 0
    assert top.memory_rss_mb > 0.0

    # Specific process lookup
    self_info = intelligence.get_process_info(subprocess.os.getpid())
    assert self_info is not None
    assert self_info.pid == subprocess.os.getpid()
    assert "python" in self_info.name.lower()
    assert self_info.is_accessible is True


@pytest.mark.integration
def test_real_top_resource_consumers(intelligence: WindowsSystemIntelligence):
    """Verify ranking of top memory and CPU consumers on live operating system."""
    top_mem = intelligence.get_top_resource_consumers(metric="memory", limit=5)
    assert len(top_mem) == 5
    # Verify sorted descending by memory
    for i in range(len(top_mem) - 1):
        assert top_mem[i].memory_rss_bytes >= top_mem[i + 1].memory_rss_bytes

    top_cpu = intelligence.get_top_resource_consumers(metric="cpu", limit=5)
    assert len(top_cpu) == 5


@pytest.mark.integration
def test_real_process_search_and_access_denied(intelligence: WindowsSystemIntelligence):
    """Verify pattern searching and graceful handling of elevated/system processes."""
    # Find python instances
    matched = intelligence.find_processes_by_name("python")
    assert any(p.pid == subprocess.os.getpid() for p in matched)

    # PID 4 (System kernel process) - should be handled safely without crashing
    kernel_proc = intelligence.get_process_info(4)
    if kernel_proc:
        assert kernel_proc.pid == 4
        assert kernel_proc.name.lower() == "system"


# -----------------------------------------------------------------------------
# 2. MEMORY INTELLIGENCE TESTS
# -----------------------------------------------------------------------------
@pytest.mark.integration
def test_real_memory_status_measurements(intelligence: WindowsSystemIntelligence):
    """Verify real physical RAM measurements and memory pressure calculation."""
    mem = intelligence.get_system_memory_status()
    assert mem.total_physical_bytes > 0
    assert mem.total_physical_mb > 1024.0  # At least 1GB physical RAM
    assert mem.available_physical_bytes > 0
    assert 0.0 <= mem.percent_used <= 100.0
    assert mem.memory_pressure_level in ["NORMAL", "WARNING", "CRITICAL"]


# -----------------------------------------------------------------------------
# 3. WINDOW INTELLIGENCE TESTS
# -----------------------------------------------------------------------------
@pytest.mark.integration
def test_real_window_enumeration_and_foreground(intelligence: WindowsSystemIntelligence):
    """Verify discovery of actual top-level desktop windows and foreground tracking."""
    wins = intelligence.list_top_level_windows(visible_only=True)
    assert len(wins) > 0, "No top-level windows discovered on desktop"

    for w in wins:
        assert w.hwnd > 0
        assert len(w.title) > 0
        assert w.pid > 0
        assert w.window_state in ["normal", "minimized", "maximized"]

    # Foreground window
    fg = intelligence.get_foreground_window()
    if fg is None and (subprocess.os.environ.get("CI") or subprocess.os.environ.get("GITHUB_ACTIONS")):
        pytest.skip("Foreground window not available in headless CI environment")
    assert fg is not None
    assert fg.hwnd > 0
    assert fg.is_foreground is True


# -----------------------------------------------------------------------------
# 4. SYSTEM INFORMATION OVERVIEW TESTS
# -----------------------------------------------------------------------------
@pytest.mark.integration
def test_real_system_overview_metrics(intelligence: WindowsSystemIntelligence):
    """Verify collection of genuine hardware, platform, and disk utilization data."""
    overview = intelligence.get_system_overview()
    assert overview.os_name.lower() == "windows"
    assert overview.logical_cores >= 1
    assert overview.physical_cores >= 1
    assert overview.disk_total_gb > 0.0
    assert overview.disk_free_gb > 0.0
    assert overview.uptime_seconds > 0.0
    assert overview.total_running_processes > 10


# -----------------------------------------------------------------------------
# 5. PROCESS MANAGER SAFEGUARDS
# -----------------------------------------------------------------------------
@pytest.mark.integration
def test_process_manager_safeguards_critical_processes(process_mgr: WindowsProcessManager):
    """Verify that critical Windows system processes (System, csrss, explorer) cannot be killed."""
    # 1. PID 4 (System)
    prot, msg = process_mgr.is_protected(4)
    assert prot is True
    assert "core Windows kernel" in msg

    close_res = process_mgr.close_process(pid=4, force=True)
    assert close_res.success is False
    assert close_res.method == "PROTECTED"

    # 2. Explorer.exe protection
    explorer_pids = process_mgr.find_pids_by_name("explorer.exe")
    if explorer_pids:
        exp_pid = explorer_pids[0]
        prot_exp, _ = process_mgr.is_protected(exp_pid)
        assert prot_exp is True


@pytest.mark.integration
def test_process_manager_close_application(process_mgr: WindowsProcessManager):
    """Verify launching a test application and closing it via process manager safeguards."""
    proc = subprocess.Popen(["notepad.exe"])
    time.sleep(1.5)
    try:
        pids = process_mgr.find_pids_by_name("notepad")
        assert len(pids) > 0, "No Notepad processes found running"

        # Close running instance
        res = process_mgr.close_application_by_name("notepad", force=True)
        assert len(res) > 0
        assert any(r.success for r in res), f"Failed to close Notepad: {[r.message for r in res]}"
    finally:
        if proc.poll() is None:
            proc.kill()


# -----------------------------------------------------------------------------
# 6. OS CONTEXT TRACKER TESTS
# -----------------------------------------------------------------------------
@pytest.mark.integration
def test_os_context_tracker_lifecycle(context_tracker: OSContextTracker):
    """Verify volatile state tracking, PID validation, and resource pre-condition checks."""
    import sys
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
    time.sleep(0.5)
    pid = proc.pid
    try:
        context_tracker.register_launch("test_app", pid)
        summary_before = context_tracker.get_context_summary()
        assert any(p["pid"] == pid for p in summary_before["tracked_processes"])

        # Resource check allows
        ok, msg = context_tracker.check_resource_preconditions("test_app")
        assert ok is True

        # Terminate process to test volatile state refresh
        proc.terminate()
        proc.wait(timeout=2.0)

        # Refresh must detect dead process
        refresh = context_tracker.refresh_volatile_state()
        assert refresh["dead_processes_detected"] >= 1
        summary_after = context_tracker.get_context_summary()
        assert not any(p["pid"] == pid for p in summary_after["tracked_processes"])
    finally:
        if proc.poll() is None:
            proc.kill()


# -----------------------------------------------------------------------------
# 7. TASK DISPATCHER OS INTELLIGENCE QUERIES
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.integration
async def test_task_dispatcher_os_queries(temp_workspace: Path):
    """Verify that natural-language OS queries run through dispatcher without screenshots."""
    dispatcher = AutonomousTaskDispatcher(workspace_root=temp_workspace)

    # 1. Memory consumers
    r1 = await dispatcher.execute_task("Show me which applications are consuming the most memory")
    assert r1.status == "COMPLETED"
    assert r1.action_type == "process_intelligence"
    assert "Top 10 Windows Applications" in r1.summary
    assert len(r1.details.get("processes", [])) > 0

    # 2. RAM status
    r2 = await dispatcher.execute_task("What is my RAM status and memory pressure?")
    assert r2.status == "COMPLETED"
    assert r2.action_type == "system_intelligence"
    assert "Windows Physical RAM" in r2.summary
    assert r2.details.get("total_physical_mb", 0) > 0

    # 3. Open windows
    r3 = await dispatcher.execute_task("List all open windows on my desktop")
    assert r3.status == "COMPLETED"
    assert r3.action_type == "window_intelligence"
    assert "Active Top-Level Windows" in r3.summary
    if subprocess.os.environ.get("CI") or subprocess.os.environ.get("GITHUB_ACTIONS"):
        assert r3.details.get("window_count", 0) >= 0
    else:
        assert r3.details.get("window_count", 0) > 0
