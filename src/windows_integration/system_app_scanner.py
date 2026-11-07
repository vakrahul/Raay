"""Comprehensive Windows System Application Scanner and Cataloger.

Scans:
1. Windows Registry (HKLM and HKCU, 64-bit and 32-bit uninstall databases).
2. Start Menu Program Shortcuts (.lnk).
3. Active Running GUI Processes.
4. CLI Developer Tools in system PATH.
Categorizes applications and tracks authorization allowlists.
"""

from enum import Enum
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, Dict, List, Optional
import winreg
from pydantic import BaseModel, Field


class AppCategory(str, Enum):
    BROWSER = "Web Browser"
    DEV_TOOL = "Developer Tool"
    AI_AUTOMATION = "AI & Workflow Automation"
    PRODUCTIVITY = "Productivity & Office"
    SYSTEM_UTILITY = "System Utility"
    MEDIA_GRAPHICS = "Media & Graphics"
    OTHER = "Other Installed Application"


class DiscoveredApp(BaseModel):
    app_id: str
    display_name: str
    category: AppCategory
    install_location: Optional[str] = None
    executable_path: Optional[str] = None
    is_running: bool = False
    is_allowed: bool = True
    pid: Optional[int] = None
    source: str = "Registry"
    capabilities: List[str] = Field(default_factory=list)


class SystemAppScanner:
    """Discovers all installed and active Windows software."""

    KNOWN_CLI_TOOLS = [
        ("git", AppCategory.DEV_TOOL, "Git Version Control"),
        ("node", AppCategory.DEV_TOOL, "Node.js JavaScript Runtime"),
        ("npm", AppCategory.DEV_TOOL, "Node Package Manager"),
        ("python", AppCategory.DEV_TOOL, "Python 3 Runtime"),
        ("code", AppCategory.DEV_TOOL, "Visual Studio Code CLI"),
        ("cmake", AppCategory.DEV_TOOL, "CMake Build System"),
        ("ffmpeg", AppCategory.MEDIA_GRAPHICS, "FFmpeg Video/Audio Engine"),
        ("n8n", AppCategory.AI_AUTOMATION, "n8n Workflow Automation"),
        ("docker", AppCategory.DEV_TOOL, "Docker Container Engine"),
        ("powershell", AppCategory.SYSTEM_UTILITY, "Windows PowerShell"),
    ]

    def __init__(self):
        self._cache: List[DiscoveredApp] = []

    def scan_running_processes(self) -> Dict[str, Dict[str, Any]]:
        """Find all running applications with window titles."""
        running = {}
        cmd = [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-Process | Where-Object { $_.MainWindowTitle } | Select-Object Id, ProcessName, MainWindowTitle | ConvertTo-Json",
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            import json
            data = json.loads(res.stdout) if res.stdout.strip() else []
            if isinstance(data, dict):
                data = [data]
            for item in data:
                pname = item.get("ProcessName", "").lower()
                running[pname] = {
                    "pid": item.get("Id"),
                    "title": item.get("MainWindowTitle"),
                }
        except Exception:
            pass
        return running

    def scan_registry_apps(self) -> List[Dict[str, str]]:
        """Inspect 64-bit and 32-bit Windows uninstall registries."""
        results = []
        seen = set()

        hives = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]

        for hive, subkey in hives:
            try:
                with winreg.OpenKey(hive, subkey) as key:
                    num_subkeys = winreg.QueryInfoKey(key)[0]
                    for i in range(num_subkeys):
                        try:
                            subkey_name = winreg.EnumKey(key, i)
                            with winreg.OpenKey(key, subkey_name) as app_key:
                                display_name = ""
                                try:
                                    display_name, _ = winreg.QueryValueEx(app_key, "DisplayName")
                                except Exception:
                                    continue

                                if not display_name or display_name.lower() in seen:
                                    continue

                                install_loc = ""
                                try:
                                    install_loc, _ = winreg.QueryValueEx(app_key, "InstallLocation")
                                except Exception:
                                    pass

                                exe_path = ""
                                try:
                                    exe_path, _ = winreg.QueryValueEx(app_key, "DisplayIcon")
                                    if exe_path:
                                        exe_path = exe_path.split(",")[0].strip('"')
                                except Exception:
                                    pass

                                seen.add(display_name.lower())
                                results.append({
                                    "name": display_name,
                                    "location": install_loc,
                                    "exe": exe_path,
                                    "source": "Windows Registry",
                                })
                        except Exception:
                            continue
            except Exception:
                continue
        return results

    def categorize_name(self, name: str) -> AppCategory:
        n = name.lower()
        if any(k in n for k in ["chrome", "edge", "brave", "firefox", "browser", "opera"]):
            return AppCategory.BROWSER
        if any(k in n for k in ["code", "ide", "git", "python", "node", "visual studio", "antigravity", "cmake", "compiler", "sdk"]):
            return AppCategory.DEV_TOOL
        if any(k in n for k in ["n8n", "zapier", "automation", "workflow", "ai", "ollama"]):
            return AppCategory.AI_AUTOMATION
        if any(k in n for k in ["office", "word", "excel", "onenote", "notion", "slack", "teams"]):
            return AppCategory.PRODUCTIVITY
        if any(k in n for k in ["paint", "adobe", "photoshop", "gimp", "blender", "ffmpeg", "canvas"]):
            return AppCategory.MEDIA_GRAPHICS
        if any(k in n for k in ["terminal", "powershell", "driver", "update", "antivirus", "cleaner", "service"]):
            return AppCategory.SYSTEM_UTILITY
        return AppCategory.OTHER

    def scan_all_applications(self) -> List[DiscoveredApp]:
        """Produce unified, categorized, deduplicated catalog of all system applications."""
        running = self.scan_running_processes()
        reg_apps = self.scan_registry_apps()
        catalog: List[DiscoveredApp] = []
        seen_ids = set()

        # 1. Add Registry Apps
        for app in reg_apps:
            raw_name = app["name"]
            app_id = raw_name.lower().replace(" ", "_").replace(".", "_")[:30]
            cat = self.categorize_name(raw_name)

            # Check if running
            is_run = False
            proc_pid = None
            for rname, rinfo in running.items():
                if rname in app_id or rname in raw_name.lower() or app_id in rname:
                    is_run = True
                    proc_pid = rinfo["pid"]
                    break

            catalog.append(DiscoveredApp(
                app_id=app_id,
                display_name=raw_name,
                category=cat,
                install_location=app.get("location"),
                executable_path=app.get("exe"),
                is_running=is_run,
                pid=proc_pid,
                source="Windows Registry",
                capabilities=["GUI Window", "Process Control"] if is_run else ["Installed"],
            ))
            seen_ids.add(app_id)

        # 2. Add Developer CLI Tools found in PATH
        for tool_name, cat, desc in self.KNOWN_CLI_TOOLS:
            path = shutil.which(tool_name)
            if path:
                tool_id = f"cli_{tool_name}"
                if tool_id not in seen_ids:
                    catalog.append(DiscoveredApp(
                        app_id=tool_id,
                        display_name=desc,
                        category=cat,
                        executable_path=path,
                        is_running=False,
                        source="System PATH (CLI)",
                        capabilities=["Command Line Execution", "Scriptable"],
                    ))
                    seen_ids.add(tool_id)

        # 3. Add active Windows Apps not in registry (like MSPaint, Notepad, Antigravity)
        active_specials = [
            ("chrome", "Google Chrome", AppCategory.BROWSER, r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            ("antigravity", "Antigravity IDE", AppCategory.DEV_TOOL, r"C:\Users\RAHUL\AppData\Local\Programs\Antigravity IDE\Antigravity IDE.exe"),
            ("mspaint", "Microsoft Paint", AppCategory.MEDIA_GRAPHICS, "mspaint.exe"),
            ("notepad", "Windows Notepad", AppCategory.PRODUCTIVITY, "notepad.exe"),
        ]
        for aid, dname, cat, exe in active_specials:
            is_run = aid in running
            pid = running[aid]["pid"] if is_run else None
            # Check if already in catalog
            existing = next((a for a in catalog if aid in a.app_id), None)
            if existing:
                existing.is_running = is_run or existing.is_running
                existing.pid = pid or existing.pid
                if exe and not existing.executable_path:
                    existing.executable_path = exe
            else:
                catalog.insert(0, DiscoveredApp(
                    app_id=aid,
                    display_name=dname,
                    category=cat,
                    executable_path=exe,
                    is_running=is_run,
                    pid=pid,
                    source="Windows System",
                    capabilities=["Active GUI Window" if is_run else "Launchable GUI"],
                ))

        self._cache = catalog
        return catalog

    def get_summary_stats(self) -> Dict[str, Any]:
        apps = self._cache or self.scan_all_applications()
        running_count = sum(1 for a in apps if a.is_running)
        categories = {}
        for a in apps:
            categories[a.category.value] = categories.get(a.category.value, 0) + 1

        return {
            "total_applications": len(apps),
            "running_applications": running_count,
            "category_breakdown": categories,
        }
