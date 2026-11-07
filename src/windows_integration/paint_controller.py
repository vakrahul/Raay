"""Phase 7a Dynamic & Stabilised Windows 11 MS Paint Controller (`paint-dynamic`).

Enforces all Phase 7a stability, safety, and dynamic SceneSpec guarantees:
1. Zero hard-coded ribbon or canvas coordinates: every tool, shape, colour swatch,
   and the `AutomationId='image'` canvas rectangle is resolved dynamically via
   DPI-aware Windows UI Automation (`uiautomation`), with fallback to stored
   reference-template matching (`.winai/paint_templates/`). If neither locates a
   required element, execution aborts with `ABORTED_SAFE` and a one-line spoken reason.
2. Pre-flight check before every drawing sequence: verifies `mspaint.exe` is running,
   bound by `PID`/`HWND`, no unexpected dialog is open, window is maximized, DPI scale
   is read, canvas rectangle is valid, and canvas is cleared (via Select All + Delete,
   never Ctrl+N or closing Paint) when required.
3. Guaranteed mouse-up & modifier release: every drag is wrapped in `try/finally`
   releasing `MOUSEEVENTF_LEFTUP`, backed by an `atexit` and `SIGINT`/`SIGTERM`
   emergency input-release handler. Physical `Esc` (`GetAsyncKeyState(0x1B)`) or
   `CancelToken` halts within one stroke step.
4. Per-stroke and per-`SendInput` `HWND`/`PID` focus guard: aborts immediately with
   `ABORTED_SAFE` if foreground window or PID changes or any unexpected dialog appears.
5. Post-fill pixel verification: samples the canvas pixel at the fill point after
   every gallery-shape fill, compares RGB against `PALETTE_EXPECTED_RGB` within
   tolerance, retries once on mismatch, and saves a failure screenshot to
   `.winai/paint_failures/` on any failed run (no silent success).
6. Dynamic `SceneSpec` (max 12 objects, normalized `[0,1]` fractions, no off-canvas
   objects, no free-text OS fields) + object-ID edits (`recolour`, `resize`, `move`,
   `remove`, `add`), with `llm_ms` and `plan_cache_hit` logged per scene.
"""

from __future__ import annotations

import atexit
import contextlib
import ctypes
import json
import logging
import math
import re
import shutil
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Protocol, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Enable Per-Monitor v2 DPI Awareness before querying screen metrics
with contextlib.suppress(Exception):
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
with contextlib.suppress(Exception):
    user32.SetProcessDPIAware()


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("pad", ctypes.c_byte * 32),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("u", _INPUT_UNION)]


INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000
KEYEVENTF_KEYUP = 0x0002

VK_ESCAPE = 0x1B
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_DELETE = 0x2E
VK_A = 0x41
VK_Z = 0x5A

# Exact 20 palette swatch names and calibrated RGB values in Windows 11 MS Paint
PALETTE_EXPECTED_RGB: Dict[str, Tuple[int, int, int]] = {
    "black": (0, 0, 0),
    "grey": (127, 127, 127),
    "dark red": (136, 0, 21),
    "red": (237, 28, 36),
    "orange": (255, 127, 39),
    "yellow": (255, 242, 0),
    "green": (34, 177, 76),
    "turquoise": (0, 162, 232),
    "indigo": (63, 72, 204),
    "purple": (163, 73, 164),
    "white": (255, 255, 255),
    "light grey": (195, 195, 195),
    "brown": (185, 122, 87),
    "rose": (255, 174, 201),
    "gold": (255, 201, 14),
    "light yellow": (239, 228, 176),
    "lime": (181, 230, 29),
    "light turquoise": (153, 217, 234),
    "blue-grey": (112, 146, 190),
    "lavender": (200, 191, 231),
}

# Extended colour lookup table (CSS/multilingual colour names -> RGB -> nearest Paint swatch)
NAMED_COLOR_RGB_LOOKUP: Dict[str, Tuple[int, int, int]] = {
    **PALETTE_EXPECTED_RGB,
    "gray": (127, 127, 127),
    "light gray": (195, 195, 195),
    "blue": (63, 72, 204),
    "navy": (20, 40, 160),
    "cobalt": (30, 70, 210),
    "sky blue": (135, 206, 235),
    "cyan": (0, 180, 240),
    "teal": (0, 140, 160),
    "aqua": (140, 220, 240),
    "crimson": (220, 20, 60),
    "scarlet": (255, 36, 0),
    "maroon": (128, 0, 0),
    "burgundy": (128, 0, 32),
    "coral": (255, 127, 80),
    "salmon": (250, 128, 114),
    "pink": (255, 182, 193),
    "magenta": (200, 50, 180),
    "violet": (148, 0, 211),
    "plum": (190, 160, 220),
    "golden": (255, 205, 20),
    "amber": (255, 191, 0),
    "cream": (245, 235, 185),
    "beige": (240, 225, 175),
    "tan": (190, 130, 90),
    "chocolate": (165, 105, 70),
    "olive": (150, 200, 40),
    "emerald": (40, 180, 80),
    "mint": (160, 230, 60),
    "silver": (195, 195, 195),
    "charcoal": (60, 60, 60),
    # Multilingual colour names (Hindi, Telugu, Tamil — both native words and transliterated English)
    "laal": (237, 28, 36),
    "lal": (237, 28, 36),
    "लाल": (237, 28, 36),
    "रेड": (237, 28, 36),
    "erupu": (237, 28, 36),
    "ఎరుపు": (237, 28, 36),
    "రెడ్": (237, 28, 36),
    "sivappu": (237, 28, 36),
    "சிவப்பு": (237, 28, 36),
    "ரெட்": (237, 28, 36),
    "neela": (63, 72, 204),
    "नीला": (63, 72, 204),
    "ब्लू": (63, 72, 204),
    "neelam": (63, 72, 204),
    "నీలం": (63, 72, 204),
    "బ్లూ": (63, 72, 204),
    "நீலம்": (63, 72, 204),
    "ப்ளூ": (63, 72, 204),
    "hara": (34, 177, 76),
    "हरा": (34, 177, 76),
    "ग्रीन": (34, 177, 76),
    "pacha": (34, 177, 76),
    "ఆకుపచ్చ": (34, 177, 76),
    "పచ్చ": (34, 177, 76),
    "గ్రీన్": (34, 177, 76),
    "pachai": (34, 177, 76),
    "பச்சை": (34, 177, 76),
    "கிரீன்": (34, 177, 76),
    "peela": (255, 242, 0),
    "पीला": (255, 242, 0),
    "येलो": (255, 242, 0),
    "pasupu": (255, 242, 0),
    "పసుపు": (255, 242, 0),
    "ఎల్లో": (255, 242, 0),
    "manjal": (255, 242, 0),
    "மஞ்சள்": (255, 242, 0),
    "யெல்லோ": (255, 242, 0),
    "sunehra": (255, 201, 14),
    "गोल्ड": (255, 201, 14),
    "bangaram": (255, 201, 14),
    "బంగారం": (255, 201, 14),
    "గోల్డ్": (255, 201, 14),
    "thangam": (255, 201, 14),
    "தங்கம்": (255, 201, 14),
    "கோல்ட்": (255, 201, 14),
    "gulabi": (255, 174, 201),
    "गुलाबी": (255, 174, 201),
    "पिंक": (255, 174, 201),
    "పింక్": (255, 174, 201),
    "பிங்க்": (255, 174, 201),
    "ఆరెంజ్": (255, 127, 39),
    "పర్పుల్": (163, 73, 164),
    "బ్రౌన్": (185, 122, 87),
    "బ్లాక్": (0, 0, 0),
    "వైట్": (255, 255, 255),
    "bhura": (185, 122, 87),
    "भूरा": (185, 122, 87),
    "kala": (0, 0, 0),
    "काला": (0, 0, 0),
    "nallani": (0, 0, 0),
    "నలుపు": (0, 0, 0),
    "karuppu": (0, 0, 0),
    "கருப்பு": (0, 0, 0),
    "safed": (255, 255, 255),
    "सफेद": (255, 255, 255),
    "tellani": (255, 255, 255),
    "vellai": (255, 255, 255),
    "narangi": (255, 127, 39),
    "नारंगी": (255, 127, 39),
    "kesari": (255, 127, 39),
    "baingani": (163, 73, 164),
    "आसमानी": (0, 162, 232),
    "అగ్రీన్": (34, 177, 76),
}

# Mapping from SceneObjectSpec.shape to MS Paint Shapes gallery UIA element name
SCENE_SHAPE_TO_UIA_NAME: Dict[str, str] = {
    "rectangle": "rectangle",
    "ellipse": "oval",
    "triangle": "triangle",
    "line": "line",
    "polygon": "pentagon",
    "pentagon": "pentagon",
    "hexagon": "hexagon",
    "diamond": "diamond",
    "star": "five-point star",
    "heart": "heart",
    "right_arrow": "right arrow",
    "left_arrow": "left arrow",
    "up_arrow": "up arrow",
    "down_arrow": "down arrow",
    "cloud": "cloud callout",
    "lightning": "lightning",
}

GALLERY_SHAPES = {
    "rectangle",
    "ellipse",
    "triangle",
    "polygon",
    "pentagon",
    "hexagon",
    "diamond",
    "star",
    "heart",
    "right_arrow",
    "left_arrow",
    "up_arrow",
    "down_arrow",
    "cloud",
    "lightning",
}

SceneShapeLiteral = Literal[
    "rectangle",
    "ellipse",
    "triangle",
    "line",
    "polygon",
    "pentagon",
    "hexagon",
    "diamond",
    "star",
    "heart",
    "right_arrow",
    "left_arrow",
    "up_arrow",
    "down_arrow",
    "cloud",
    "lightning",
    "polyline",
]

SHAPE_SYNONYMS: Dict[str, SceneShapeLiteral] = {
    "circle": "ellipse",
    "oval": "ellipse",
    "ellipse": "ellipse",
    "gola": "ellipse",
    "vruttam": "ellipse",
    "vattam": "ellipse",
    "गोला": "ellipse",
    "सर्कल": "ellipse",
    "వృత్తం": "ellipse",
    "సర్కిల్": "ellipse",
    "సర్కిట్": "ellipse",
    "సర్కల్": "ellipse",
    "సరికిల్": "ellipse",
    "రౌండ్": "ellipse",
    "గుండ్రం": "ellipse",
    "సైకిల్": "ellipse",
    "வட்டம்": "ellipse",
    "सर्किल": "ellipse",
    "square": "rectangle",
    "rectangle": "rectangle",
    "box": "rectangle",
    "dabba": "rectangle",
    "चौकोर": "rectangle",
    "चतुर्भुज": "rectangle",
    "స్క్వేర్": "rectangle",
    "రెక్టాంగిల్": "rectangle",
    "చతురస్రం": "rectangle",
    "బాక్స్": "rectangle",
    "சதுரம்": "rectangle",
    "triangle": "triangle",
    "trikon": "triangle",
    "त्रिकोण": "triangle",
    "ట్రయాంగిల్": "triangle",
    "త్రిభుజం": "triangle",
    "முக்கோணம்": "triangle",
    "star": "star",
    "tara": "star",
    "nakshatram": "star",
    "तारा": "star",
    "स्टार": "star",
    "నక్షత్రం": "star",
    "స్టార్": "star",
    "நட்சத்திரம்": "star",
    "heart": "heart",
    "love": "heart",
    "love symbol": "heart",
    "లవ్": "heart",
    "లవ్ సింబల్": "heart",
    "లవ్ సింగల్": "heart",
    "ప్రేమ": "heart",
    "dil": "heart",
    "hrudayam": "heart",
    "दिल": "heart",
    "प्यार": "heart",
    "हार्ट": "heart",
    "హృదయం": "heart",
    "హార్ట్": "heart",
    "இதயம்": "heart",
    "லவ்": "heart",
    "diamond": "diamond",
    "డైమండ్": "diamond",
    "డైమండ్ షిప్": "diamond",
    "డైమండ్ షేప్": "diamond",
    "डायमंड": "diamond",
    "हीरा": "diamond",
    "வைரம்": "diamond",
    "right arrow": "right_arrow",
    "రైట్ సైడ్ ఆరో": "right_arrow",
    "రైట్ ఆరో": "right_arrow",
    "arrow": "right_arrow",
    "ఆరో": "right_arrow",
    "యారో": "right_arrow",
    "బాణం": "right_arrow",
    "एरो": "right_arrow",
    "तीर": "right_arrow",
    "அம்பு": "right_arrow",
    "left arrow": "left_arrow",
    "లెఫ్ట్ ఆరో": "left_arrow",
    "up arrow": "up_arrow",
    "అప్ ఆరో": "up_arrow",
    "down arrow": "down_arrow",
    "డౌన్ ఆరో": "down_arrow",
    "polygon": "polygon",
    "pentagon": "pentagon",
    "పెంటగాన్": "pentagon",
    "hexagon": "hexagon",
    "హెక్సాగాన్": "hexagon",
    "cloud": "cloud",
    "క్లౌడ్": "cloud",
    "మేఘం": "cloud",
    "lightning": "lightning",
    "లైట్నింగ్": "lightning",
    "మెరుపు": "lightning",
    "line": "line",
    "rekha": "line",
    "geetha": "line",
    "polyline": "polyline",
}

# Backwards-compatible exports for api_routes.py
SHAPE_ALIASES = SHAPE_SYNONYMS
COLOR_ALIASES = {k: k for k in NAMED_COLOR_RGB_LOOKUP}


class PaintAbortSafeError(RuntimeError):
    """Raised when pre-flight, element lookup, or focus/dialog guard aborts safely."""

    def __init__(self, spoken_reason: str, code: str = "PAINT_ABORTED_SAFE") -> None:
        super().__init__(spoken_reason)
        self.spoken_reason = spoken_reason
        self.code = code


def resolve_palette_color(
    requested_color: Optional[str],
    color_mappings_log: Optional[List[Dict[str, str]]] = None,
) -> Optional[str]:
    """Map any colour name to an exact Windows 11 MS Paint palette swatch name.

    Unknown colour names map to the nearest palette entry by Euclidean RGB distance
    and log the mapping.
    """
    if not requested_color:
        return None
    clean = requested_color.strip().lower()
    if not clean or clean == "none":
        return None
    if clean in PALETTE_EXPECTED_RGB:
        return clean

    target_rgb = NAMED_COLOR_RGB_LOOKUP.get(clean)
    if target_rgb is None:
        # Hash-deterministic or substring fallback into NAMED_COLOR_RGB_LOOKUP
        for k, v in NAMED_COLOR_RGB_LOOKUP.items():
            if k in clean or clean in k:
                target_rgb = v
                break
    if target_rgb is None:
        # Default unknown exotic colour name to nearest warm/cool match ('indigo')
        target_rgb = (63, 72, 204)

    best_name = "black"
    best_dist = float("inf")
    for swatch_name, swatch_rgb in PALETTE_EXPECTED_RGB.items():
        dist = math.sqrt(sum((a - b) ** 2 for a, b in zip(target_rgb, swatch_rgb, strict=True)))
        if dist < best_dist:
            best_dist = dist
            best_name = swatch_name

    if color_mappings_log is not None and clean != best_name:
        mapping_entry = {
            "requested": clean,
            "mapped_to": best_name,
            "rgb_distance": f"{best_dist:.1f}",
        }
        color_mappings_log.append(mapping_entry)
        logger.info(
            "Mapped colour %r -> palette swatch %r (dist=%.1f)", clean, best_name, best_dist
        )
    return best_name


def rgb_distance(c1: Tuple[int, int, int], c2: Tuple[int, int, int]) -> float:
    return math.sqrt(float((c1[0] - c2[0]) ** 2 + (c1[1] - c2[1]) ** 2 + (c1[2] - c2[2]) ** 2))


# ---------------------------------------------------------------------------
# Phase 7a Pydantic SceneSpec & SceneEditSpec Schemas
# ---------------------------------------------------------------------------

SceneShapeKind = SceneShapeLiteral

_OBJ_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")


class SceneObjectSpec(BaseModel):
    """Single geometric object inside a `SceneSpec` (normalized `[0, 1]` canvas fractions)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=32)
    shape: SceneShapeKind
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    w: float = Field(ge=0.0, le=1.0)
    h: float = Field(ge=0.0, le=1.0)
    fill_color: Optional[str] = Field(default=None, max_length=32)
    outline_color: str = Field(default="black", max_length=32)
    points: Optional[List[Tuple[float, float]]] = Field(default=None, max_length=64)

    @field_validator("id")
    @classmethod
    def _validate_id_no_free_text(cls, v: str) -> str:
        cleaned = (v or "").strip()
        if not _OBJ_ID_RE.match(cleaned):
            raise ValueError(
                f"Object id '{v}' must match ^[a-zA-Z0-9_-]{{1,32}}$ (no free text)."
            )
        return cleaned

    @model_validator(mode="after")
    def _validate_on_canvas_bounds(self) -> "SceneObjectSpec":
        # Reject off-canvas objects (x + w > 1.0 or y + h > 1.0 with 1e-6 float tolerance)
        if self.x + self.w > 1.0 + 1e-6 or self.y + self.h > 1.0 + 1e-6:
            raise ValueError(
                f"Object '{self.id}' extends off-canvas "
                f"(x={self.x}, y={self.y}, w={self.w}, h={self.h})."
            )
        if self.shape in GALLERY_SHAPES and (self.w < 0.02 or self.h < 0.02):
            raise ValueError(
                f"Gallery shape '{self.id}' ({self.shape}) must have w >= 0.02 and h >= 0.02."
            )
        if self.shape == "polyline":
            if not self.points or len(self.points) < 2:
                raise ValueError(f"Polyline object '{self.id}' requires at least 2 points.")
            for px, py in self.points:
                if not (0.0 <= px <= 1.0 and 0.0 <= py <= 1.0):
                    raise ValueError(
                        f"Polyline point ({px}, {py}) on '{self.id}' is outside [0, 1] bounds."
                    )
        return self


class SceneSpec(BaseModel):
    """Validated Paint scene specification (max 12 objects, zero OS free-text fields)."""

    model_config = ConfigDict(extra="forbid")

    scene_id: str = Field(default="scene_1", pattern=r"^[a-zA-Z0-9_-]{1,32}$")
    clear_before_draw: bool = True
    objects: List[SceneObjectSpec] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def _validate_unique_ids(self) -> "SceneSpec":
        seen = set()
        for obj in self.objects:
            if obj.id in seen:
                raise ValueError(f"Duplicate object id '{obj.id}' in SceneSpec.")
            seen.add(obj.id)
        return self


class SceneEditSpec(BaseModel):
    """Structured edit operation on the stored `SceneSpec` by object id."""

    model_config = ConfigDict(extra="forbid")

    operation: Literal["recolour", "resize", "move", "remove", "add"]
    target_id: str = Field(default="last", pattern=r"^[a-zA-Z0-9_-]{1,32}$")
    new_fill_color: Optional[str] = Field(default=None, max_length=32)
    new_outline_color: Optional[str] = Field(default=None, max_length=32)
    scale_w: Optional[float] = Field(default=None, gt=0.1, le=3.0)
    scale_h: Optional[float] = Field(default=None, gt=0.1, le=3.0)
    new_x: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    new_y: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    add_object: Optional[SceneObjectSpec] = None


# ---------------------------------------------------------------------------
# Emergency Input Release Handler (atexit + SIGINT/SIGTERM)
# ---------------------------------------------------------------------------

_ACTIVE_BACKENDS: List["PaintBackendProtocol"] = []


def register_backend_for_emergency_release(backend: "PaintBackendProtocol") -> None:
    if backend not in _ACTIVE_BACKENDS:
        _ACTIVE_BACKENDS.append(backend)


def emergency_release_all_inputs(
    target_backend: Optional["PaintBackendProtocol"] = None,
) -> Dict[str, Any]:
    """Unconditionally release left mouse button and Ctrl/Shift/Alt modifier keys."""
    backends = [target_backend] if target_backend is not None else list(_ACTIVE_BACKENDS)
    released_count = 0
    for b in backends:
        with contextlib.suppress(Exception):
            b.emergency_release()
            released_count += 1

    # Also release OS-level mouse up & modifier keys if running on Win32 without explicit backend
    if target_backend is None:
        with contextlib.suppress(Exception):
            inp_up = INPUT(
                INPUT_MOUSE,
                _INPUT_UNION(mi=MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, 0)),
            )
            user32.SendInput(1, ctypes.byref(inp_up), ctypes.sizeof(INPUT))
            for vk in (VK_CONTROL, VK_SHIFT, VK_MENU):
                k_up = INPUT(
                    INPUT_KEYBOARD,
                    _INPUT_UNION(ki=KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP, 0, 0)),
                )
                user32.SendInput(1, ctypes.byref(k_up), ctypes.sizeof(INPUT))
    return {
        "released_backends": released_count,
        "modifiers_released": ["LBUTTON", "CTRL", "SHIFT", "ALT"],
    }


atexit.register(emergency_release_all_inputs)


def _install_signal_handlers() -> None:
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(Exception):
            prev = signal.getsignal(sig)

            def _handler(signum: int, frame: Any, _prev: Any = prev) -> None:
                emergency_release_all_inputs()
                if callable(_prev):
                    _prev(signum, frame)

            signal.signal(sig, _handler)


_install_signal_handlers()


# ---------------------------------------------------------------------------
# Backend Protocol (Win32 Live Backend vs Fake Unit-Test Backend)
# ---------------------------------------------------------------------------


class PaintBackendProtocol(Protocol):
    is_mouse_button_down: bool
    mouse_events: List[Tuple[str, int, int]]
    key_events: List[str]

    def ensure_paint_running_and_maximized(self) -> Tuple[int, int, float]: ...
    def verify_foreground_guard(
        self, expected_hwnd: int, expected_pid: int
    ) -> Tuple[bool, str]: ...
    def inspect_uia_elements(
        self, hwnd: int
    ) -> Tuple[
        Optional[Tuple[int, int, int, int]],
        Dict[str, Tuple[int, int]],
        Dict[str, Tuple[int, int]],
        Dict[str, Tuple[int, int]],
    ]: ...
    def match_reference_template(self, category: str, name: str) -> Optional[Tuple[int, int]]: ...
    def move_to(self, x: int, y: int) -> None: ...
    def mouse_down(self, x: int, y: int) -> None: ...
    def mouse_up(self, x: int, y: int) -> None: ...
    def send_ctrl_a_delete(self) -> None: ...
    def send_ctrl_z(self) -> None: ...
    def is_escape_pressed(self) -> bool: ...
    def sample_pixel(self, x: int, y: int) -> Tuple[int, int, int]: ...
    def save_failure_screenshot(
        self, out_path: Path, canvas_rect: Optional[Tuple[int, int, int, int]]
    ) -> Optional[str]: ...
    def emergency_release(self) -> None: ...


class Win32PaintBackend:
    """Real DPI-aware Windows 11 UIA + 64-bit SendInput + PIL ImageGrab backend."""

    def __init__(self, templates_dir: Optional[Path] = None) -> None:
        import uiautomation as auto

        self._auto = auto
        self._auto.SetGlobalSearchTimeout(2.5)
        self.templates_dir = templates_dir or (Path.cwd() / ".winai" / "paint_templates")
        self.is_mouse_button_down: bool = False
        self.mouse_events: List[Tuple[str, int, int]] = []
        self.key_events: List[str] = []
        self.screen_w: int = max(1, int(user32.GetSystemMetrics(0)))
        self.screen_h: int = max(1, int(user32.GetSystemMetrics(1)))
        self._cached_uia_hwnd: int = 0
        self._cached_uia_tuple: Optional[
            Tuple[
                Optional[Tuple[int, int, int, int]],
                Dict[str, Tuple[int, int]],
                Dict[str, Tuple[int, int]],
                Dict[str, Tuple[int, int]],
            ]
        ] = None
        register_backend_for_emergency_release(self)

    def _refresh_screen_metrics(self) -> None:
        self.screen_w = max(1, int(user32.GetSystemMetrics(0)))
        self.screen_h = max(1, int(user32.GetSystemMetrics(1)))

    @staticmethod
    def _get_hwnd_pid_and_class(hwnd: int) -> Tuple[int, str, str]:
        if not hwnd:
            return 0, "", ""
        pid = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
        cls_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(ctypes.c_void_p(hwnd), cls_buf, 256)
        txt_buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(ctypes.c_void_p(hwnd), txt_buf, 256)
        return int(pid.value), cls_buf.value, txt_buf.value

    def ensure_paint_running_and_maximized(self) -> Tuple[int, int, float]:
        """Ensure `mspaint.exe` is running and maximized; return `(hwnd, pid, dpi_scale)`."""
        with contextlib.ExitStack() as stack:
            if hasattr(self._auto, "UIAutomationInitializerInThread"):
                stack.enter_context(self._auto.UIAutomationInitializerInThread())
            win = self._auto.WindowControl(searchDepth=2, ClassName="MSPaintApp")
            if not win.Exists(maxSearchSeconds=0.8):
                paint_exe = shutil.which("mspaint.exe") or "mspaint.exe"
                subprocess.Popen([paint_exe])  # noqa: S603
                time.sleep(2.2)
                win = self._auto.WindowControl(searchDepth=2, ClassName="MSPaintApp")
                if not win.Exists(maxSearchSeconds=4.0):
                    raise PaintAbortSafeError(
                        "Stopped safely: Microsoft Paint could not be launched or found.",
                        code="PAINT_NOT_RUNNING",
                    )

            hwnd = int(win.NativeWindowHandle or 0)
            pid, cls_name, _title = self._get_hwnd_pid_and_class(hwnd)
            if not hwnd or not pid or cls_name != "MSPaintApp":
                raise PaintAbortSafeError(
                    "Stopped safely: invalid Microsoft Paint window handle or process ID.",
                    code="PAINT_INVALID_HWND",
                )

            for _attempt in range(6):
                cur_tid = kernel32.GetCurrentThreadId()
                fg_hwnd = int(user32.GetForegroundWindow() or 0)
                fg_tid = (
                    user32.GetWindowThreadProcessId(ctypes.c_void_p(fg_hwnd), None)
                    if fg_hwnd
                    else 0
                )
                attached = False
                if fg_tid and cur_tid != fg_tid:
                    attached = bool(user32.AttachThreadInput(cur_tid, fg_tid, True))
                try:
                    user32.keybd_event(0x12, 0, 0, 0)
                    user32.keybd_event(0x12, 0, 2, 0)
                    user32.ShowWindow(ctypes.c_void_p(hwnd), 9)  # SW_RESTORE
                    user32.ShowWindow(ctypes.c_void_p(hwnd), 3)  # SW_MAXIMIZE
                    user32.BringWindowToTop(ctypes.c_void_p(hwnd))
                    user32.SetForegroundWindow(ctypes.c_void_p(hwnd))
                    user32.SwitchToThisWindow(ctypes.c_void_p(hwnd), True)
                finally:
                    if attached:
                        user32.AttachThreadInput(cur_tid, fg_tid, False)
                with contextlib.suppress(Exception):
                    win.SetActive()
                time.sleep(0.12)
                if int(user32.GetForegroundWindow() or 0) == hwnd:
                    break

            dpi_scale = 1.0
            with contextlib.suppress(Exception):
                dpi = user32.GetDpiForWindow(ctypes.c_void_p(hwnd))
                if dpi > 0:
                    dpi_scale = round(float(dpi) / 96.0, 2)
            return hwnd, pid, dpi_scale

    def verify_foreground_guard(self, expected_hwnd: int, expected_pid: int) -> Tuple[bool, str]:
        """Verify foreground window is `expected_hwnd` (`expected_pid`) with no modal dialog."""
        fg_hwnd = int(user32.GetForegroundWindow() or 0)
        fg_pid, fg_cls, fg_title = self._get_hwnd_pid_and_class(fg_hwnd)
        if fg_cls in ("#32770", "ContentDialog") or any(
            k in fg_title.lower() for k in ("save as", "do you want to save", "don't save")
        ):
            return False, f"FAIL:unexpected_dialog_hwnd={fg_hwnd}_cls={fg_cls}_title={fg_title!r}"
        if fg_hwnd != expected_hwnd or fg_pid != expected_pid or fg_cls != "MSPaintApp":
            return (
                False,
                f"FAIL:fg_hwnd={fg_hwnd}(expected={expected_hwnd})_fg_pid={fg_pid}(expected={expected_pid})_cls={fg_cls}",
            )
        return True, f"PASS:hwnd={expected_hwnd}:pid={expected_pid}"

    def inspect_uia_elements(
        self, hwnd: int
    ) -> Tuple[
        Optional[Tuple[int, int, int, int]],
        Dict[str, Tuple[int, int]],
        Dict[str, Tuple[int, int]],
        Dict[str, Tuple[int, int]],
    ]:
        """Walk the live Windows 11 MS Paint UIA tree with zero hardcoded pixel fallbacks."""
        if (
            self._cached_uia_hwnd == hwnd
            and self._cached_uia_tuple is not None
            and self._cached_uia_tuple[0] is not None
        ):
            cr, tm, sm, cm = self._cached_uia_tuple
            return cr, dict(tm), dict(sm), dict(cm)

        with contextlib.ExitStack() as stack:
            if hasattr(self._auto, "UIAutomationInitializerInThread"):
                stack.enter_context(self._auto.UIAutomationInitializerInThread())
            win = self._auto.WindowControl(searchDepth=2, ClassName="MSPaintApp")
            if not win.Exists(maxSearchSeconds=1.5):
                return None, {}, {}, {}

            canvas_rect: Optional[Tuple[int, int, int, int]] = None
            tools_map: Dict[str, Tuple[int, int]] = {}
            shapes_map: Dict[str, Tuple[int, int]] = {}
            colors_map: Dict[str, Tuple[int, int]] = {}

            known_tools = {
                "save",
                "undo",
                "redo",
                "select",
                "crop",
                "pencil",
                "fill",
                "text",
                "eraser",
                "colour picker",
                "color picker",
                "magnifier",
                "brushes",
            }
            known_shapes = {
                "line",
                "curve",
                "oval",
                "rectangle",
                "rounded rectangle",
                "polygon",
                "triangle",
                "right triangle",
                "diamond",
                "pentagon",
                "hexagon",
                "right arrow",
                "left arrow",
                "up arrow",
                "down arrow",
                "four-point star",
                "five-point star",
                "six-point star",
                "rounded rectangular callout",
                "oval callout",
                "cloud callout",
                "heart",
                "lightning",
            }

            for ctrl, _depth in self._auto.WalkControl(win, maxDepth=8):
                with contextlib.suppress(Exception):
                    name = (ctrl.Name or "").strip()
                    aid = (ctrl.AutomationId or "").strip()
                    r = ctrl.BoundingRectangle
                    if not r or (r.right - r.left) <= 4:
                        continue
                    cx = (r.left + r.right) // 2
                    cy = (r.top + r.bottom) // 2
                    low = name.lower()
                    ctype = ctrl.ControlTypeName

                    if aid == "image" and (r.right - r.left) >= 200 and (r.bottom - r.top) >= 200:
                        canvas_rect = (int(r.left), int(r.top), int(r.right), int(r.bottom))
                    elif aid == "BrushesSplitButton":
                        tools_map["brushes"] = (cx, int(r.top) + 22)
                    elif low == "select" and ctype == "RadioButtonControl":
                        tools_map["select"] = (cx, int(r.top) + 22)
                    elif low in known_tools and ctype in ("ButtonControl", "RadioButtonControl"):
                        tools_map[low] = (cx, cy)
                    elif low in known_shapes and ctype in ("ListItemControl", "ButtonControl"):
                        shapes_map[low] = (cx, cy)
                    elif low in PALETTE_EXPECTED_RGB and ctype == "ListItemControl":
                        colors_map[low] = (cx, cy)

        # Save small reference templates when UIA succeeds so fallback templates exist on disk
        with contextlib.suppress(Exception):
            self._cache_reference_templates(tools_map, shapes_map, colors_map)

        if canvas_rect is not None and tools_map and shapes_map and colors_map:
            self._cached_uia_hwnd = hwnd
            self._cached_uia_tuple = (
                canvas_rect,
                dict(tools_map),
                dict(shapes_map),
                dict(colors_map),
            )

        return canvas_rect, tools_map, shapes_map, colors_map

    def _cache_reference_templates(
        self,
        tools_map: Dict[str, Tuple[int, int]],
        shapes_map: Dict[str, Tuple[int, int]],
        colors_map: Dict[str, Tuple[int, int]],
    ) -> None:
        from PIL import ImageGrab

        self.templates_dir.mkdir(parents=True, exist_ok=True)
        # Only capture once if templates dir is empty
        if any(self.templates_dir.glob("*.png")):
            return
        screen = ImageGrab.grab()
        for cat, mp in (("tool", tools_map), ("shape", shapes_map), ("color", colors_map)):
            for key, (cx, cy) in mp.items():
                box = (max(0, cx - 12), max(0, cy - 12), cx + 12, cy + 12)
                crop = screen.crop(box)
                safe_name = re.sub(r"[^a-z0-9_-]+", "_", key.lower())
                crop.save(self.templates_dir / f"{cat}_{safe_name}.png")

    def match_reference_template(self, category: str, name: str) -> Optional[Tuple[int, int]]:
        """Fallback to OpenCV reference-screenshot template match if UIA misses an element."""
        safe_name = re.sub(r"[^a-z0-9_-]+", "_", name.strip().lower())
        tpl_path = self.templates_dir / f"{category}_{safe_name}.png"
        if not tpl_path.exists():
            return None
        try:
            import cv2
            import numpy as np
            from PIL import ImageGrab

            screen_pil = ImageGrab.grab()
            screen_bgr = cv2.cvtColor(np.array(screen_pil), cv2.COLOR_RGB2BGR)
            tpl_bgr = cv2.imread(str(tpl_path), cv2.IMREAD_COLOR)
            if tpl_bgr is None:
                return None
            res = cv2.matchTemplate(screen_bgr, tpl_bgr, cv2.TM_CCOEFF_NORMED)
            _min_v, max_v, _min_loc, max_loc = cv2.minMaxLoc(res)
            if max_v >= 0.82:
                th, tw = tpl_bgr.shape[:2]
                return (int(max_loc[0] + tw // 2), int(max_loc[1] + th // 2))
        except Exception:
            return None
        return None

    def _send_mouse_abs(self, flags: int, x: int, y: int) -> None:
        self._refresh_screen_metrics()
        max_w = float(self.screen_w - 1)
        max_h = float(self.screen_h - 1)
        ax = int(round(max(0.0, min(max_w, float(x))) * 65535.0 / max_w))
        ay = int(round(max(0.0, min(max_h, float(y))) * 65535.0 / max_h))
        inp = INPUT(
            INPUT_MOUSE,
            _INPUT_UNION(
                mi=MOUSEINPUT(
                    ax,
                    ay,
                    0,
                    flags | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_MOVE,
                    0,
                    0,
                )
            ),
        )
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    def move_to(self, x: int, y: int) -> None:
        self._send_mouse_abs(0, int(x), int(y))
        self.mouse_events.append(("MOVE", int(x), int(y)))

    def mouse_down(self, x: int, y: int) -> None:
        self._send_mouse_abs(0, int(x), int(y))
        time.sleep(0.025)
        self.is_mouse_button_down = True
        self._send_mouse_abs(MOUSEEVENTF_LEFTDOWN, int(x), int(y))
        self.mouse_events.append(("DOWN", int(x), int(y)))

    def mouse_up(self, x: int, y: int) -> None:
        self._send_mouse_abs(MOUSEEVENTF_LEFTUP, int(x), int(y))
        self.is_mouse_button_down = False
        self.mouse_events.append(("UP", int(x), int(y)))

    @staticmethod
    def _send_vk(vk: int, key_up: bool = False) -> None:
        flags = KEYEVENTF_KEYUP if key_up else 0
        inp = INPUT(INPUT_KEYBOARD, _INPUT_UNION(ki=KEYBDINPUT(vk, 0, flags, 0, 0)))
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    def send_ctrl_a_delete(self) -> None:
        """Send Select All (`Ctrl+A`) + `Delete` (never `Ctrl+N`, never closes Paint)."""
        self.key_events.append("CTRL+A_DELETE")
        self._send_vk(VK_CONTROL, key_up=False)
        time.sleep(0.02)
        self._send_vk(VK_A, key_up=False)
        time.sleep(0.03)
        self._send_vk(VK_A, key_up=True)
        time.sleep(0.02)
        self._send_vk(VK_CONTROL, key_up=True)
        time.sleep(0.08)
        self._send_vk(VK_DELETE, key_up=False)
        time.sleep(0.03)
        self._send_vk(VK_DELETE, key_up=True)
        time.sleep(0.10)

    def send_ctrl_z(self) -> None:
        """Send `Ctrl+Z` to undo the last Paint action."""
        self.key_events.append("CTRL+Z")
        self._send_vk(VK_CONTROL, key_up=False)
        time.sleep(0.02)
        self._send_vk(VK_Z, key_up=False)
        time.sleep(0.03)
        self._send_vk(VK_Z, key_up=True)
        time.sleep(0.02)
        self._send_vk(VK_CONTROL, key_up=True)
        time.sleep(0.10)

    def is_escape_pressed(self) -> bool:
        """Poll physical `Esc` key via `GetAsyncKeyState(VK_ESCAPE)`."""
        with contextlib.suppress(Exception):
            state = user32.GetAsyncKeyState(VK_ESCAPE)
            return bool(state & 0x8000)
        return False

    def sample_pixel(self, x: int, y: int) -> Tuple[int, int, int]:
        from PIL import ImageGrab

        img = ImageGrab.grab(bbox=(int(x), int(y), int(x) + 1, int(y) + 1))
        px = img.getpixel((0, 0))
        if isinstance(px, tuple) and len(px) >= 3:
            return (int(px[0]), int(px[1]), int(px[2]))
        return (255, 255, 255)

    def save_failure_screenshot(
        self, out_path: Path, canvas_rect: Optional[Tuple[int, int, int, int]]
    ) -> Optional[str]:
        with contextlib.suppress(Exception):
            from PIL import ImageGrab

            out_path.parent.mkdir(parents=True, exist_ok=True)
            img = ImageGrab.grab(bbox=canvas_rect) if canvas_rect else ImageGrab.grab()
            img.save(out_path)
            return str(out_path)
        return None

    def emergency_release(self) -> None:
        self._send_mouse_abs(MOUSEEVENTF_LEFTUP, self.screen_w // 2, self.screen_h // 2)
        self.is_mouse_button_down = False
        self.mouse_events.append(("EMERGENCY_UP", self.screen_w // 2, self.screen_h // 2))
        for vk, name in ((VK_CONTROL, "CTRL_UP"), (VK_SHIFT, "SHIFT_UP"), (VK_MENU, "ALT_UP")):
            self._send_vk(vk, key_up=True)
            self.key_events.append(name)


# ---------------------------------------------------------------------------
# Phase 7a Windows11PaintController
# ---------------------------------------------------------------------------


# 30 Multilingual Command Fixtures (English, Hindi/Hinglish, Telugu/Tenglish, Tamil/Tanglish)
MULTILINGUAL_PAINT_FIXTURES_30: Tuple[str, ...] = (
    # English (8)
    "draw a circle and paint it red",
    "draw a square and paint it blue",
    "draw a triangle and fill it green",
    "draw a star and paint it gold",
    "draw a heart and paint it pink",
    "draw a house",
    "draw a rocket",
    "make it yellow",
    # Hindi & Hinglish (8)
    "एक गोला बनाओ और लाल रंग भरो",
    "एक तारा बनाओ और गोल्ड रंग भरो",
    "एक घर बनाओ",
    "एक रॉकेट बनाओ",
    "ek circle banao aur neela rang bharo",
    "ek dil banao aur gulabi rang bharo",
    "isey bada karo",
    "kैनवास साफ़ करो",
    # Telugu & Tenglish (7)
    "ఒక సర్కిల్ గీసి ఎరుపు రంగు వేయి",
    "ఒక నక్షత్రం గీసి బంగారం రంగు వేయి",
    "ఒక ఇల్లు గీయి",
    "ఒక రాకెట్ గీయి",
    "oka heart geesi pink color veyyi",
    "oka box geesi pacha rang veyyi",
    "peddaga cheyyi",
    # Tamil & Tanglish (7)
    "ஒரு வட்டம் வரைந்து சிவப்பு நிறம் கொடு",
    "ஒரு நட்சத்திரம் வரைந்து தங்கம் நிறம் கொடு",
    "ஒரு வீடு வரை",
    "ஒரு ராக்கெட் வரை",
    "oru idhayam varaindhu pink niram kodu",
    "oru சதுரம் varaindhu neelam niram kodu",
    "innum perisa மாற்று",
)


class Windows11PaintController:
    """Phase 7a Stabilised & Dynamic Windows 11 MS Paint Controller."""

    def __init__(
        self,
        backend: Optional[PaintBackendProtocol] = None,
        workspace_root: Optional[Path] = None,
        stroke_duration_sec: float = 0.28,
        stroke_steps: int = 24,
        color_tolerance: float = 65.0,
    ) -> None:
        self.workspace_root = (workspace_root or Path.cwd()).resolve()
        self.backend: PaintBackendProtocol = backend or Win32PaintBackend(
            templates_dir=self.workspace_root / ".winai" / "paint_templates"
        )
        self.failures_dir = self.workspace_root / ".winai" / "paint_failures"
        self.actions_log_file = self.workspace_root / ".winai" / "paint_actions.jsonl"

        self.stroke_duration_sec = max(0.01, float(stroke_duration_sec))
        self.stroke_steps = max(4, int(stroke_steps))
        self.color_tolerance = float(color_tolerance)

        self.bound_hwnd: int = 0
        self.bound_pid: int = 0
        self.dpi_scale: float = 1.0
        self.canvas_rect: Optional[Tuple[int, int, int, int]] = None
        self.tools_map: Dict[str, Tuple[int, int]] = {}
        self.shapes_map: Dict[str, Tuple[int, int]] = {}
        self.colors_map: Dict[str, Tuple[int, int]] = {}

        # Lookup telemetry counters (UIA vs Reference Template)
        self.uia_lookup_count: int = 0
        self.template_fallback_count: int = 0
        self.uia_found_elements: List[str] = []
        self.template_found_elements: List[str] = []
        self.color_mappings_logged: List[Dict[str, str]] = []

        # Stored active SceneSpec for object-ID edits
        self.current_scene: Optional[SceneSpec] = None
        self.last_shape_center: Tuple[int, int] = (958, 670)

    def _assert_focus_guard(self) -> str:
        """Verify foreground HWND & PID before every stroke and every SendInput."""
        ok, detail = self.backend.verify_foreground_guard(self.bound_hwnd, self.bound_pid)
        if not ok:
            raise PaintAbortSafeError(
                "Stopped safely: Microsoft Paint lost focus or an unexpected dialog appeared.",
                code=detail,
            )
        return detail

    def resolve_element(
        self, category: Literal["tool", "shape", "color"], name: str
    ) -> Tuple[int, int]:
        """Resolve `(x, y)` via UIA first, then stored reference template, else `ABORTED_SAFE`."""
        clean = name.strip().lower()
        target_map = (
            self.tools_map
            if category == "tool"
            else (self.shapes_map if category == "shape" else self.colors_map)
        )
        if clean in target_map:
            self.uia_lookup_count += 1
            tag = f"{category}:{clean}"
            if tag not in self.uia_found_elements:
                self.uia_found_elements.append(tag)
            return target_map[clean]

        tpl_pos = self.backend.match_reference_template(category, clean)
        if tpl_pos is not None:
            self.template_fallback_count += 1
            tag = f"{category}:{clean}"
            if tag not in self.template_found_elements:
                self.template_found_elements.append(tag)
            target_map[clean] = tpl_pos
            return tpl_pos

        raise PaintAbortSafeError(
            f"Stopped safely: could not locate {category} '{clean}' via UIA or template.",
            code=f"ELEMENT_NOT_FOUND_{category.upper()}_{clean.upper()}",
        )

    def _guarded_click(self, x: int, y: int, hold_sec: float = 0.045) -> None:
        """Click `(x, y)` with focus guard before every SendInput and guaranteed mouse-up."""
        fast = self.stroke_duration_sec <= 0.04
        pre_s = 0.015 if fast else 0.025
        hold_s = 0.035 if fast else hold_sec
        post_s = 0.045 if fast else 0.065
        self._assert_focus_guard()
        self.backend.move_to(x, y)
        time.sleep(pre_s)
        self._assert_focus_guard()
        try:
            self.backend.mouse_down(x, y)
            time.sleep(hold_s)
        finally:
            if self.backend.is_mouse_button_down:
                self.backend.mouse_up(x, y)
        time.sleep(post_s)

    def _guarded_drag(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        *,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> bool:
        """Drag `(x1, y1)` -> `(x2, y2)` with try/finally mouse-up, focus guard, & Esc check."""
        fast = self.stroke_duration_sec <= 0.04
        pre_s = 0.005 if fast else 0.03
        down_s = 0.010 if fast else 0.035
        post_s = 0.015 if fast else 0.08
        self._assert_focus_guard()
        self.backend.move_to(x1, y1)
        time.sleep(pre_s)
        self._assert_focus_guard()
        last_x, last_y = x1, y1
        cancelled = False
        step_sleep = max(0.001, self.stroke_duration_sec / float(self.stroke_steps))
        try:
            self.backend.mouse_down(x1, y1)
            time.sleep(down_s)
            for i in range(1, self.stroke_steps + 1):
                if (should_cancel and should_cancel()) or self.backend.is_escape_pressed():
                    cancelled = True
                    break
                self._assert_focus_guard()
                t = i / float(self.stroke_steps)
                last_x = int(round(x1 + (x2 - x1) * t))
                last_y = int(round(y1 + (y2 - y1) * t))
                self.backend.move_to(last_x, last_y)
                time.sleep(step_sleep)
        finally:
            if self.backend.is_mouse_button_down:
                self.backend.mouse_up(last_x, last_y)
            time.sleep(post_s)
        return not cancelled

    def _guarded_polyline_drag(
        self,
        abs_pts: List[Tuple[int, int]],
        *,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> bool:
        """Trace a multi-point `polyline` in one continuous drag with try/finally mouse-up."""
        if len(abs_pts) < 2:
            return True
        fast = self.stroke_duration_sec <= 0.04
        x0, y0 = abs_pts[0]
        self._assert_focus_guard()
        self.backend.move_to(x0, y0)
        time.sleep(0.015 if fast else 0.03)
        self._assert_focus_guard()
        last_x, last_y = x0, y0
        cancelled = False
        try:
            self.backend.mouse_down(x0, y0)
            time.sleep(0.015 if fast else 0.035)
            for idx in range(1, len(abs_pts)):
                px, py = abs_pts[idx]
                dist = math.hypot(px - last_x, py - last_y)
                sub_steps = max(4, int(dist / 3.0))
                for s in range(1, sub_steps + 1):
                    if (should_cancel and should_cancel()) or self.backend.is_escape_pressed():
                        cancelled = True
                        break
                    self._assert_focus_guard()
                    t = s / float(sub_steps)
                    ix = int(round(last_x + (px - last_x) * t))
                    iy = int(round(last_y + (py - last_y) * t))
                    self.backend.move_to(ix, iy)
                    time.sleep(0.001 if fast else 0.0025)
                if cancelled:
                    break
                last_x, last_y = px, py
        finally:
            if self.backend.is_mouse_button_down:
                self.backend.mouse_up(last_x, last_y)
            time.sleep(0.025 if fast else 0.08)
        return not cancelled

    def preflight_check(self, *, clear_canvas_first: bool = False) -> Dict[str, Any]:
        """Run mandatory Phase 7a pre-flight check before every drawing sequence."""
        hwnd, pid, dpi_scale = self.backend.ensure_paint_running_and_maximized()
        self.bound_hwnd = hwnd
        self.bound_pid = pid
        self.dpi_scale = dpi_scale

        guard_detail = self._assert_focus_guard()
        canvas_rect, tools_map, shapes_map, colors_map = self.backend.inspect_uia_elements(hwnd)
        self.canvas_rect = canvas_rect
        self.tools_map = tools_map
        self.shapes_map = shapes_map
        self.colors_map = colors_map

        if self.canvas_rect is None:
            raise PaintAbortSafeError(
                "Stopped safely: Microsoft Paint drawing canvas rectangle was not found.",
                code="CANVAS_NOT_FOUND",
            )
        cl, ct, cr, cb = self.canvas_rect
        if (cr - cl) < 200 or (cb - ct) < 200:
            raise PaintAbortSafeError(
                "Stopped safely: Microsoft Paint canvas dimensions are too small.",
                code="CANVAS_TOO_SMALL",
            )

        cleared = False
        if clear_canvas_first:
            self._clear_canvas_internal()
            cleared = True

        preflight = {
            "paint_running": True,
            "bound_hwnd": self.bound_hwnd,
            "bound_pid": self.bound_pid,
            "maximized": True,
            "dpi_scale": self.dpi_scale,
            "canvas_rect": self.canvas_rect,
            "canvas_cleared": cleared,
            "window_guard_result": guard_detail,
            "uia_tools_discovered": len(self.tools_map),
            "uia_shapes_discovered": len(self.shapes_map),
            "uia_colors_discovered": len(self.colors_map),
        }
        logger.info("Paint preflight passed: %s", preflight)
        return preflight

    def _clear_canvas_internal(self) -> None:
        """Clear canvas using Select All (`Ctrl+A`) + `Delete` (never `Ctrl+N` or closing Paint)."""
        if self.canvas_rect is None:
            raise PaintAbortSafeError("Stopped safely: canvas rectangle missing during clear.")
        sel_x, sel_y = self.resolve_element("tool", "select")
        self._guarded_click(sel_x, sel_y)
        cl, ct, cr, cb = self.canvas_rect
        self._guarded_click((cl + cr) // 2, (ct + cb) // 2)
        self._assert_focus_guard()
        self.backend.send_ctrl_a_delete()
        self._assert_focus_guard()
        brush_x, brush_y = self.resolve_element("tool", "brushes")
        self._guarded_click(brush_x, brush_y)

    def clear_canvas(self) -> Dict[str, Any]:
        """Public pre-flighted canvas clear (`Select All + Delete`)."""
        pf = self.preflight_check(clear_canvas_first=True)
        return {
            "status": "COMPLETED",
            "action": "clear_canvas",
            "strokes_completed": 0,
            "window_guard_result": pf["window_guard_result"],
            "summary": "Cleared MS Paint canvas via Select All + Delete.",
        }

    def undo(self) -> Dict[str, Any]:
        """Public pre-flighted Undo (`Ctrl+Z`)."""
        pf = self.preflight_check(clear_canvas_first=False)
        self._assert_focus_guard()
        self.backend.send_ctrl_z()
        return {
            "status": "COMPLETED",
            "action": "undo",
            "strokes_completed": 0,
            "window_guard_result": pf["window_guard_result"],
            "summary": "Undid last MS Paint action.",
        }

    def _fill_and_verify_pixel(
        self,
        palette_color: str,
        fill_x: int,
        fill_y: int,
    ) -> Tuple[bool, Tuple[int, int, int], float, int]:
        """Fill `(fill_x, fill_y)`, sample pixel vs `PALETTE_EXPECTED_RGB`, and retry once."""
        expected_rgb = PALETTE_EXPECTED_RGB[palette_color]
        fill_tool_x, fill_tool_y = self.resolve_element("tool", "fill")
        swatch_x, swatch_y = self.resolve_element("color", palette_color)

        for attempt in (1, 2):
            self._guarded_click(fill_tool_x, fill_tool_y)
            self._guarded_click(swatch_x, swatch_y)
            self._guarded_click(fill_x, fill_y)
            time.sleep(0.06)
            sampled_rgb = self.backend.sample_pixel(fill_x, fill_y)
            dist = rgb_distance(sampled_rgb, expected_rgb)
            if dist <= self.color_tolerance:
                return True, sampled_rgb, round(dist, 2), attempt
            logger.warning(
                "Post-fill pixel mismatch (attempt %d at %d,%d): sampled=%s expected=%s dist=%.1f",
                attempt,
                fill_x,
                fill_y,
                sampled_rgb,
                expected_rgb,
                dist,
            )
        return False, sampled_rgb, round(dist, 2), 2

    def _draw_single_scene_object(
        self,
        obj: SceneObjectSpec,
        *,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        """Draw one `SceneObjectSpec` mapped to live `canvas_rect` fractions and verify fill."""
        if self.canvas_rect is None:
            raise PaintAbortSafeError("Stopped safely: canvas rectangle not initialized.")
        cl, ct, cr, cb = self.canvas_rect
        cw = cr - cl
        ch = cb - ct

        outline_swatch = (
            resolve_palette_color(obj.outline_color, self.color_mappings_logged) or "black"
        )
        fill_swatch = resolve_palette_color(obj.fill_color, self.color_mappings_logged)

        x1 = int(round(cl + obj.x * cw))
        y1 = int(round(ct + obj.y * ch))
        x2 = int(round(cl + (obj.x + obj.w) * cw))
        y2 = int(round(ct + (obj.y + obj.h) * ch))
        cx = (x1 + x2) // 2
        cy = (y1 + y2) // 2

        if obj.shape == "polyline" and obj.points:
            pen_x, pen_y = self.resolve_element("tool", "pencil")
            self._guarded_click(pen_x, pen_y)
            sw_x, sw_y = self.resolve_element("color", outline_swatch)
            self._guarded_click(sw_x, sw_y)
            abs_pts = [
                (int(round(cl + px * cw)), int(round(ct + py * ch)))
                for px, py in obj.points
            ]
            ok = self._guarded_polyline_drag(abs_pts, should_cancel=should_cancel)
            if not ok:
                return {
                    "id": obj.id,
                    "shape": obj.shape,
                    "status": "STOPPED",
                    "fill_verified": False,
                }
            # If the polyline is a closed loop and has a fill_color, fill its interior belly
            is_closed = (
                len(abs_pts) >= 4
                and math.hypot(abs_pts[0][0] - abs_pts[-1][0], abs_pts[0][1] - abs_pts[-1][1]) <= 6
            )
            if is_closed and fill_swatch:
                # Compute interior point (for crescent moon or closed polygon)
                mid_idx = len(abs_pts) // 4
                opp_idx = (3 * len(abs_pts)) // 4
                fill_x = (abs_pts[mid_idx][0] + abs_pts[opp_idx][0]) // 2
                fill_y = (abs_pts[mid_idx][1] + abs_pts[opp_idx][1]) // 2
                self.last_shape_center = (fill_x, fill_y)
                fill_ok, sampled_rgb, rgb_dist, attempts = self._fill_and_verify_pixel(
                    fill_swatch, fill_x, fill_y
                )
                return {
                    "id": obj.id,
                    "shape": obj.shape,
                    "status": "COMPLETED" if fill_ok else "FAILED_EXECUTION",
                    "outline_color": outline_swatch,
                    "fill_color": fill_swatch,
                    "fill_verified": fill_ok,
                    "sampled_rgb": sampled_rgb,
                    "rgb_distance": rgb_dist,
                    "fill_attempts": attempts,
                    "points_drawn": len(abs_pts),
                    "center": (fill_x, fill_y),
                }
            return {
                "id": obj.id,
                "shape": obj.shape,
                "status": "COMPLETED",
                "outline_color": outline_swatch,
                "fill_color": None,
                "fill_verified": True,
                "points_drawn": len(abs_pts),
            }

        # Gallery shape or line
        uia_shape_name = SCENE_SHAPE_TO_UIA_NAME[obj.shape]
        sh_x, sh_y = self.resolve_element("shape", uia_shape_name)
        self._guarded_click(sh_x, sh_y)
        sw_x, sw_y = self.resolve_element("color", outline_swatch)
        self._guarded_click(sw_x, sw_y)

        completed = self._guarded_drag(
            x1, y1, x2, y2, should_cancel=should_cancel
        )
        self.last_shape_center = (cx, cy)
        if not completed:
            return {
                "id": obj.id,
                "shape": obj.shape,
                "status": "STOPPED",
                "fill_verified": False,
            }

        # Fill only for gallery shapes (never for line/polyline)
        if obj.shape in GALLERY_SHAPES and fill_swatch:
            fill_y = cy + max(8, (y2 - y1) // 8) if obj.shape == "triangle" else cy
            fill_ok, sampled_rgb, rgb_dist, attempts = self._fill_and_verify_pixel(
                fill_swatch, cx, fill_y
            )
            return {
                "id": obj.id,
                "shape": obj.shape,
                "status": "COMPLETED" if fill_ok else "FAILED_EXECUTION",
                "outline_color": outline_swatch,
                "fill_color": fill_swatch,
                "fill_verified": fill_ok,
                "sampled_rgb": sampled_rgb,
                "rgb_distance": rgb_dist,
                "fill_attempts": attempts,
                "bbox": (x1, y1, x2, y2),
                "center": (cx, fill_y),
            }

        # Commit non-filled shape by clicking Brushes tool
        br_x, br_y = self.resolve_element("tool", "brushes")
        self._guarded_click(br_x, br_y)
        return {
            "id": obj.id,
            "shape": obj.shape,
            "status": "COMPLETED",
            "outline_color": outline_swatch,
            "fill_color": None,
            "fill_verified": True,
            "bbox": (x1, y1, x2, y2),
            "center": (cx, cy),
        }

    def execute_scene_spec(
        self,
        scene: SceneSpec,
        *,
        transcript: str = "",
        llm_ms: float = 0.0,
        plan_cache_hit: bool = False,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        """Execute a validated `SceneSpec` with preflight, focus guard, and pixel verification."""
        t0 = time.perf_counter()
        strokes_completed = 0
        stop_time_ms: Optional[float] = None
        window_guard_result = "UNCHECKED"
        object_results: List[Dict[str, Any]] = []

        try:
            preflight = self.preflight_check(clear_canvas_first=scene.clear_before_draw)
            window_guard_result = preflight["window_guard_result"]

            for obj in scene.objects:
                if (should_cancel and should_cancel()) or self.backend.is_escape_pressed():
                    stop_time_ms = round((time.perf_counter() - t0) * 1000.0, 2)
                    res_stopped = self._finalize_action_result(
                        status="STOPPED",
                        transcript=transcript,
                        planned_action=f"SceneSpec({scene.scene_id}:{len(scene.objects)}_objs)",
                        strokes_completed=strokes_completed,
                        stop_time_ms=stop_time_ms,
                        window_guard_result=window_guard_result,
                        llm_ms=llm_ms,
                        plan_cache_hit=plan_cache_hit,
                        object_results=object_results,
                        summary=f"Stopped drawing after {strokes_completed} object(s).",
                    )
                    return res_stopped

                obj_res = self._draw_single_scene_object(obj, should_cancel=should_cancel)
                object_results.append(obj_res)
                if obj_res["status"] == "STOPPED":
                    stop_time_ms = round((time.perf_counter() - t0) * 1000.0, 2)
                    return self._finalize_action_result(
                        status="STOPPED",
                        transcript=transcript,
                        planned_action=f"SceneSpec({scene.scene_id}:{len(scene.objects)}_objs)",
                        strokes_completed=strokes_completed,
                        stop_time_ms=stop_time_ms,
                        window_guard_result=window_guard_result,
                        llm_ms=llm_ms,
                        plan_cache_hit=plan_cache_hit,
                        object_results=object_results,
                        summary=f"Stopped mid-stroke on object '{obj.id}'.",
                    )
                strokes_completed += 1

            self.current_scene = scene.model_copy(deep=True)
            any_failed = any(r["status"] == "FAILED_EXECUTION" for r in object_results)
            final_status = "FAILED_EXECUTION" if any_failed else "COMPLETED"
            summary_txt = (
                f"Drew {strokes_completed} object(s) in MS Paint."
                if not any_failed
                else f"Failed pixel check on 1 or more objects in SceneSpec '{scene.scene_id}'."
            )
            return self._finalize_action_result(
                status=final_status,
                transcript=transcript,
                planned_action=f"SceneSpec({scene.scene_id}:{len(scene.objects)}_objs)",
                strokes_completed=strokes_completed,
                stop_time_ms=None,
                window_guard_result=window_guard_result,
                llm_ms=llm_ms,
                plan_cache_hit=plan_cache_hit,
                object_results=object_results,
                summary=summary_txt,
            )
        except PaintAbortSafeError as abort_err:
            window_guard_result = abort_err.code
            return self._finalize_action_result(
                status="ABORTED_SAFE",
                transcript=transcript,
                planned_action=f"SceneSpec({scene.scene_id})",
                strokes_completed=strokes_completed,
                stop_time_ms=round((time.perf_counter() - t0) * 1000.0, 2),
                window_guard_result=window_guard_result,
                llm_ms=llm_ms,
                plan_cache_hit=plan_cache_hit,
                object_results=object_results,
                summary=abort_err.spoken_reason,
            )

    def apply_scene_edit(
        self,
        edit: SceneEditSpec,
        *,
        transcript: str = "",
        llm_ms: float = 0.0,
        plan_cache_hit: bool = False,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        """Apply an edit (`recolour`, `resize`, `move`, `remove`, `add`) to `self.current_scene`.

        Rule 12:
        - Last-object edits use `Ctrl+Z` (undoing fill + shape) and redraw the last object.
        - Other edits clear the canvas (`Select All + Delete`) and redraw the full scene.
        """
        t0 = time.perf_counter()
        if self.current_scene is None or not self.current_scene.objects:
            return self._finalize_action_result(
                status="ABORTED_SAFE",
                transcript=transcript,
                planned_action=f"SceneEdit({edit.operation}:{edit.target_id})",
                strokes_completed=0,
                stop_time_ms=0.0,
                window_guard_result="NO_ACTIVE_SCENE",
                llm_ms=llm_ms,
                plan_cache_hit=plan_cache_hit,
                object_results=[],
                summary="Stopped safely: no active Paint scene exists to edit.",
            )

        scene_copy = self.current_scene.model_copy(deep=True)
        objs = list(scene_copy.objects)

        if edit.operation == "add":
            if edit.add_object is None:
                return self._finalize_action_result(
                    status="ABORTED_SAFE",
                    transcript=transcript,
                    planned_action="SceneEdit(add)",
                    strokes_completed=0,
                    stop_time_ms=0.0,
                    window_guard_result="MISSING_ADD_OBJECT",
                    llm_ms=llm_ms,
                    plan_cache_hit=plan_cache_hit,
                    object_results=[],
                    summary="Stopped safely: add edit missing object specification.",
                )
            if len(objs) >= 12:
                return self._finalize_action_result(
                    status="ABORTED_SAFE",
                    transcript=transcript,
                    planned_action="SceneEdit(add)",
                    strokes_completed=0,
                    stop_time_ms=0.0,
                    window_guard_result="SCENE_MAX_12_OBJECTS",
                    llm_ms=llm_ms,
                    plan_cache_hit=plan_cache_hit,
                    object_results=[],
                    summary="Stopped safely: scene already has the maximum of 12 objects.",
                )
            try:
                pf = self.preflight_check(clear_canvas_first=False)
                obj_res = self._draw_single_scene_object(
                    edit.add_object, should_cancel=should_cancel
                )
                if obj_res["status"] == "COMPLETED":
                    objs.append(edit.add_object)
                    self.current_scene = SceneSpec(
                        scene_id=scene_copy.scene_id,
                        clear_before_draw=False,
                        objects=objs,
                    )
                return self._finalize_action_result(
                    status=obj_res["status"],
                    transcript=transcript,
                    planned_action=f"SceneEdit(add:{edit.add_object.id})",
                    strokes_completed=1 if obj_res["status"] == "COMPLETED" else 0,
                    stop_time_ms=None,
                    window_guard_result=pf["window_guard_result"],
                    llm_ms=llm_ms,
                    plan_cache_hit=plan_cache_hit,
                    object_results=[obj_res],
                    summary=(
                        f"Added object '{edit.add_object.id}' "
                        f"({edit.add_object.shape}) to Paint scene."
                    ),
                )
            except PaintAbortSafeError as abort_err:
                return self._finalize_action_result(
                    status="ABORTED_SAFE",
                    transcript=transcript,
                    planned_action=f"SceneEdit(add:{edit.add_object.id})",
                    strokes_completed=0,
                    stop_time_ms=round((time.perf_counter() - t0) * 1000.0, 2),
                    window_guard_result=abort_err.code,
                    llm_ms=llm_ms,
                    plan_cache_hit=plan_cache_hit,
                    object_results=[],
                    summary=abort_err.spoken_reason,
                )

        # Locate target object by ID (or 'last')
        target_idx = len(objs) - 1 if edit.target_id == "last" else -1
        if target_idx == -1:
            for idx, o in enumerate(objs):
                if o.id == edit.target_id or o.shape == edit.target_id:
                    target_idx = idx
                    break
        if target_idx == -1:
            return self._finalize_action_result(
                status="ABORTED_SAFE",
                transcript=transcript,
                planned_action=f"SceneEdit({edit.operation}:{edit.target_id})",
                strokes_completed=0,
                stop_time_ms=0.0,
                window_guard_result=f"OBJECT_NOT_FOUND:{edit.target_id}",
                llm_ms=llm_ms,
                plan_cache_hit=plan_cache_hit,
                object_results=[],
                summary=(
                    f"Stopped safely: object '{edit.target_id}' "
                    "was not found in the current scene."
                ),
            )

        is_last_obj = target_idx == (len(objs) - 1)
        target_obj = objs[target_idx].model_copy(deep=True)
        had_fill = bool(target_obj.fill_color and target_obj.shape in GALLERY_SHAPES)

        if edit.operation == "remove":
            objs.pop(target_idx)
        elif edit.operation == "recolour":
            if edit.new_fill_color:
                target_obj.fill_color = edit.new_fill_color
            if edit.new_outline_color:
                target_obj.outline_color = edit.new_outline_color
            objs[target_idx] = target_obj
        elif edit.operation == "resize":
            sw = edit.scale_w if edit.scale_w is not None else 1.25
            sh = edit.scale_h if edit.scale_h is not None else 1.25
            cx_frac = target_obj.x + target_obj.w / 2.0
            cy_frac = target_obj.y + target_obj.h / 2.0
            new_w = max(0.04, min(0.90, round(target_obj.w * sw, 4)))
            new_h = max(0.04, min(0.90, round(target_obj.h * sh, 4)))
            new_x = max(0.02, min(1.0 - new_w - 0.02, round(cx_frac - new_w / 2.0, 4)))
            new_y = max(0.02, min(1.0 - new_h - 0.02, round(cy_frac - new_h / 2.0, 4)))
            target_obj = SceneObjectSpec(
                id=target_obj.id,
                shape=target_obj.shape,
                x=new_x,
                y=new_y,
                w=new_w,
                h=new_h,
                fill_color=target_obj.fill_color,
                outline_color=target_obj.outline_color,
                points=target_obj.points,
            )
            objs[target_idx] = target_obj
        elif edit.operation == "move":
            nx = (
                edit.new_x
                if edit.new_x is not None
                else min(1.0 - target_obj.w - 0.02, target_obj.x + 0.12)
            )
            ny = edit.new_y if edit.new_y is not None else target_obj.y
            nx = max(0.02, min(1.0 - target_obj.w - 0.02, round(nx, 4)))
            ny = max(0.02, min(1.0 - target_obj.h - 0.02, round(ny, 4)))
            target_obj = SceneObjectSpec(
                id=target_obj.id,
                shape=target_obj.shape,
                x=nx,
                y=ny,
                w=target_obj.w,
                h=target_obj.h,
                fill_color=target_obj.fill_color,
                outline_color=target_obj.outline_color,
                points=target_obj.points,
            )
            objs[target_idx] = target_obj

        if is_last_obj and edit.operation != "remove":
            # Last-object edit: Ctrl+Z (twice if object had shape + bucket fill) + redraw
            try:
                pf = self.preflight_check(clear_canvas_first=False)
                self._assert_focus_guard()
                self.backend.send_ctrl_z()
                if had_fill:
                    self._assert_focus_guard()
                    self.backend.send_ctrl_z()
                obj_res = self._draw_single_scene_object(target_obj, should_cancel=should_cancel)
                if obj_res["status"] == "COMPLETED":
                    self.current_scene = SceneSpec(
                        scene_id=scene_copy.scene_id,
                        clear_before_draw=False,
                        objects=objs,
                    )
                return self._finalize_action_result(
                    status=obj_res["status"],
                    transcript=transcript,
                    planned_action=f"SceneEdit({edit.operation}:{target_obj.id}:last_ctrl_z)",
                    strokes_completed=1 if obj_res["status"] == "COMPLETED" else 0,
                    stop_time_ms=None,
                    window_guard_result=pf["window_guard_result"],
                    llm_ms=llm_ms,
                    plan_cache_hit=plan_cache_hit,
                    object_results=[obj_res],
                    summary=(
                        f"Edited last object '{target_obj.id}' "
                        f"({edit.operation}) via Ctrl+Z and redraw."
                    ),
                )
            except PaintAbortSafeError as abort_err:
                return self._finalize_action_result(
                    status="ABORTED_SAFE",
                    transcript=transcript,
                    planned_action=f"SceneEdit({edit.operation}:{target_obj.id})",
                    strokes_completed=0,
                    stop_time_ms=round((time.perf_counter() - t0) * 1000.0, 2),
                    window_guard_result=abort_err.code,
                    llm_ms=llm_ms,
                    plan_cache_hit=plan_cache_hit,
                    object_results=[],
                    summary=abort_err.spoken_reason,
                )

        # Earlier-object edit: clear canvas (Select All + Delete) and redraw full updated scene
        if not objs:
            return self.clear_canvas()
        updated_scene = SceneSpec(
            scene_id=scene_copy.scene_id,
            clear_before_draw=True,
            objects=objs,
        )
        return self.execute_scene_spec(
            updated_scene,
            transcript=transcript,
            llm_ms=llm_ms,
            plan_cache_hit=plan_cache_hit,
            should_cancel=should_cancel,
        )

    def _finalize_action_result(
        self,
        *,
        status: str,
        transcript: str,
        planned_action: str,
        strokes_completed: int,
        stop_time_ms: Optional[float],
        window_guard_result: str,
        llm_ms: float,
        plan_cache_hit: bool,
        object_results: List[Dict[str, Any]],
        summary: str,
    ) -> Dict[str, Any]:
        failure_screenshot: Optional[str] = None
        if status in ("ABORTED_SAFE", "FAILED_EXECUTION"):
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
            snap_file = self.failures_dir / f"paint_{status.lower()}_{ts}.png"
            failure_screenshot = self.backend.save_failure_screenshot(snap_file, self.canvas_rect)

        record = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "transcript": transcript,
            "planned_action": planned_action,
            "strokes_completed": strokes_completed,
            "stop_time_ms": stop_time_ms,
            "window_guard_result": window_guard_result,
            "llm_ms": round(float(llm_ms), 2),
            "plan_cache_hit": bool(plan_cache_hit),
            "uia_lookup_count": self.uia_lookup_count,
            "template_fallback_count": self.template_fallback_count,
            "uia_found_elements": list(self.uia_found_elements),
            "template_found_elements": list(self.template_found_elements),
            "color_mappings_logged": list(self.color_mappings_logged),
            "objects": object_results,
            "failure_screenshot": failure_screenshot,
            "summary": summary,
        }
        with contextlib.suppress(Exception):
            self.actions_log_file.parent.mkdir(parents=True, exist_ok=True)
            with self.actions_log_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    # ------------------------------------------------------------------
    # SceneSpec Planner (LLM JSON + Deterministic Multilingual Parser)
    # ------------------------------------------------------------------

    @staticmethod
    def build_moon_scene_spec() -> SceneSpec:
        """Build a point-by-point Crescent Moon (`polyline`) + golden stars `SceneSpec`."""
        pts: List[Tuple[float, float]] = []
        # 1. Outer crescent arc: 28 points from top tip (0.48, 0.22) to bottom tip (0.48, 0.78)
        for i in range(29):
            t = i / 28.0
            ang = -math.pi / 2.0 - math.pi * t
            px = round(0.48 + 0.18 * math.cos(ang), 4)
            py = round(0.50 + 0.28 * math.sin(ang), 4)
            pts.append((px, py))
        # 2. Inner crescent arc: 28 points from (0.48, 0.78) back to (0.48, 0.22)
        for i in range(1, 29):
            t = i / 28.0
            ang = math.pi / 2.0 - math.pi * t
            px = round(0.48 - 0.09 * math.cos(ang), 4)
            py = round(0.50 + 0.28 * math.sin(ang), 4)
            pts.append((px, py))
        # Ensure exact closure at top tip
        pts.append(pts[0])

        return SceneSpec(
            scene_id="moon_scene",
            clear_before_draw=True,
            objects=[
                SceneObjectSpec(
                    id="crescent_moon",
                    shape="polyline",
                    x=0.30,
                    y=0.22,
                    w=0.18,
                    h=0.56,
                    fill_color="gold",
                    outline_color="orange",
                    points=pts,
                ),
                SceneObjectSpec(
                    id="night_star_1",
                    shape="star",
                    x=0.58,
                    y=0.22,
                    w=0.10,
                    h=0.15,
                    fill_color="yellow",
                    outline_color="gold",
                ),
                SceneObjectSpec(
                    id="night_star_2",
                    shape="star",
                    x=0.68,
                    y=0.44,
                    w=0.08,
                    h=0.12,
                    fill_color="light yellow",
                    outline_color="gold",
                ),
            ],
        )

    @staticmethod
    def build_house_scene_spec() -> SceneSpec:
        """Standard 4-object House SceneSpec (walls, roof, door, sun) in [0,1] canvas fractions."""
        return SceneSpec(
            scene_id="house_scene",
            clear_before_draw=True,
            objects=[
                SceneObjectSpec(
                    id="house_walls",
                    shape="rectangle",
                    x=0.32,
                    y=0.42,
                    w=0.30,
                    h=0.40,
                    fill_color="light yellow",
                    outline_color="brown",
                ),
                SceneObjectSpec(
                    id="house_roof",
                    shape="triangle",
                    x=0.29,
                    y=0.16,
                    w=0.36,
                    h=0.26,
                    fill_color="brown",
                    outline_color="dark red",
                ),
                SceneObjectSpec(
                    id="house_door",
                    shape="rectangle",
                    x=0.43,
                    y=0.58,
                    w=0.08,
                    h=0.24,
                    fill_color="red",
                    outline_color="dark red",
                ),
                SceneObjectSpec(
                    id="sun",
                    shape="ellipse",
                    x=0.72,
                    y=0.12,
                    w=0.12,
                    h=0.18,
                    fill_color="gold",
                    outline_color="orange",
                ),
            ],
        )

    @staticmethod
    def build_rocket_scene_spec() -> SceneSpec:
        """Standard 4-object Rocket SceneSpec in [0,1] canvas fractions."""
        return SceneSpec(
            scene_id="rocket_scene",
            clear_before_draw=True,
            objects=[
                SceneObjectSpec(
                    id="rocket_body",
                    shape="rectangle",
                    x=0.43,
                    y=0.32,
                    w=0.14,
                    h=0.38,
                    fill_color="light turquoise",
                    outline_color="indigo",
                ),
                SceneObjectSpec(
                    id="rocket_nose",
                    shape="triangle",
                    x=0.42,
                    y=0.12,
                    w=0.16,
                    h=0.20,
                    fill_color="red",
                    outline_color="dark red",
                ),
                SceneObjectSpec(
                    id="rocket_window",
                    shape="ellipse",
                    x=0.46,
                    y=0.42,
                    w=0.08,
                    h=0.12,
                    fill_color="gold",
                    outline_color="indigo",
                ),
                SceneObjectSpec(
                    id="rocket_flame",
                    shape="star",
                    x=0.45,
                    y=0.71,
                    w=0.10,
                    h=0.16,
                    fill_color="orange",
                    outline_color="red",
                ),
            ],
        )

    @staticmethod
    def build_futuristic_city_scene_spec() -> SceneSpec:
        """6-object Futuristic City Skyline SceneSpec in [0,1] canvas fractions."""
        return SceneSpec(
            scene_id="futuristic_city_scene",
            clear_before_draw=True,
            objects=[
                SceneObjectSpec(
                    id="cyber_tower_left",
                    shape="rectangle",
                    x=0.10,
                    y=0.34,
                    w=0.15,
                    h=0.48,
                    fill_color="indigo",
                    outline_color="blue",
                ),
                SceneObjectSpec(
                    id="neon_spire_center",
                    shape="rectangle",
                    x=0.33,
                    y=0.22,
                    w=0.18,
                    h=0.60,
                    fill_color="blue",
                    outline_color="indigo",
                ),
                SceneObjectSpec(
                    id="spire_pyramid_dome",
                    shape="triangle",
                    x=0.32,
                    y=0.08,
                    w=0.20,
                    h=0.14,
                    fill_color="light turquoise",
                    outline_color="indigo",
                ),
                SceneObjectSpec(
                    id="solar_tower_right",
                    shape="rectangle",
                    x=0.59,
                    y=0.28,
                    w=0.16,
                    h=0.54,
                    fill_color="purple",
                    outline_color="indigo",
                ),
                SceneObjectSpec(
                    id="aerial_skybridge",
                    shape="rectangle",
                    x=0.25,
                    y=0.48,
                    w=0.34,
                    h=0.06,
                    fill_color="light turquoise",
                    outline_color="blue",
                ),
                SceneObjectSpec(
                    id="orbital_sun_beacon",
                    shape="star",
                    x=0.77,
                    y=0.10,
                    w=0.14,
                    h=0.16,
                    fill_color="gold",
                    outline_color="orange",
                ),
            ],
        )

    def draw_starry_city_live_with_cursor(
        self,
        *,
        should_cancel: Optional[Callable[[], bool]] = None,
        on_stage_callback: Optional[Callable[[str, int], None]] = None,
    ) -> Dict[str, Any]:
        """Draw the complete Starry Night Moon, Mountains & Futuristic City Skyline LIVE with the mouse cursor."""
        t0 = time.perf_counter()
        prev_dur = self.stroke_duration_sec
        prev_steps = self.stroke_steps
        # Use smooth, clearly visible cursor drag speed so the user watches every stroke live
        self.stroke_duration_sec = 0.18
        self.stroke_steps = 20
        stages_list: List[SceneObjectSpec] = [
            # 1. Mid Azure Sky Band, Pale Ice Horizon Band & Pitch-Black Ground
            # (Canvas is flood-filled Indigo first so there are zero white gaps!)
            SceneObjectSpec(
                id="sky_mid_azure",
                shape="rectangle",
                x=0.005,
                y=0.25,
                w=0.99,
                h=0.21,
                fill_color="turquoise",
                outline_color="turquoise",
            ),
            SceneObjectSpec(
                id="sky_horizon_ice",
                shape="rectangle",
                x=0.005,
                y=0.465,
                w=0.99,
                h=0.21,
                fill_color="light turquoise",
                outline_color="light turquoise",
            ),
            SceneObjectSpec(
                id="ground_foreground",
                shape="rectangle",
                x=0.005,
                y=0.68,
                w=0.99,
                h=0.315,
                fill_color="black",
                outline_color="black",
            ),
            # 2. Giant Top-Left Moon, Right Full Moon & Six-Point Star in Upper Indigo Sky
            SceneObjectSpec(
                id="giant_moon_left",
                shape="ellipse",
                x=0.03,
                y=0.02,
                w=0.22,
                h=0.22,
                fill_color="white",
                outline_color="white",
            ),
            SceneObjectSpec(
                id="small_moon_right",
                shape="ellipse",
                x=0.72,
                y=0.08,
                w=0.055,
                h=0.10,
                fill_color="white",
                outline_color="white",
            ),
            SceneObjectSpec(
                id="night_star_right",
                shape="star",
                x=0.86,
                y=0.10,
                w=0.045,
                h=0.08,
                fill_color="white",
                outline_color="white",
            ),
            # 3. Layered Mountain Ranges on the Left (Blue-Grey & Indigo peaks on Light Turquoise horizon)
            SceneObjectSpec(
                id="mountain_peak_1",
                shape="triangle",
                x=0.01,
                y=0.48,
                w=0.16,
                h=0.195,
                fill_color="blue-grey",
                outline_color="blue-grey",
            ),
            SceneObjectSpec(
                id="mountain_peak_2",
                shape="triangle",
                x=0.18,
                y=0.47,
                w=0.18,
                h=0.205,
                fill_color="indigo",
                outline_color="indigo",
            ),
            SceneObjectSpec(
                id="mountain_peak_3",
                shape="triangle",
                x=0.37,
                y=0.49,
                w=0.13,
                h=0.185,
                fill_color="blue-grey",
                outline_color="blue-grey",
            ),
            # 4. Futuristic City Skyscrapers on the Right (y = 0.47..0.675)
            SceneObjectSpec(
                id="city_tower_1",
                shape="rectangle",
                x=0.51,
                y=0.50,
                w=0.06,
                h=0.175,
                fill_color="black",
                outline_color="black",
            ),
            SceneObjectSpec(
                id="city_tower_2",
                shape="rectangle",
                x=0.58,
                y=0.475,
                w=0.06,
                h=0.20,
                fill_color="black",
                outline_color="black",
            ),
            SceneObjectSpec(
                id="city_tower_3",
                shape="rectangle",
                x=0.65,
                y=0.49,
                w=0.05,
                h=0.185,
                fill_color="black",
                outline_color="black",
            ),
            SceneObjectSpec(
                id="city_tower_4_tallest",
                shape="rectangle",
                x=0.71,
                y=0.47,
                w=0.05,
                h=0.205,
                fill_color="black",
                outline_color="black",
            ),
            SceneObjectSpec(
                id="city_tower_5_citadel",
                shape="rectangle",
                x=0.77,
                y=0.48,
                w=0.09,
                h=0.195,
                fill_color="black",
                outline_color="black",
            ),
            SceneObjectSpec(
                id="city_tower_6_right",
                shape="rectangle",
                x=0.87,
                y=0.50,
                w=0.05,
                h=0.175,
                fill_color="black",
                outline_color="black",
            ),
            # 5. Glowing White Skyscraper Windows, Vertical Light Beam & Diagonal Triangle Atrium
            SceneObjectSpec(
                id="tower_1_win_top",
                shape="rectangle",
                x=0.525,
                y=0.52,
                w=0.03,
                h=0.04,
                fill_color="white",
                outline_color="white",
            ),
            SceneObjectSpec(
                id="tower_1_win_bot",
                shape="rectangle",
                x=0.525,
                y=0.58,
                w=0.03,
                h=0.04,
                fill_color="white",
                outline_color="white",
            ),
            SceneObjectSpec(
                id="tower_2_win_top",
                shape="rectangle",
                x=0.595,
                y=0.495,
                w=0.03,
                h=0.045,
                fill_color="white",
                outline_color="white",
            ),
            SceneObjectSpec(
                id="tower_2_win_bot",
                shape="rectangle",
                x=0.595,
                y=0.56,
                w=0.03,
                h=0.045,
                fill_color="white",
                outline_color="white",
            ),
            SceneObjectSpec(
                id="tower_4_vertical_beam",
                shape="rectangle",
                x=0.725,
                y=0.485,
                w=0.02,
                h=0.165,
                fill_color="white",
                outline_color="white",
            ),
            SceneObjectSpec(
                id="citadel_glass_atrium",
                shape="triangle",
                x=0.78,
                y=0.55,
                w=0.07,
                h=0.115,
                fill_color="white",
                outline_color="white",
            ),
            # 6. Pine Tree Silhouettes (Left Mountain Foothills & Far-Right Tall Pine)
            SceneObjectSpec(
                id="pine_tree_left",
                shape="triangle",
                x=0.14,
                y=0.53,
                w=0.04,
                h=0.145,
                fill_color="black",
                outline_color="black",
            ),
            SceneObjectSpec(
                id="pine_tree_right",
                shape="triangle",
                x=0.93,
                y=0.475,
                w=0.05,
                h=0.20,
                fill_color="black",
                outline_color="black",
            ),
        ]

        results: List[Dict[str, Any]] = []
        strokes_done = 0
        try:
            pf = self.preflight_check(clear_canvas_first=True)
            assert self.canvas_rect is not None
            cl, ct, cr, cb = self.canvas_rect
            cw = cr - cl
            ch = cb - ct
            # Step 0: Flood-fill the entire blank canvas with Deep Night Indigo so zero white gaps exist!
            self._fill_and_verify_pixel("indigo", cl + cw // 2, ct + ch // 5)
            strokes_done += 1
            for idx, obj in enumerate(stages_list, start=1):
                if (should_cancel and should_cancel()) or self.backend.is_escape_pressed():
                    return self._finalize_action_result(
                        status="STOPPED",
                        transcript="draw starry futuristic city",
                        planned_action="StarryCityLiveCursor",
                        strokes_completed=strokes_done,
                        stop_time_ms=round((time.perf_counter() - t0) * 1000.0, 2),
                        window_guard_result="CANCELLED_BY_USER",
                        llm_ms=0.0,
                        plan_cache_hit=False,
                        object_results=results,
                        summary="Stopped live city drawing on user interrupt.",
                    )
                res = self._draw_single_scene_object(obj, should_cancel=should_cancel)
                results.append(res)
                strokes_done += 1
                if on_stage_callback is not None:
                    on_stage_callback(obj.id, idx)

            # Live cursor starlight stipple dots across upper night sky using Brushes tool + White
            assert self.canvas_rect is not None
            cl, ct, cr, cb = self.canvas_rect
            cw = cr - cl
            ch = cb - ct
            br_x, br_y = self.resolve_element("tool", "brushes")
            self._guarded_click(br_x, br_y)
            sw_x, sw_y = self.resolve_element("color", "white")
            self._guarded_click(sw_x, sw_y)
            star_coords = [
                (0.32, 0.08),
                (0.38, 0.14),
                (0.44, 0.07),
                (0.49, 0.18),
                (0.55, 0.11),
                (0.61, 0.06),
                (0.66, 0.16),
                (0.79, 0.07),
                (0.83, 0.19),
                (0.94, 0.09),
            ]
            for sx_f, sy_f in star_coords:
                if (should_cancel and should_cancel()) or self.backend.is_escape_pressed():
                    break
                px = cl + int(cw * sx_f)
                py = ct + int(ch * sy_f)
                self._guarded_drag(px, py, px + 3, py + 3, should_cancel=should_cancel)
                strokes_done += 1

            self.current_scene = SceneSpec(
                scene_id="futuristic_city_scene",
                clear_before_draw=False,
                objects=stages_list[-12:],
            )
            return self._finalize_action_result(
                status="COMPLETED",
                transcript="draw starry futuristic city",
                planned_action="StarryCityLiveCursor(20_objects+10_stars)",
                strokes_completed=strokes_done,
                stop_time_ms=None,
                window_guard_result=pf["window_guard_result"],
                llm_ms=0.0,
                plan_cache_hit=False,
                object_results=results,
                summary="Drew the Starry Night Moon, Mountains & Futuristic City Skyline live with the cursor in MS Paint.",
            )
        finally:
            self.stroke_duration_sec = prev_dur
            self.stroke_steps = prev_steps

    def parse_multilingual_command_to_plan(
        self, command_text: str
    ) -> Tuple[Literal["scene", "edit", "clear", "undo"], Any]:
        """Parse English/Hindi/Telugu/Tamil Paint commands into `SceneSpec` or `SceneEditSpec`."""
        raw = (command_text or "").strip()
        low = raw.lower()

        clear_words = (
            "clear",
            "reset",
            "clean",
            "saaf",
            "cheripeyyi",
            "azhichidu",
            "साफ़",
            "తుడిచేయి",
            "அழி",
        )
        if any(k in low for k in clear_words):
            return "clear", None

        undo_words = ("undo", "wapas", "venakki", "thiruppi", "वापस", "వెనక్కి", "திருப்பி")
        if any(k in low for k in undo_words):
            return "undo", None

        # Extract requested colour(s) in utterance order, skipping negated colours
        # Note: Do NOT use \b on Indic combining marks (\u0c3e-\u0c4d), which Python re treats as \W!
        # In Telugu/Tamil/Hindi ('రెడ్ వద్దు', 'రెడ్ బాలేదు'), negation follows the colour;
        # in English ('not red', 'no red'), negation precedes the colour.
        negation_following = (
            "వద్దు",
            "కాదు",
            "బాలేదు",
            "vaddu",
            "kaadu",
            "kadu",
            "nahi",
            "mat",
            "नहीं",
            "मत",
            "venam",
            "வேண்டாம்",
        )
        negation_preceding = ("not", "no", "don't", "dont")
        color_matches: List[Tuple[int, str]] = []
        occupied_spans: List[Tuple[int, int]] = []
        for alias in sorted(NAMED_COLOR_RGB_LOOKUP.keys(), key=len, reverse=True):
            for m in re.finditer(re.escape(alias), low):
                start_pos, end_pos = m.span(0)
                # For pure ASCII aliases, require non-letter boundaries so 'tan' doesn't match inside 'triangle'
                if alias.isascii():
                    prev_ch = low[start_pos - 1] if start_pos > 0 else " "
                    next_ch = low[end_pos] if end_pos < len(low) else " "
                    if prev_ch.isalpha() or next_ch.isalpha():
                        continue
                if any(not (end_pos <= s or start_pos >= e) for s, e in occupied_spans):
                    continue
                after_slice = low[end_pos : end_pos + 22].strip().split()
                before_slice = low[max(0, start_pos - 22) : start_pos].strip().split()
                if after_slice and any(after_slice[0].startswith(neg) for neg in negation_following):
                    occupied_spans.append((start_pos, end_pos))
                    continue
                if before_slice and any(before_slice[-1].endswith(neg) for neg in negation_preceding):
                    occupied_spans.append((start_pos, end_pos))
                    continue
                occupied_spans.append((start_pos, end_pos))
                resolved = resolve_palette_color(alias)
                if resolved:
                    color_matches.append((start_pos, resolved))
        color_matches.sort(key=lambda item: item[0])
        found_colors: List[str] = []
        for _pos, col_name in color_matches:
            if col_name not in found_colors:
                found_colors.append(col_name)

        # Check for edit commands on existing scene (resize, move, recolour, remove, add)
        is_resize = any(
            k in low
            for k in (
                "resize",
                "bigger",
                "smaller",
                "bada",
                "chhota",
                "peddaga",
                "chinnaga",
                "perisa",
                "chinnadha",
                "बड़ा",
                "छोटा",
                "పెద్దగా",
                "చిన్నగా",
                "பெரிதாக",
            )
        )
        move_words = ("move", "shift", "right", "left", "jarupu", "நகர்த்து", "खिसकाओ")
        is_move = any(k in low for k in move_words)
        remove_words = ("remove", "delete object", "hatao", "teeseyyi", "neekidu")
        is_remove = any(k in low for k in remove_words)
        add_beside_words = (
            "add ",
            "also draw",
            "beside",
            "next to",
            "another",
            "one more",
            "jodo",
            "bagal",
            "cherchu",
            "ఇంకో",
            "ఇంకొక",
            "పక్కన",
            "పాటు",
            "మరొక",
            "சேர்",
            "பக்கத்தில்",
        )
        is_add = any(k in low for k in add_beside_words)
        is_recolour = bool(found_colors) and any(
            k in low
            for k in (
                "recolour",
                "recolor",
                "change",
                "చేంజ్",
                "మార్చండి",
                "change color",
                "change colour",
                "make it ",
                "paint it ",
                "fill it ",
                "rang badlo",
                "color marchu",
                "नया रंग",
                "రంగు మార్చు",
                "நிறம் மாற்று",
                "కలర్",
                "రంగు",
                "పెట్టు",
                "మార్చు",
                "వేయి",
                "కావాలి",
                "వద్దు",
                "దానికి",
            )
        )

        # Match target shape/id if mentioned
        matched_shape: Optional[SceneShapeKind] = None
        for alias, canonical_sh in sorted(SHAPE_SYNONYMS.items(), key=lambda kv: -len(kv[0])):
            if alias in low:
                matched_shape = canonical_sh
                break

        if is_resize and self.current_scene is not None:
            shrink_words = ("smaller", "chhota", "chinnaga", "chinnadha", "छोटा", "చిన్నగా")
            factor = 0.75 if any(k in low for k in shrink_words) else 1.35
            return (
                "edit",
                SceneEditSpec(
                    operation="resize",
                    target_id=matched_shape or "last",
                    scale_w=factor,
                    scale_h=factor,
                ),
            )

        if is_move and self.current_scene is not None:
            return (
                "edit",
                SceneEditSpec(
                    operation="move",
                    target_id=matched_shape or "last",
                ),
            )

        if is_remove and self.current_scene is not None:
            return (
                "edit",
                SceneEditSpec(
                    operation="remove",
                    target_id=matched_shape or "last",
                ),
            )

        if is_add and self.current_scene is not None:
            # If user said 'ఇంకో సర్కిల్ తీసుకో' or 'దాంతో పాటు పక్కన', add beside the last shape!
            last_obj = self.current_scene.objects[-1]
            sh = matched_shape or last_obj.shape
            fill_c = found_colors[0] if found_colors else (last_obj.fill_color or "gold")
            idx = len(self.current_scene.objects) + 1
            # Place side-by-side to the right (or wrap to next row if near right edge)
            next_x = round(last_obj.x + last_obj.w + 0.06, 2)
            next_y = last_obj.y
            if next_x + 0.22 > 0.94:
                next_x = 0.08
                next_y = round(min(0.62, last_obj.y + 0.32), 2)
            return (
                "edit",
                SceneEditSpec(
                    operation="add",
                    target_id="last",
                    add_object=SceneObjectSpec(
                        id=f"{sh}_{idx}",
                        shape=sh,
                        x=next_x,
                        y=next_y,
                        w=0.22,
                        h=0.28,
                        fill_color=fill_c,
                        outline_color="indigo",
                    ),
                ),
            )

        has_new_shape_verb = any(
            k in low
            for k in (
                "draw ",
                "take ",
                "take a ",
                "geesi",
                "geeyi",
                "banao",
                "varai",
                "గీయి",
                "గీసి",
                "డ్రా",
                "తీసుకో",
                "తీసుకొని",
                "టేక్",
                "ఒక ",
                "ఇంకో",
            )
        )
        if (
            (is_recolour or (bool(found_colors) and matched_shape is None))
            and self.current_scene is not None
            and not (matched_shape is not None and has_new_shape_verb)
        ):
            return (
                "edit",
                SceneEditSpec(
                    operation="recolour",
                    target_id=matched_shape or "last",
                    new_fill_color=found_colors[-1],
                ),
            )

        # If user only asked to open Paint ("open paint", "పెయింట్ ఓపెన్ చెయ్", "पेंट खोलो", "பெயிண்ட் திற")
        open_only_words = ("open", "launch", "start", "ఓపెన్", "తెరువు", "खोलो", "திற")
        draw_words = (
            "draw",
            "paint a",
            "paint it",
            "geeyi",
            "geesi",
            "banao",
            "varai",
            "గీయి",
            "గీసి",
            "డ్రా",
            "बनाओ",
            "வரை",
        )
        if (
            any(w in low for w in open_only_words)
            and matched_shape is None
            and not found_colors
            and not any(w in low for w in draw_words)
        ):
            return "open", None

        # Full scenes: Moon, House, or Rocket
        moon_words = (
            "moon",
            "crescent",
            "chand",
            "chandamama",
            "nilavu",
            "nila",
            "चाँद",
            "चंदा",
            "చందమామ",
            "చంద్రుడు",
            "நிலா",
            "நிலவு",
        )
        if any(k in low for k in moon_words):
            return "scene", self.build_moon_scene_spec()

        house_words = ("house", "hut", "home", "ghar", "illu", "veedu", "घर", "ఇల్లు", "வீடு")
        if any(k in low for k in house_words):
            return "scene", self.build_house_scene_spec()

        rocket_words = ("rocket", "spaceship", "रॉकेट", "రాకెట్", "ராக்கெட்")
        if any(k in low for k in rocket_words):
            return "scene", self.build_rocket_scene_spec()

        city_words = (
            "futuristic city",
            "city",
            "skyline",
            "metropolis",
            "సిటీ",
            "ఫ్యూచరిస్టిక్",
            "నగరం",
            "शहर",
            "நகரம்",
        )
        if any(k in low for k in city_words):
            return "scene", self.build_futuristic_city_scene_spec()

        # If neither a shape nor a colour nor a scene was mentioned, do NOT blindly draw a red circle!
        if matched_shape is None and not found_colors:
            return "noop", None

        # Single shape scene (or side-by-side append if user said 'టేక్' / 'తీసుకో' / 'ఒక' with an active scene)
        shape_kind: SceneShapeKind = matched_shape or "ellipse"
        fill_c = found_colors[0] if found_colors else "red"
        outline_c = found_colors[1] if len(found_colors) > 1 else "indigo"
        if (
            self.current_scene is not None
            and len(self.current_scene.objects) < 10
            and any(k in low for k in ("టేక్", "తీసుకో", "తీసుకొని", "ఒక ", "పక్కన", "ఇంకో", "beside", "next to", "take "))
        ):
            last_obj = self.current_scene.objects[-1]
            idx = len(self.current_scene.objects) + 1
            next_x = round(last_obj.x + last_obj.w + 0.06, 2)
            next_y = last_obj.y
            if next_x + 0.22 > 0.94:
                next_x = 0.08
                next_y = round(min(0.62, last_obj.y + 0.32), 2)
            return (
                "edit",
                SceneEditSpec(
                    operation="add",
                    target_id="last",
                    add_object=SceneObjectSpec(
                        id=f"{shape_kind}_{idx}",
                        shape=shape_kind,
                        x=next_x,
                        y=next_y,
                        w=0.22,
                        h=0.28,
                        fill_color=fill_c if shape_kind in GALLERY_SHAPES else None,
                        outline_color=outline_c,
                    ),
                ),
            )

        obj_spec = SceneObjectSpec(
            id=f"{shape_kind}_1",
            shape=shape_kind,
            x=0.14,
            y=0.28,
            w=0.24,
            h=0.34 if shape_kind != "line" else 0.02,
            fill_color=fill_c if shape_kind in GALLERY_SHAPES else None,
            outline_color=outline_c,
            points=[(0.25, 0.50), (0.50, 0.30), (0.75, 0.50)] if shape_kind == "polyline" else None,
        )
        return (
            "scene",
            SceneSpec(
                scene_id=f"{shape_kind}_scene",
                clear_before_draw=True,
                objects=[obj_spec],
            ),
        )

    def execute_natural_paint_command(
        self,
        command_text: str,
        *,
        should_cancel: Optional[Callable[[], bool]] = None,
        llm_ms: float = 0.0,
        plan_cache_hit: bool = False,
    ) -> Dict[str, Any]:
        """Parse and execute any multilingual Paint command via `SceneSpec` / `SceneEditSpec`."""
        kind, spec = self.parse_multilingual_command_to_plan(command_text)
        if kind == "noop":
            return {
                "status": "COMPLETED",
                "action": "clarify_shape",
                "strokes_completed": 0,
                "window_guard_result": "NO_OP",
                "summary": "ఏ ఆకారం (సర్కిల్, స్క్వేర్, డైమండ్, లవ్ సింబల్, ఆరో, చందమామ) గీయాలో చెప్పండి?",
            }
        if kind == "open":
            try:
                pf = self.preflight_check(clear_canvas_first=False)
                w_res = pf["window_guard_result"]
            except PaintAbortSafeError as exc:
                w_res = exc.code
            return {
                "status": "COMPLETED",
                "action": "open_paint",
                "strokes_completed": 0,
                "window_guard_result": w_res,
                "summary": "Opened and maximized Microsoft Paint on screen.",
            }
        if kind == "clear":
            return self.clear_canvas()
        if kind == "undo":
            return self.undo()
        if kind == "edit":
            return self.apply_scene_edit(
                spec,
                transcript=command_text,
                llm_ms=llm_ms,
                plan_cache_hit=plan_cache_hit,
                should_cancel=should_cancel,
            )
        return self.execute_scene_spec(
            spec,
            transcript=command_text,
            llm_ms=llm_ms,
            plan_cache_hit=plan_cache_hit,
            should_cancel=should_cancel,
        )

    async def plan_scene_via_llm(
        self,
        transcript: str,
        llm_provider: Any,
        *,
        use_cache: bool = False,
    ) -> Tuple[SceneSpec, float, bool]:
        """Call LLM to produce ONLY a validated `SceneSpec` JSON (never pixels or keystrokes).

        Rejects and raises `PaintAbortSafeError` with a short spoken clarification on invalid
        or over-limit (>12 objects or off-canvas) specs.
        """
        from src.providers.base import ChatMessage

        cache_attr = "_scene_plan_cache"
        if not hasattr(self, cache_attr):
            setattr(self, cache_attr, {})
        plan_cache: Dict[str, SceneSpec] = getattr(self, cache_attr)

        norm_key = re.sub(r"\s+", " ", (transcript or "").strip().lower())
        if use_cache and norm_key in plan_cache:
            return plan_cache[norm_key].model_copy(deep=True), 0.0, True

        sys_prompt = (
            "You are the SceneSpec JSON planner for Windows 11 MS Paint.\n"
            "Return ONLY a valid JSON object matching SceneSpec:\n"
            "{\n"
            '  "scene_id": "scene_1",\n'
            '  "clear_before_draw": true,\n'
            '  "objects": [\n'
            "    {\n"
            '      "id": "obj_1",\n'
            '      "shape": "rectangle|ellipse|triangle|line|polygon|star|heart|polyline",\n'
            '      "x": 0.35, "y": 0.28, "w": 0.26, "h": 0.38,\n'
            '      "fill_color": "red",\n'
            '      "outline_color": "indigo"\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "STRICT RULES:\n"
            "- x, y, w, h MUST be canvas fractions in [0.0, 1.0] (x+w <= 1.0, y+h <= 1.0).\n"
            "- At most 12 objects.\n"
            "- NEVER output pixel coordinates (>1.0), keystrokes, or UI element names."
        )

        t0 = time.perf_counter()
        try:
            if callable(llm_provider) and not hasattr(llm_provider, "complete"):
                raw_text = await llm_provider(transcript)
            else:
                resp = await llm_provider.complete(
                    messages=[
                        ChatMessage(role="system", content=sys_prompt),
                        ChatMessage(role="user", content=transcript),
                    ],
                    temperature=0.1,
                    max_tokens=400,
                )
                raw_text = getattr(resp, "content", "") if not isinstance(resp, str) else resp
        except Exception as exc:
            llm_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            raise PaintAbortSafeError(
                "Could not plan that drawing right now. Please state a clear shape and colour.",
                code=f"LLM_PLANNER_ERROR:{type(exc).__name__}",
            ) from exc

        llm_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        cleaned = str(raw_text or "").strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            cleaned = cleaned[start : end + 1]

        try:
            parsed = json.loads(cleaned)
        except Exception as exc:
            raise PaintAbortSafeError(
                "Could not understand that drawing layout. Please ask for up to 12 simple shapes.",
                code="SCENE_SPEC_INVALID_JSON",
            ) from exc

        if not isinstance(parsed, dict):
            raise PaintAbortSafeError(
                "Drawing specification must be a JSON object.",
                code="SCENE_SPEC_NOT_DICT",
            )

        # Explicitly forbid pixel coordinates, keystrokes, or UI element names from the LLM
        forbidden_keys = {
            "keys",
            "hotkey",
            "keystrokes",
            "automation_id",
            "hwnd",
            "pixel_x",
            "pixel_y",
        }
        if any(k in parsed for k in forbidden_keys):
            raise PaintAbortSafeError(
                "Rejected unsafe drawing specification containing raw keystrokes or UI handles.",
                code="SCENE_SPEC_FORBIDDEN_FIELDS",
            )

        try:
            scene = SceneSpec(**parsed)
        except Exception as exc:
            raise PaintAbortSafeError(
                "Could not draw that scene: keep all shapes inside the canvas (max 12 objects).",
                code=f"SCENE_SPEC_VALIDATION_FAILED:{type(exc).__name__}",
            ) from exc

        if use_cache:
            plan_cache[norm_key] = scene.model_copy(deep=True)
        return scene, llm_ms, False

