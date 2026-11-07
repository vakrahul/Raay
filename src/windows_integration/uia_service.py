"""Genuine Windows UI Automation and Accessibility Integration (Phase 64-66).

Connects directly to the Windows UI Automation subsystem (IUIAutomation / UIAutomationCore.dll)
via native accessibility wrappers. Provides real window discovery, accessible element inspection,
control pattern invocations (ValuePattern, InvokePattern, TextPattern), and keyboard dispatch.
Zero simulation, zero remote computer vision, zero mock data.
"""

import ctypes
import re
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
import uiautomation as auto

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


class UIElementInfo(BaseModel):
    name: str
    control_type: str
    automation_id: str
    is_enabled: bool = True
    value: Optional[str] = None
    bounding_rect: Optional[List[int]] = None  # [left, top, right, bottom]


class UIAutomationService:
    """Interfaces directly with real Windows UI Automation and accessibility element trees."""

    def __init__(self):
        # Configure uiautomation engine defaults
        auto.SetGlobalSearchTimeout(3.0)

    def find_window(
        self,
        window_title: str,
        class_name: Optional[str] = None,
        timeout_seconds: float = 5.0,
    ) -> Optional[auto.WindowControl]:
        """Locate an actual top-level application window on Windows desktop."""
        escaped_title = re.escape(window_title)
        regex_pattern = f"(?i).*{escaped_title}.*"

        # Search desktop windows (depth 3 to catch dialogs and child windows)
        if class_name:
            win = auto.WindowControl(
                searchDepth=3,
                ClassName=class_name,
                RegexName=regex_pattern,
            )
        else:
            win = auto.WindowControl(
                searchDepth=3,
                RegexName=regex_pattern,
            )

        if win.Exists(maxSearchSeconds=timeout_seconds):
            return win

        # Fallback: Check standard exact Name match
        win_exact = auto.WindowControl(searchDepth=3, Name=window_title)
        if win_exact.Exists(maxSearchSeconds=0.5):
            return win_exact

        return None

    def focus_window(self, window_title: str, timeout_seconds: float = 3.0) -> bool:
        """Bring target application window to foreground and assign active input focus."""
        win = self.find_window(window_title, timeout_seconds=timeout_seconds)
        if not win:
            return False

        try:
            hwnd = win.NativeWindowHandle
            if hwnd:
                cur_thread = kernel32.GetCurrentThreadId()
                fg_hwnd = user32.GetForegroundWindow()
                fg_thread = user32.GetWindowThreadProcessId(fg_hwnd, None)
                user32.AttachThreadInput(cur_thread, fg_thread, True)
                user32.keybd_event(0x12, 0, 0, 0)  # ALT down
                user32.keybd_event(0x12, 0, 2, 0)  # ALT up
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
                user32.AttachThreadInput(cur_thread, fg_thread, False)

            win.SetActive()
            win.SetFocus()
            time.sleep(0.2)
            return True
        except Exception:
            return False

    def wait_for_window(self, window_title: str, timeout_seconds: float = 5.0) -> bool:
        """Wait until an actual application window exists on the desktop."""
        win = self.find_window(window_title, timeout_seconds=timeout_seconds)
        return win is not None

    def inspect_window_elements(
        self,
        window_title: str,
        max_depth: int = 3,
        timeout_seconds: float = 3.0,
    ) -> List[UIElementInfo]:
        """Enumerate genuine accessible UI Automation elements for an active window."""
        win = self.find_window(window_title, timeout_seconds=timeout_seconds)
        if not win:
            return []

        elements: List[UIElementInfo] = []
        try:
            for child, depth in auto.WalkControl(win, maxDepth=max_depth):
                val = None
                try:
                    vp = child.GetValuePattern()
                    if vp:
                        val = vp.Value
                    elif child.GetTextPattern():
                        val = child.GetTextPattern().DocumentRange.GetText(-1)
                except Exception:
                    pass

                rect_list = None
                try:
                    r = child.BoundingRectangle
                    if r:
                        rect_list = [r.left, r.top, r.right, r.bottom]
                except Exception:
                    pass

                elements.append(
                    UIElementInfo(
                        name=child.Name or "",
                        control_type=child.ControlTypeName or "Unknown",
                        automation_id=child.AutomationId or "",
                        is_enabled=child.IsEnabled,
                        value=val,
                        bounding_rect=rect_list,
                    )
                )
        except Exception:
            pass

        return elements

    def locate_control(
        self,
        window_title: str,
        name: Optional[str] = None,
        control_type: Optional[str] = None,
        automation_id: Optional[str] = None,
        timeout_seconds: float = 3.0,
    ) -> Optional[auto.Control]:
        """Locate an actual accessible control within the window by name, type, or ID."""
        win = self.find_window(window_title, timeout_seconds=timeout_seconds)
        if not win:
            return None

        # Try direct search using uiautomation control queries
        for child, _ in auto.WalkControl(win, maxDepth=6):
            match = True
            if name is not None:
                child_name = child.Name or ""
                if name.lower() not in child_name.lower():
                    match = False
            if control_type is not None:
                ctype = child.ControlTypeName or ""
                # Match either "EditControl" or "Edit"
                norm_target = control_type.lower().replace("control", "")
                norm_actual = ctype.lower().replace("control", "")
                if norm_target != norm_actual:
                    match = False
            if automation_id is not None:
                if (child.AutomationId or "") != automation_id:
                    match = False

            if match:
                return child

        return None

    def read_text_value(
        self,
        window_title: str,
        automation_id: Optional[str] = None,
        name: Optional[str] = None,
        control_type: Optional[str] = None,
    ) -> Optional[str]:
        """Read real text from an accessible control or document editor."""
        win = self.find_window(window_title)
        if not win:
            return None

        target_control: Optional[auto.Control] = None

        if automation_id or name or control_type:
            target_control = self.locate_control(
                window_title=window_title,
                name=name,
                control_type=control_type,
                automation_id=automation_id,
            )

        if not target_control:
            # Check default DocumentControl / EditControl in window
            doc = win.DocumentControl(searchDepth=4)
            if doc.Exists(0.2):
                target_control = doc
            else:
                edit = win.EditControl(searchDepth=4)
                if edit.Exists(0.2):
                    target_control = edit

        if not target_control:
            return None

        try:
            vp = target_control.GetValuePattern()
            if vp and vp.Value:
                return vp.Value
        except Exception:
            pass

        try:
            tp = target_control.GetTextPattern()
            if tp:
                return tp.DocumentRange.GetText(-1)
        except Exception:
            pass

        # Fallback to Name property (e.g. for TextControls or Buttons)
        return target_control.Name

    def set_text_value(
        self,
        window_title: str,
        text: str,
        automation_id: Optional[str] = None,
        name: Optional[str] = None,
        control_type: Optional[str] = None,
        clear_first: bool = False,
    ) -> bool:
        """Set text into an accessible edit/document control using ValuePattern or SendKeys."""
        win = self.find_window(window_title)
        if not win:
            return False

        self.focus_window(window_title)

        target_control: Optional[auto.Control] = None
        if automation_id or name or control_type:
            target_control = self.locate_control(
                window_title=window_title,
                name=name,
                control_type=control_type,
                automation_id=automation_id,
            )
            # Fall back to cached window handle if control search lands on another instance
            if target_control is None:
                pass

        if not target_control:
            # CRITICAL: When multiple tabs/windows share the title regex, locate_control
            # may return a control bound to the wrong tab. Re-resolve the editor
            # directly from the already-found window object instead.
            for _ in range(3):
                try:
                    doc = win.DocumentControl(searchDepth=4)
                    if doc.Exists(0.2):
                        target_control = doc
                        break
                except Exception:
                    pass
                try:
                    edit = win.EditControl(searchDepth=4)
                    if edit.Exists(0.2):
                        target_control = edit
                        break
                except Exception:
                    pass
                try:
                    win.RebuildCache()
                except Exception:
                    pass
                time.sleep(0.2)

        if not target_control:
            return False

        try:
            target_control.SetFocus()
            time.sleep(0.1)

            # 1. Attempt native ValuePattern SetValue
            try:
                vp = target_control.GetValuePattern()
                if vp:
                    vp.SetValue(text)
                    return True
            except Exception:
                pass

            # 2. Native keyboard dispatch
            if clear_first:
                target_control.SendKeys("{Ctrl}a{Delete}")
                time.sleep(0.05)

            target_control.SendKeys(text)
            return True
        except Exception:
            return False

    def send_keys_to_window(self, window_title: str, keys: str, wait_time: float = 0.05) -> bool:
        """Focus the application window and dispatch genuine keystrokes."""
        for attempt in range(3):
            try:
                win = self.find_window(window_title, timeout_seconds=1.5)
                if not win:
                    time.sleep(0.5)
                    continue
                self.focus_window(window_title)
                time.sleep(0.2)
                win = self.find_window(window_title, timeout_seconds=0.5) or win
                win.SendKeys(keys, interval=wait_time)
                return True
            except Exception:
                time.sleep(0.4)
        return False

    def invoke_button(
        self,
        window_title: str,
        automation_id: Optional[str] = None,
        name: Optional[str] = None,
    ) -> bool:
        """Invoke or click a real Windows button via UIA InvokePattern or physical click."""
        control = self.locate_control(
            window_title=window_title,
            name=name,
            control_type="Button",
            automation_id=automation_id,
        )

        if not control:
            # Check without control_type constraint in case it's a ListItem or MenuItem
            control = self.locate_control(
                window_title=window_title,
                name=name,
                automation_id=automation_id,
            )

        if not control:
            return False

        try:
            ip = control.GetInvokePattern()
            if ip:
                ip.Invoke()
                return True
        except Exception:
            pass

        try:
            control.Click()
            return True
        except Exception:
            return False

    def click_control(
        self,
        window_title: str,
        name: Optional[str] = None,
        control_type: Optional[str] = None,
        automation_id: Optional[str] = None,
    ) -> bool:
        """Click an accessible UI control."""
        control = self.locate_control(
            window_title=window_title,
            name=name,
            control_type=control_type,
            automation_id=automation_id,
        )
        if not control:
            return False

        try:
            control.Click()
            return True
        except Exception:
            return False

    def select_menu_item(self, window_title: str, menu_path: str) -> bool:
        """Navigate and select real application menu hierarchy (e.g. 'File -> Save As...')."""
        win = self.find_window(window_title)
        if not win:
            return False

        self.focus_window(window_title)
        parts = [p.strip() for p in menu_path.split("->")]

        for part in parts:
            item = self.locate_control(window_title=window_title, name=part)
            if not item:
                # Try accelerator if common
                if part.lower() == "save":
                    win.SendKeys("{Ctrl}s")
                    return True
                elif part.lower() == "open":
                    win.SendKeys("{Ctrl}o")
                    return True
                return False

            try:
                ip = item.GetInvokePattern()
                if ip:
                    ip.Invoke()
                else:
                    item.Click()
                time.sleep(0.3)
            except Exception:
                item.Click()
                time.sleep(0.3)

        return True

    def wait_for_control(
        self,
        window_title: str,
        name: Optional[str] = None,
        control_type: Optional[str] = None,
        automation_id: Optional[str] = None,
        timeout_seconds: float = 5.0,
    ) -> bool:
        """Poll until an accessible control (or window) appears."""
        start = time.time()
        while time.time() - start < timeout_seconds:
            if name is None and control_type is None and automation_id is None:
                win = self.find_window(window_title, timeout_seconds=0.5)
                if win:
                    return True
            else:
                ctrl = self.locate_control(
                    window_title=window_title,
                    name=name,
                    control_type=control_type,
                    automation_id=automation_id,
                    timeout_seconds=0.5,
                )
                if ctrl:
                    return True
            time.sleep(0.2)
        return False

    def get_window_title_by_hwnd(self, hwnd: int) -> Optional[str]:
        """Resolve the current title of a known window handle (titles change on navigation)."""
        try:
            ctrl = auto.ControlFromHandle(hwnd)
            try:
                ctrl.RebuildCache()
            except Exception:
                pass
            name = ctrl.Name
            return name or None
        except Exception:
            return None

    def _window_from_hwnd(self, hwnd: int) -> Optional[auto.Control]:
        """Bind directly to a known window handle (deterministic across title changes)."""
        try:
            ctrl = auto.ControlFromHandle(hwnd)
            if ctrl.Exists(maxSearchSeconds=0.5):
                return ctrl
        except Exception:
            pass
        return None

    def focus_window_by_hwnd(self, hwnd: int) -> bool:
        """Bring a known window handle to the foreground with input focus."""
        try:
            cur_thread = kernel32.GetCurrentThreadId()
            fg_hwnd = user32.GetForegroundWindow()
            fg_thread = user32.GetWindowThreadProcessId(fg_hwnd, None)
            user32.AttachThreadInput(cur_thread, fg_thread, True)
            user32.keybd_event(0x12, 0, 0, 0)
            user32.keybd_event(0x12, 0, 2, 0)
            user32.ShowWindow(hwnd, 9)
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.AttachThreadInput(cur_thread, fg_thread, False)
            ctrl = self._window_from_hwnd(hwnd)
            if ctrl:
                try:
                    ctrl.SetFocus()
                except Exception:
                    pass
            time.sleep(0.2)
            return True
        except Exception:
            return False

    def send_keys_by_hwnd(self, hwnd: int, keys: str, wait_time: float = 0.05) -> bool:
        """Dispatch keystrokes to a known window handle (no title matching).

        Verifies foreground ownership before sending; retries when Windows
        denies the foreground switch (e.g. during calls/media capture).
        """
        for attempt in range(4):
            try:
                ctrl = self._window_from_hwnd(hwnd)
                if not ctrl:
                    time.sleep(0.5)
                    continue
                try:
                    already_foreground = user32.GetForegroundWindow() == hwnd
                except Exception:
                    already_foreground = False
                if not already_foreground:
                    # Focus dance only when needed: refocusing a window moves
                    # keyboard focus out of controls (e.g. the address bar).
                    self.focus_window_by_hwnd(hwnd)
                    foregrounded = False
                    for _ in range(10):
                        try:
                            if user32.GetForegroundWindow() == hwnd:
                                foregrounded = True
                                break
                        except Exception:
                            pass
                        time.sleep(0.2)
                    if not foregrounded:
                        time.sleep(0.5)
                        continue
                    time.sleep(0.3)
                    ctrl = self._window_from_hwnd(hwnd) or ctrl
                ctrl.SendKeys(keys, interval=wait_time)
                return True
            except Exception:
                time.sleep(0.5)
        return False

    def is_address_bar_focused(self) -> bool:
        """Check whether keyboard focus currently sits in a browser address bar."""
        try:
            focused = auto.GetFocusedControl()
            if not focused:
                return False
            if focused.ControlTypeName != "EditControl":
                return False
            return "address and search bar" in (focused.Name or "").lower()
        except Exception:
            return False

    def get_browser_url_by_hwnd(self, hwnd: int) -> Optional[str]:
        """Read a Chromium address bar by window handle (deterministic, no title matching).

        Returns None only when the address bar cannot be found. A blank New Tab
        page legitimately reports an empty string, which is a valid observation.
        """
        try:
            win = self._window_from_hwnd(hwnd)
            if not win:
                return None
            for child, _ in auto.WalkControl(win, maxDepth=14):
                try:
                    if child.ControlTypeName != "EditControl":
                        continue
                    cname = child.Name or ""
                    if "address and search bar" in cname.lower():
                        try:
                            vp = child.GetValuePattern()
                        except Exception:
                            return None
                        if vp is None:
                            return None
                        return vp.Value or ""
                except Exception:
                    continue
        except Exception:
            pass
        return None

    def list_browser_tabs(self, hwnd: int) -> List[Dict[str, Any]]:
        """List browser tabs (TabItem controls) with selection state. No screenshots."""
        tabs: List[Dict[str, Any]] = []
        try:
            win = self._window_from_hwnd(hwnd)
            if not win:
                return tabs
            for child, _ in auto.WalkControl(win, maxDepth=14):
                try:
                    if (child.ControlTypeName or "") != "TabItemControl":
                        continue
                    name = child.Name or ""
                    selected = False
                    try:
                        sip = child.GetSelectionItemPattern()
                        if sip:
                            selected = bool(sip.IsSelected)
                    except Exception:
                        pass
                    tabs.append({"name": name, "selected": selected})
                except Exception:
                    continue
        except Exception:
            pass
        return tabs

    def select_browser_tab(self, hwnd: int, tab_name: str) -> bool:
        """Activate a browser tab by (sub)string of its accessible name via InvokePattern."""
        try:
            win = self._window_from_hwnd(hwnd)
            if not win:
                return False
            self.focus_window_by_hwnd(hwnd)
            needle = (tab_name or "").lower()
            for child, _ in auto.WalkControl(win, maxDepth=14):
                try:
                    if (child.ControlTypeName or "") != "TabItemControl":
                        continue
                    if needle and needle not in (child.Name or "").lower():
                        continue
                    try:
                        ip = child.GetInvokePattern()
                        if ip:
                            ip.Invoke()
                            return True
                    except Exception:
                        pass
                    try:
                        child.Click()
                        return True
                    except Exception:
                        continue
                except Exception:
                    continue
        except Exception:
            pass
        return False

    def read_page_heading_by_hwnd(self, hwnd: int, timeout_seconds: float = 15.0) -> Optional[str]:
        """Read a page heading by window handle through the accessibility tree.

        Chromium only populates a page's accessibility subtree while its window
        owns the foreground, so foreground is asserted (and re-asserted) before
        each walk. Waits for async page rendering. No screenshots, no coordinates.
        """
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            try:
                self.focus_window_by_hwnd(hwnd)
                fg_ok = False
                for _ in range(10):
                    try:
                        if user32.GetForegroundWindow() == hwnd:
                            fg_ok = True
                            break
                    except Exception:
                        pass
                    time.sleep(0.2)
                if not fg_ok:
                    time.sleep(0.5)
                    continue
                time.sleep(0.6)
                win = self._window_from_hwnd(hwnd)
                if not win:
                    time.sleep(0.5)
                    continue
                try:
                    win.RebuildCache()
                except Exception:
                    pass
                # Scan every page document: background tabs expose empty trees,
                # so the content-bearing (active tab) document must be selected
                # by substance, not by position.
                docs: List[Any] = []
                try:
                    for child, _ in auto.WalkControl(win, maxDepth=12):
                        try:
                            if (child.ControlTypeName or "") == "DocumentControl":
                                docs.append(child)
                        except Exception:
                            continue
                except Exception:
                    pass
                if not docs:
                    time.sleep(0.5)
                    continue
                best_heading: Optional[str] = None
                best_fallback: Optional[str] = None
                best_count = -1
                for doc in docs:
                    try:
                        headings: List[str] = []
                        texts: List[str] = []
                        for child, _ in auto.WalkControl(doc, maxDepth=18):
                            try:
                                ctype = child.ControlTypeName or ""
                                cname = (child.Name or "").strip()
                                if not cname:
                                    continue
                                if "heading" in ctype.lower() or "header" in ctype.lower():
                                    headings.append(cname)
                                elif ctype == "TextControl" and len(cname) >= 3:
                                    texts.append(cname)
                            except Exception:
                                continue
                        substance = len(headings) * 4 + len(texts)
                        if substance > best_count:
                            best_count = substance
                            best_heading = headings[0] if headings else None
                            best_fallback = texts[0] if texts else None
                    except Exception:
                        continue
                if best_heading:
                    return best_heading
                if best_fallback:
                    return best_fallback
            except Exception:
                pass
            time.sleep(0.5)
        return None

    def get_browser_url(self, window_title: str, timeout_seconds: float = 3.0) -> Optional[str]:
        """Read the current URL from a Chromium browser's address bar via UI Automation.

        Locates the address-bar Edit control by accessible name (no screenshots,
        no coordinates) and returns its ValuePattern text.
        """
        win = self.find_window(window_title, timeout_seconds=timeout_seconds)
        if not win:
            return None
        try:
            for child, _ in auto.WalkControl(win, maxDepth=14):
                try:
                    if child.ControlTypeName != "EditControl":
                        continue
                    cname = child.Name or ""
                    if "address and search bar" in cname.lower():
                        try:
                            vp = child.GetValuePattern()
                        except Exception:
                            return None
                        if vp is None:
                            return None
                        return vp.Value or ""
                except Exception:
                    continue
        except Exception:
            pass
        return None

    def read_page_heading(self, window_title: str, timeout_seconds: float = 5.0) -> Optional[str]:
        """Read a web page's main heading through the accessibility tree.

        Prefers real heading controls, then the first substantial text control
        inside the page document. No screenshots, no coordinates.
        """
        win = self.find_window(window_title, timeout_seconds=timeout_seconds)
        if not win:
            return None
        try:
            doc = win.DocumentControl(searchDepth=10)
            if not doc.Exists(1.0):
                return None
            fallback: Optional[str] = None
            for child, _ in auto.WalkControl(doc, maxDepth=16):
                try:
                    ctype = child.ControlTypeName or ""
                    cname = (child.Name or "").strip()
                    if not cname:
                        continue
                    if "heading" in ctype.lower() or "header" in ctype.lower():
                        return cname
                    if fallback is None and ctype == "TextControl" and len(cname) >= 3:
                        fallback = cname
                except Exception:
                    continue
            return fallback
        except Exception:
            return None

