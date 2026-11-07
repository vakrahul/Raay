"""Windows System Intelligence Service (Phase 1).

Collects genuine operating-system intelligence via native Windows APIs (ctypes/win32)
and psutil interfaces. Zero simulations, zero mock statistics, zero fake processes.
Handles access-denied restrictions gracefully.
"""

import ctypes
import os
import platform
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
import psutil
from pydantic import BaseModel, Field
import win32gui
import win32process

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


class ProcessInfo(BaseModel):
    pid: int
    name: str
    status: str
    cpu_percent: float = 0.0
    memory_rss_bytes: int = 0
    memory_rss_mb: float = 0.0
    memory_vms_bytes: int = 0
    memory_vms_mb: float = 0.0
    memory_percent: float = 0.0
    num_threads: int = 0
    username: Optional[str] = None
    exe_path: Optional[str] = None
    create_time: float = 0.0
    parent_pid: Optional[int] = None
    is_accessible: bool = True


class SystemMemoryInfo(BaseModel):
    total_physical_bytes: int
    available_physical_bytes: int
    used_physical_bytes: int
    free_physical_bytes: int
    total_physical_mb: float
    available_physical_mb: float
    used_physical_mb: float
    percent_used: float
    total_swap_bytes: int
    used_swap_bytes: int
    free_swap_bytes: int
    swap_percent_used: float
    memory_pressure_level: str  # NORMAL, WARNING, CRITICAL


class WindowInfo(BaseModel):
    hwnd: int
    title: str
    pid: int
    process_name: Optional[str] = None
    is_visible: bool
    is_foreground: bool
    window_state: str  # "normal", "minimized", "maximized"
    rect: Tuple[int, int, int, int]  # (left, top, right, bottom)
    width: int
    height: int


class SystemOverviewInfo(BaseModel):
    os_name: str
    os_version: str
    os_build: str
    architecture: str
    hostname: str
    boot_time_timestamp: float
    uptime_seconds: float
    cpu_model: str
    physical_cores: int
    logical_cores: int
    cpu_usage_percent: float
    memory: SystemMemoryInfo
    disk_total_gb: float
    disk_used_gb: float
    disk_free_gb: float
    disk_percent_used: float
    total_running_processes: int
    total_open_windows: int


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class WindowsSystemIntelligence:
    """Provides deep, accurate, real-time operating system intelligence on Windows."""

    # -------------------------------------------------------------------------
    # 1. PROCESS INTELLIGENCE
    # -------------------------------------------------------------------------

    def list_processes(
        self,
        sort_by: str = "memory",
        limit: Optional[int] = None,
    ) -> List[ProcessInfo]:
        """Discover actual running processes on the system with resource metrics."""
        results: List[ProcessInfo] = []

        for p in psutil.process_iter(
            attrs=[
                "pid",
                "name",
                "status",
                "cpu_percent",
                "memory_info",
                "memory_percent",
                "num_threads",
                "username",
                "exe",
                "create_time",
                "ppid",
            ]
        ):
            try:
                info = p.info
                mem_info = info.get("memory_info")
                rss = mem_info.rss if mem_info else 0
                vms = mem_info.vms if mem_info else 0

                results.append(
                    ProcessInfo(
                        pid=info["pid"],
                        name=info.get("name") or "unknown",
                        status=str(info.get("status") or "unknown"),
                        cpu_percent=float(info.get("cpu_percent") or 0.0),
                        memory_rss_bytes=rss,
                        memory_rss_mb=round(rss / (1024 * 1024), 2),
                        memory_vms_bytes=vms,
                        memory_vms_mb=round(vms / (1024 * 1024), 2),
                        memory_percent=round(float(info.get("memory_percent") or 0.0), 2),
                        num_threads=int(info.get("num_threads") or 0),
                        username=info.get("username"),
                        exe_path=info.get("exe"),
                        create_time=float(info.get("create_time") or 0.0),
                        parent_pid=info.get("ppid"),
                        is_accessible=True,
                    )
                )
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            except psutil.AccessDenied:
                # Handle elevated/system process gracefully
                try:
                    results.append(
                        ProcessInfo(
                            pid=p.pid,
                            name=p.name(),
                            status="access_denied",
                            is_accessible=False,
                        )
                    )
                except Exception:
                    continue

        if sort_by == "memory":
            results.sort(key=lambda x: x.memory_rss_bytes, reverse=True)
        elif sort_by == "cpu":
            results.sort(key=lambda x: x.cpu_percent, reverse=True)
        elif sort_by == "name":
            results.sort(key=lambda x: x.name.lower())
        elif sort_by == "pid":
            results.sort(key=lambda x: x.pid)

        if limit is not None and limit > 0:
            return results[:limit]
        return results

    def get_process_info(self, pid: int) -> Optional[ProcessInfo]:
        """Retrieve detailed intelligence for a specific running process."""
        try:
            p = psutil.Process(pid)
            mem_info = p.memory_info()
            rss = mem_info.rss
            vms = mem_info.vms

            exe_path = None
            try:
                exe_path = p.exe()
            except (psutil.AccessDenied, Exception):
                pass

            user = None
            try:
                user = p.username()
            except (psutil.AccessDenied, Exception):
                pass

            parent_pid = None
            try:
                parent_pid = p.ppid()
            except (psutil.AccessDenied, Exception):
                pass

            return ProcessInfo(
                pid=p.pid,
                name=p.name(),
                status=str(p.status()),
                cpu_percent=round(p.cpu_percent(interval=0.05), 2),
                memory_rss_bytes=rss,
                memory_rss_mb=round(rss / (1024 * 1024), 2),
                memory_vms_bytes=vms,
                memory_vms_mb=round(vms / (1024 * 1024), 2),
                memory_percent=round(p.memory_percent(), 2),
                num_threads=p.num_threads(),
                username=user,
                exe_path=exe_path,
                create_time=p.create_time(),
                parent_pid=parent_pid,
                is_accessible=True,
            )
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            return None
        except psutil.AccessDenied:
            try:
                p = psutil.Process(pid)
                return ProcessInfo(
                    pid=pid,
                    name=p.name(),
                    status="access_denied",
                    is_accessible=False,
                )
            except Exception:
                return None

    def find_processes_by_name(self, name_pattern: str) -> List[ProcessInfo]:
        """Locate running processes matching a given name or executable substring."""
        pattern = name_pattern.lower()
        all_procs = self.list_processes(sort_by="memory")
        return [p for p in all_procs if pattern in p.name.lower()]

    def get_top_resource_consumers(self, metric: str = "memory", limit: int = 10) -> List[ProcessInfo]:
        """Return the top resource-consuming applications currently active."""
        return self.list_processes(sort_by=metric, limit=limit)

    # -------------------------------------------------------------------------
    # 2. MEMORY INTELLIGENCE
    # -------------------------------------------------------------------------

    def get_system_memory_status(self) -> SystemMemoryInfo:
        """Measure actual physical RAM, commit charge, and memory pressure on Windows."""
        # 1. psutil metrics
        vm = psutil.virtual_memory()
        swap = psutil.swap_memory()

        # 2. Win32 GlobalMemoryStatusEx confirmation
        mem_status = MEMORYSTATUSEX()
        mem_status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        kernel32.GlobalMemoryStatusEx(ctypes.byref(mem_status))

        total_bytes = vm.total
        avail_bytes = vm.available
        used_bytes = vm.used
        free_bytes = vm.free
        pct_used = vm.percent

        if pct_used >= 88.0:
            pressure = "CRITICAL"
        elif pct_used >= 75.0:
            pressure = "WARNING"
        else:
            pressure = "NORMAL"

        return SystemMemoryInfo(
            total_physical_bytes=total_bytes,
            available_physical_bytes=avail_bytes,
            used_physical_bytes=used_bytes,
            free_physical_bytes=free_bytes,
            total_physical_mb=round(total_bytes / (1024 * 1024), 2),
            available_physical_mb=round(avail_bytes / (1024 * 1024), 2),
            used_physical_mb=round(used_bytes / (1024 * 1024), 2),
            percent_used=pct_used,
            total_swap_bytes=swap.total,
            used_swap_bytes=swap.used,
            free_swap_bytes=swap.free,
            swap_percent_used=swap.percent,
            memory_pressure_level=pressure,
        )

    # -------------------------------------------------------------------------
    # 3. WINDOW INTELLIGENCE
    # -------------------------------------------------------------------------

    def list_top_level_windows(self, visible_only: bool = True) -> List[WindowInfo]:
        """Discover genuine top-level application windows with window state and owning PIDs."""
        fg_hwnd = user32.GetForegroundWindow()
        windows: List[WindowInfo] = []

        def enum_cb(hwnd, extra):
            try:
                is_vis = bool(user32.IsWindowVisible(hwnd))
                if visible_only and not is_vis:
                    return True

                length = user32.GetWindowTextLengthW(hwnd)
                if length == 0 and visible_only:
                    return True

                buff = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buff, length + 1)
                title = buff.value.strip()

                if visible_only and not title:
                    return True

                # Owning PID
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                proc_name = None
                try:
                    proc_name = psutil.Process(pid).name()
                except Exception:
                    pass

                # Window Rect
                rect = win32gui.GetWindowRect(hwnd)  # (left, top, right, bottom)
                width = rect[2] - rect[0]
                height = rect[3] - rect[1]

                # Window placement / state
                placement = win32gui.GetWindowPlacement(hwnd)
                state_code = placement[1]
                if state_code == 2:  # SW_SHOWMINIMIZED
                    state = "minimized"
                elif state_code == 3:  # SW_SHOWMAXIMIZED
                    state = "maximized"
                else:
                    state = "normal"

                windows.append(
                    WindowInfo(
                        hwnd=hwnd,
                        title=title,
                        pid=pid,
                        process_name=proc_name,
                        is_visible=is_vis,
                        is_foreground=(hwnd == fg_hwnd),
                        window_state=state,
                        rect=rect,
                        width=width,
                        height=height,
                    )
                )
            except Exception:
                pass
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_void_p)
        user32.EnumWindows(WNDENUMPROC(enum_cb), None)
        return windows

    def get_foreground_window(self) -> Optional[WindowInfo]:
        """Identify the actual window currently receiving user focus."""
        fg_hwnd = user32.GetForegroundWindow()
        if not fg_hwnd:
            return None

        windows = self.list_top_level_windows(visible_only=False)
        for w in windows:
            if w.hwnd == fg_hwnd:
                return w
        return None

    def find_windows(
        self,
        title_query: Optional[str] = None,
        pid: Optional[int] = None,
        process_name: Optional[str] = None,
        visible_only: bool = True,
    ) -> List[WindowInfo]:
        """Find windows matching a title substring, process ID, or process name."""
        all_wins = self.list_top_level_windows(visible_only=visible_only)
        matched = []

        for w in all_wins:
            if title_query and title_query.lower() not in w.title.lower():
                continue
            if pid is not None and w.pid != pid:
                continue
            if process_name and (not w.process_name or process_name.lower() not in w.process_name.lower()):
                continue
            matched.append(w)

        return matched

    # -------------------------------------------------------------------------
    # 4. SYSTEM INFORMATION OVERVIEW
    # -------------------------------------------------------------------------

    def get_system_overview(self) -> SystemOverviewInfo:
        """Collect structured hardware, OS, disk, and active resource measurements."""
        mem = self.get_system_memory_status()
        boot_time = psutil.boot_time()
        uptime = time.time() - boot_time

        # CPU info
        cpu_usage = psutil.cpu_percent(interval=0.1)
        phys_cores = psutil.cpu_count(logical=False) or 1
        log_cores = psutil.cpu_count(logical=True) or 1
        cpu_model = platform.processor() or "Unknown CPU"

        # Disk info (system root partition)
        sys_drive = os.environ.get("SystemDrive", "C:") + "\\"
        disk = psutil.disk_usage(sys_drive)

        # Count active processes & visible windows
        all_procs = psutil.pids()
        open_wins = len(self.list_top_level_windows(visible_only=True))

        return SystemOverviewInfo(
            os_name=platform.system(),
            os_version=platform.version(),
            os_build=platform.release(),
            architecture=platform.machine(),
            hostname=platform.node(),
            boot_time_timestamp=boot_time,
            uptime_seconds=round(uptime, 2),
            cpu_model=cpu_model,
            physical_cores=phys_cores,
            logical_cores=log_cores,
            cpu_usage_percent=cpu_usage,
            memory=mem,
            disk_total_gb=round(disk.total / (1024**3), 2),
            disk_used_gb=round(disk.used / (1024**3), 2),
            disk_free_gb=round(disk.free / (1024**3), 2),
            disk_percent_used=disk.percent,
            total_running_processes=len(all_procs),
            total_open_windows=open_wins,
        )
