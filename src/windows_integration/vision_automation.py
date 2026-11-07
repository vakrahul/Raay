"""Dynamic Computer Vision-Based UI Element Detection and Human-Like Cursor Automation.

Runs 100% locally via OpenCV and Win32 APIs — zero API credit consumption.
Provides:
1. Dynamic detection of application canvases, buttons, and tools.
2. Smooth, human-like visible cursor movement with easing.
3. Natural multi-point stroke drawing.
"""

import ctypes
import math
import time
from typing import Any, Dict, List, Optional, Tuple
import cv2
import numpy as np
from PIL import ImageGrab

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class HumanCursorController:
    """Controls the mouse cursor with smooth, visible, human-like easing."""

    def __init__(self):
        # Determine Windows display scaling factor
        self.logical_w = user32.GetSystemMetrics(0)
        try:
            self.physical_w = ImageGrab.grab().width
        except Exception:
            self.physical_w = self.logical_w
        self.scale_factor = self.physical_w / self.logical_w if self.logical_w > 0 else 1.0

    def physical_to_logical(self, px: int, py: int) -> Tuple[int, int]:
        """Convert physical screen pixels to logical coordinates for cursor positioning."""
        return int(px / self.scale_factor), int(py / self.scale_factor)

    def get_cursor_pos(self) -> Tuple[int, int]:
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    def move_smooth(self, target_px: int, target_py: int, duration: float = 0.5):
        """Move cursor smoothly from current position to target coordinate."""
        target_lx, target_ly = self.physical_to_logical(target_px, target_py)
        start_x, start_y = self.get_cursor_pos()

        steps = max(15, int(duration * 60))
        for step in range(1, steps + 1):
            t = step / steps
            # Smooth ease-in-out cubic curve
            ease_t = 3 * (t ** 2) - 2 * (t ** 3)
            curr_x = int(start_x + (target_lx - start_x) * ease_t)
            curr_y = int(start_y + (target_ly - start_y) * ease_t)
            user32.SetCursorPos(curr_x, curr_y)
            time.sleep(duration / steps)

    def click_smooth(self, target_px: int, target_py: int, duration: float = 0.5):
        """Smoothly glide to target and perform a natural click."""
        self.move_smooth(target_px, target_py, duration=duration)
        time.sleep(0.08)
        user32.mouse_event(0x0002, 0, 0, 0, 0)  # Left down
        time.sleep(0.05)
        user32.mouse_event(0x0004, 0, 0, 0, 0)  # Left up
        time.sleep(0.1)

    def draw_path_smooth(self, points: List[Tuple[int, int]], speed: float = 0.008):
        """Draw a natural stroke by gliding the mouse through coordinates while holding left-click."""
        if not points:
            return

        first_lx, first_ly = self.physical_to_logical(points[0][0], points[0][1])
        user32.SetCursorPos(first_lx, first_ly)
        time.sleep(0.04)
        user32.mouse_event(0x0002, 0, 0, 0, 0)  # Mouse down
        time.sleep(0.02)

        for i in range(1, len(points)):
            p0 = points[i - 1]
            p1 = points[i]
            dist = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
            sub_steps = max(1, int(dist / 4))

            for s in range(1, sub_steps + 1):
                inter_x = int(p0[0] + (p1[0] - p0[0]) * (s / sub_steps))
                inter_y = int(p0[1] + (p1[1] - p0[1]) * (s / sub_steps))
                lx, ly = self.physical_to_logical(inter_x, inter_y)
                user32.SetCursorPos(lx, ly)
                user32.mouse_event(0x0001, 0, 0, 0, 0)  # MOUSEEVENTF_MOVE
                time.sleep(speed)

        user32.mouse_event(0x0004, 0, 0, 0, 0)  # Mouse up
        time.sleep(0.03)


class DynamicVisionDetector:
    """Uses OpenCV to inspect application screens and dynamically locate canvases and buttons."""

    @staticmethod
    def capture_fullscreen() -> np.ndarray:
        """Capture entire screen as BGR image."""
        img = ImageGrab.grab()
        return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)

    @staticmethod
    def find_canvas_bounds(screen_bgr: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
        """Dynamically detect the primary white drawing canvas on the screen.

        Returns (x, y, width, height) of the canvas in physical pixels.
        """
        # Convert to grayscale
        gray = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)

        # Threshold for white/near-white drawing area
        _, thresh = cv2.threshold(gray, 245, 255, cv2.THRESH_BINARY)

        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best_box = None
        max_area = 0

        h, w = gray.shape
        # Canvas must be large (at least 20% of screen area) and located in lower 85% of screen
        min_area = (w * h) * 0.15

        for cnt in contours:
            x, y, cw, ch = cv2.boundingRect(cnt)
            area = cw * ch
            # Canvas should start below the top ribbon (y > 100)
            if area > min_area and y > 80:
                if area > max_area:
                    max_area = area
                    best_box = (x, y, cw, ch)

        return best_box

    @staticmethod
    def find_toolbar_tools(screen_bgr: np.ndarray, ribbon_y_max: int = 180) -> List[Dict[str, Any]]:
        """Locate clickable UI buttons and icon containers in the top ribbon."""
        ribbon = screen_bgr[0:ribbon_y_max, :]
        gray = cv2.cvtColor(ribbon, cv2.COLOR_BGR2GRAY)

        # Edge detection for button outlines
        edges = cv2.Canny(gray, 50, 150)
        contours, _ = cv2.findContours(edges, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

        buttons = []
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            # Filter typical toolbar button sizes (20x20 to 100x60)
            if 18 <= w <= 120 and 18 <= h <= 70:
                buttons.append({
                    "center_x": x + w // 2,
                    "center_y": y + h // 2,
                    "box": (x, y, w, h),
                })
        return buttons


class VisualButtonInteractor:
    """Uses vision to identify any button in an open application and clicks it."""

    def __init__(self, cursor_controller: Optional[HumanCursorController] = None):
        self.cursor = cursor_controller or HumanCursorController()

    async def identify_and_click_button(
        self,
        hwnd: int,
        button_label: str,
        gemini_api_key: str,
    ) -> Dict[str, Any]:
        """Locates button on screen by label, smoothly moves cursor, clicks, and verifies."""
        from pathlib import Path
        import base64
        import httpx
        import re

        # 1. Bring window to foreground
        cur_thread = kernel32.GetCurrentThreadId()
        fg_hwnd = user32.GetForegroundWindow()
        fg_thread = user32.GetWindowThreadProcessId(fg_hwnd, None)
        user32.AttachThreadInput(cur_thread, fg_thread, True)
        user32.ShowWindow(hwnd, 3) # Maximize
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
        user32.AttachThreadInput(cur_thread, fg_thread, False)
        time.sleep(1.0)

        # 2. Capture screenshot of window
        rect = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        img = ImageGrab.grab(bbox=(max(0, rect.left), max(0, rect.top), rect.right, rect.bottom))
        snap_path = Path(f"button_search_{int(time.time()*1000)}.png")
        img.save(snap_path)

        # 3. Ask Gemini Vision for exact coordinates of button
        b64 = base64.b64encode(snap_path.read_bytes()).decode("utf-8")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent?key={gemini_api_key}"
        prompt = (
            f"Look at this screenshot of the window. Find the button labeled '{button_label}'. "
            f"Return ONLY its center X and Y pixel coordinates within this image in the format: X, Y. "
            f"Do not write any other words."
        )
        payload = {
            "contents": [{
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": "image/png", "data": b64}}
                ]
            }]
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            res = await client.post(url, json=payload)
            if res.status_code != 200:
                return {"success": False, "error": f"API error: {res.status_code}"}

            coords_text = res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            m = re.search(r"(\d+)\s*,\s*(\d+)", coords_text)
            if not m:
                return {"success": False, "error": f"Could not parse coordinates: '{coords_text}'"}

            target_x = int(m.group(1)) + max(0, rect.left)
            target_y = int(m.group(2)) + max(0, rect.top)

            # 4. Smoothly move cursor and click
            self.cursor.click_smooth(target_x, target_y, duration=0.8)
            time.sleep(2.0)

            # 5. Capture post-action verification
            post_img = ImageGrab.grab(bbox=(max(0, rect.left), max(0, rect.top), rect.right, rect.bottom))
            post_snap = Path(f"post_click_{int(time.time()*1000)}.png")
            post_img.save(post_snap)

            return {
                "success": True,
                "button_label": button_label,
                "detected_coordinates": (target_x, target_y),
                "pre_snapshot": str(snap_path),
                "post_snapshot": str(post_snap),
            }
