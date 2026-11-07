"""Always-On-Top Movable Floating Circle Voice Orb for WinAI-OE.

Behavior:
- Stays completely hidden while no target application is open.
- Automatically appears (`deiconify`) ONLY when an application (such as Notepad,
  Calculator, Chrome, VS Code, or Edge) is opened by WinAI-OE, and automatically
  hides (`withdraw`) when that application is closed.
- Uses Windows `-transparentcolor` so the widget is a pure round circle (no square border).
- Freely movable/draggable anywhere on the screen (`<B1-Motion>`), while short-click
  toggles microphone listening / sends speech to type into the active app.
"""

import argparse
import base64
import contextlib
import math
import threading
import winsound
from typing import Any, Dict, Optional

import httpx

TRANSPARENT_KEY = "#010203"


class FloatingVoiceOrbOverlay:
    """Movable, circular always-on-top voice orb that appears only when an app is open."""

    ORB_SIZE = 104

    def __init__(self, base_url: str = "http://127.0.0.1:8765") -> None:
        import tkinter as tk

        self._tk = tk
        self.base_url = base_url.rstrip("/")
        self.state = "IDLE"  # IDLE, LISTENING, THINKING, APPROVAL, SPEAKING, SUCCESS, ERROR
        self.active_session_id: Optional[str] = None
        self.pending_nonce: Optional[str] = None
        self.current_context_app: Optional[str] = None
        self.is_visible: bool = False
        self.phase = 0.0

        self._drag_start_root: Optional[tuple] = None
        self._win_start_pos: tuple = (0, 0)
        self._was_dragged: bool = False

        self.root = tk.Tk()
        self.root.title("WinAI Voice Orb")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-toolwindow", True)
        self.root.configure(bg=TRANSPARENT_KEY)
        with contextlib.suppress(Exception):
            # Make the square window bounding box 100% transparent on Windows
            self.root.wm_attributes("-transparentcolor", TRANSPARENT_KEY)
            self.root.attributes("-alpha", 0.96)

        self._place_orb_bottom_right()

        self.canvas = tk.Canvas(
            self.root,
            width=self.ORB_SIZE,
            height=self.ORB_SIZE,
            bg=TRANSPARENT_KEY,
            highlightthickness=0,
            bd=0,
            cursor="fleur",
        )
        self.canvas.pack(fill="both", expand=True)

        # Bind smooth drag + click on both canvas and root
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        self.toast_win: Optional[Any] = None

        # Start hidden until an application is opened!
        self.root.withdraw()

        self._animate_loop()
        self._poll_app_and_approvals_loop()

    def _place_orb_bottom_right(self) -> None:
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = max(20, sw - self.ORB_SIZE - 40)
        y = max(20, sh - self.ORB_SIZE - 120)
        self.root.geometry(f"{self.ORB_SIZE}x{self.ORB_SIZE}+{x}+{y}")

    # ------------------------------------------------------------------
    # Visibility Control (Only Shown When an Application Is Open)
    # ------------------------------------------------------------------

    def show_orb(self, app_name: str) -> None:
        first_show = not self.is_visible or (self.current_context_app != app_name)
        self.current_context_app = app_name
        if not self.is_visible:
            self.is_visible = True
            self.root.deiconify()
            self.root.attributes("-topmost", True)
        if first_show:
            self.show_toast(
                title=f"{app_name.capitalize()} Active · Voice Ready",
                message=(
                    f"Drag this circle anywhere, or tap it and speak in Tamil, Telugu, "
                    f"Malayalam, Hindi, or English to write in {app_name.capitalize()}!"
                ),
                kind="info",
            )

    def hide_orb(self) -> None:
        if self.is_visible:
            self.is_visible = False
            self.current_context_app = None
            if self.toast_win is not None and self.toast_win.winfo_exists():
                self.toast_win.destroy()
                self.toast_win = None
            self.root.withdraw()

    # ------------------------------------------------------------------
    # Smooth Dragging & Click Disambiguation
    # ------------------------------------------------------------------

    def _on_press(self, event: Any) -> None:
        self._drag_start_root = (event.x_root, event.y_root)
        self._win_start_pos = (self.root.winfo_x(), self.root.winfo_y())
        self._was_dragged = False

    def _on_drag(self, event: Any) -> None:
        if not self._drag_start_root:
            return
        dx = event.x_root - self._drag_start_root[0]
        dy = event.y_root - self._drag_start_root[1]
        if abs(dx) + abs(dy) >= 6:
            self._was_dragged = True
        nx = self._win_start_pos[0] + dx
        ny = self._win_start_pos[1] + dy
        self.root.geometry(f"{self.ORB_SIZE}x{self.ORB_SIZE}+{nx}+{ny}")
        if self.toast_win is not None and self.toast_win.winfo_exists():
            self._position_toast_near_orb()

    def _on_release(self, _event: Any) -> None:
        was_drag = self._was_dragged
        self._drag_start_root = None
        self._was_dragged = False
        if not was_drag:
            self.on_orb_clicked()

    # ------------------------------------------------------------------
    # 60fps Pure Circular Animated Renderer (Zero Square Box)
    # ------------------------------------------------------------------

    def _animate_loop(self) -> None:
        if self.is_visible:
            self.phase += 0.14 if self.state in ("LISTENING", "SPEAKING", "THINKING") else 0.05
            c = self.canvas
            c.delete("all")
            cx = self.ORB_SIZE / 2
            cy = self.ORB_SIZE / 2

            palette = {
                "IDLE": ("#38bdf8", "#d4a849", "SPEAK"),
                "LISTENING": ("#ef4444", "#38bdf8", "SEND"),
                "THINKING": ("#818cf8", "#38bdf8", "AI..."),
                "APPROVAL": ("#f59e0b", "#fbbf24", "ALLOW?"),
                "SPEAKING": ("#10b981", "#38bdf8", "VOICE"),
                "SUCCESS": ("#10b981", "#d4a849", "DONE"),
                "ERROR": ("#ef4444", "#f59e0b", "RETRY"),
            }
            primary, secondary, label = palette.get(self.state, palette["IDLE"])

            # Outer solid dark disc so the circle is opaque while corners are transparent
            disc_r = 44
            c.create_oval(
                cx - disc_r,
                cy - disc_r,
                cx + disc_r,
                cy + disc_r,
                fill="#0b101d",
                outline=primary,
                width=2.2,
            )

            # Animated harmonic wave rings inside the circular disc
            for ring in range(3):
                wave = math.sin(self.phase - ring * 0.75) * (
                    5.0 if self.state in ("LISTENING", "SPEAKING") else 2.0
                )
                r = 24 + ring * 6.5 + wave
                color = primary if ring % 2 == 0 else secondary
                c.create_oval(cx - r, cy - r, cx + r, cy + r, outline=color, width=1.8)

            # Inner core circle
            core_r = 20 + math.sin(self.phase * 1.3) * (2.5 if self.state == "LISTENING" else 1.0)
            c.create_oval(
                cx - core_r,
                cy - core_r,
                cx + core_r,
                cy + core_r,
                fill="#131c31",
                outline=primary,
                width=2.0,
            )

            c.create_text(
                cx,
                cy - 2,
                text=label,
                fill="#f8fafc",
                font=("Segoe UI", 8, "bold"),
            )
            c.create_text(
                cx,
                cy + 10,
                text="DRAG / TAP",
                fill="#94a3b8",
                font=("Segoe UI", 6),
            )

        self.root.after(30, self._animate_loop)

    # ------------------------------------------------------------------
    # Floating Toast & Approval Bubble above the Circle Orb
    # ------------------------------------------------------------------

    def _position_toast_near_orb(self) -> None:
        if self.toast_win is None or not self.toast_win.winfo_exists():
            return
        ox = self.root.winfo_x()
        oy = self.root.winfo_y()
        tx = max(16, ox - 230)
        ty = max(16, oy - 155)
        self.toast_win.geometry(f"320x142+{tx}+{ty}")

    def show_toast(
        self,
        title: str,
        message: str,
        kind: str = "info",
        approval_nonce: Optional[str] = None,
    ) -> None:
        if not self.is_visible:
            return
        tk = self._tk
        if self.toast_win is not None and self.toast_win.winfo_exists():
            self.toast_win.destroy()

        border_color = {
            "info": "#38bdf8",
            "success": "#10b981",
            "approval": "#f59e0b",
            "error": "#ef4444",
        }.get(kind, "#38bdf8")

        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-toolwindow", True)
        win.configure(bg="#101522", highlightbackground=border_color, highlightthickness=2)
        self.toast_win = win
        self._position_toast_near_orb()

        top = tk.Frame(win, bg="#101522")
        top.pack(fill="x", padx=12, pady=(8, 2))
        tk.Label(
            top,
            text=title[:38],
            fg=border_color,
            bg="#101522",
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        ).pack(side="left")
        tk.Button(
            top,
            text="✕",
            fg="#94a3b8",
            bg="#101522",
            bd=0,
            command=win.destroy,
            cursor="hand2",
        ).pack(side="right")

        tk.Label(
            win,
            text=message[:155],
            fg="#f1f5f9",
            bg="#101522",
            font=("Segoe UI", 8),
            wraplength=292,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=12, pady=4)

        btn_frame = tk.Frame(win, bg="#101522")
        btn_frame.pack(fill="x", padx=12, pady=(4, 8))

        if approval_nonce:
            tk.Button(
                btn_frame,
                text="Approve",
                bg="#10b981",
                fg="#032218",
                font=("Segoe UI", 8, "bold"),
                bd=0,
                padx=10,
                pady=3,
                cursor="hand2",
                command=lambda: self._resolve_approval_async(approval_nonce, True),
            ).pack(side="left", padx=(0, 6))
            tk.Button(
                btn_frame,
                text="Deny",
                bg="#ef4444",
                fg="#ffffff",
                font=("Segoe UI", 8, "bold"),
                bd=0,
                padx=10,
                pady=3,
                cursor="hand2",
                command=lambda: self._resolve_approval_async(approval_nonce, False),
            ).pack(side="left")
        else:
            tk.Button(
                btn_frame,
                text="Tap to Speak",
                bg="#38bdf8",
                fg="#06121e",
                font=("Segoe UI", 8, "bold"),
                bd=0,
                padx=10,
                pady=3,
                cursor="hand2",
                command=self.on_orb_clicked,
            ).pack(side="left")

    # ------------------------------------------------------------------
    # Voice Capture & Turn Execution via Background Thread
    # ------------------------------------------------------------------

    def on_orb_clicked(self) -> None:
        if self.state == "LISTENING":
            self.state = "THINKING"
            threading.Thread(target=self._stop_and_transcribe_worker, daemon=True).start()
        else:
            self.state = "LISTENING"
            threading.Thread(target=self._start_listening_worker, daemon=True).start()

    def _start_listening_worker(self) -> None:
        try:
            with httpx.Client(base_url=self.base_url, timeout=10.0) as client:
                r = client.post("/api/v1/voice/session/start", json={"start_capture": True})
                if r.status_code == 200:
                    data = r.json()
                    self.active_session_id = data.get("session_id")
                    self.root.after(
                        0,
                        lambda: self.show_toast(
                            "Listening...",
                            (
                                "Speak in Tamil, Telugu, Malayalam, Hindi, Kannada, "
                                "or English, then tap the circle again to write into "
                                f"{(self.current_context_app or 'Notepad').capitalize()}!"
                            ),
                            "info",
                        ),
                    )
                else:
                    self.state = "ERROR"
        except Exception as e:
            self.state = "ERROR"
            msg = str(e)
            self.root.after(0, lambda: self.show_toast("Mic Error", msg, "error"))

    def _stop_and_transcribe_worker(self) -> None:
        if not self.active_session_id:
            self.state = "IDLE"
            return
        sid = self.active_session_id
        try:
            with httpx.Client(base_url=self.base_url, timeout=30.0) as client:
                r = client.post(
                    f"/api/v1/voice/session/{sid}/stop",
                    json={"speak_response": True, "conversational_mode": True},
                )
                data = r.json()
                self.root.after(0, lambda: self._handle_turn_response(data))
        except Exception as e:
            self.state = "ERROR"
            msg = str(e)
            self.root.after(0, lambda: self.show_toast("Voice Error", msg, "error"))

    def _resolve_approval_async(self, nonce: str, approved: bool) -> None:
        self.state = "THINKING"
        sid = self.active_session_id or "orb_session"

        def _worker() -> None:
            try:
                with httpx.Client(base_url=self.base_url, timeout=25.0) as client:
                    r = client.post(
                        "/api/v1/voice/approval/resolve",
                        json={
                            "session_id": sid,
                            "approval_nonce": nonce,
                            "approved": approved,
                            "speak_response": True,
                        },
                    )
                    data = r.json()
                    self.root.after(0, lambda: self._handle_turn_response(data))
            except Exception as e:
                self.state = "ERROR"
                msg = str(e)
                self.root.after(0, lambda: self.show_toast("Approval Error", msg, "error"))

        threading.Thread(target=_worker, daemon=True).start()

    def _handle_turn_response(self, data: Dict[str, Any]) -> None:
        entry = data.get("activity_entry") or data.get("turn") or {}
        b64_audio = data.get("audio_base64")
        p_status = entry.get("pipeline_status", "")
        msg = entry.get("user_facing_message") or "Ready — tap the circle and speak!"

        if p_status == "WAITING_FOR_APPROVAL" and entry.get("pending_approval"):
            pa = entry["pending_approval"]
            self.pending_nonce = pa.get("approval_nonce")
            self.active_session_id = pa.get("session_id")
            self.state = "APPROVAL"
            self.show_toast(
                title=f"Approve: {pa.get('target_resource')}",
                message=msg,
                kind="approval",
                approval_nonce=self.pending_nonce,
            )
        elif p_status == "COMPLETED":
            self.pending_nonce = None
            self.state = "SPEAKING" if b64_audio else "SUCCESS"
            self.show_toast(
                title=f"Verified: {entry.get('action_type') or 'Done'}",
                message=msg,
                kind="success",
            )
        else:
            self.state = "IDLE"
            self.show_toast(
                title="WinAI Voice Assistant",
                message=msg,
                kind="info",
            )

        if b64_audio:
            with contextlib.suppress(Exception):
                wav_bytes = base64.b64decode(b64_audio)
                winsound.PlaySound(wav_bytes, winsound.SND_MEMORY | winsound.SND_ASYNC)

    def _poll_app_and_approvals_loop(self) -> None:
        """Poll /api/v1/voice/activity to show the Orb ONLY when an app is open."""

        def _check() -> None:
            with contextlib.suppress(Exception):
                with httpx.Client(base_url=self.base_url, timeout=1.5) as client:
                    r = client.get("/api/v1/voice/activity")
                    if r.status_code == 200:
                        d = r.json()
                        should_show = bool(d.get("overlay_should_show"))
                        ctx_app = d.get("active_context_app") or "notepad"
                        if should_show:
                            self.root.after(0, lambda: self.show_orb(ctx_app))
                        else:
                            self.root.after(0, self.hide_orb)
            return

        threading.Thread(target=_check, daemon=True).start()
        self.root.after(900, self._poll_app_and_approvals_loop)

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(description="WinAI-OE Movable Floating Circle Voice Orb")
    parser.add_argument("--server", default="http://127.0.0.1:8765")
    args = parser.parse_args()
    FloatingVoiceOrbOverlay(base_url=args.server).run()


if __name__ == "__main__":
    main()
