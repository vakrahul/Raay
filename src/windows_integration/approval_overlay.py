"""On-screen approval button for WinAI-OE (always-on-top overlay).

A small floating button visible on any screen. It polls the orchestrator's
pending-approval queue and turns red with a count badge when something needs
a decision. Clicking it opens the pending list with per-item Approve / Deny
buttons that call back into /api/v1/approval/respond.

GUI logic is kept in ApprovalOverlay (tkinter); all HTTP lives in
ApprovalServiceClient so it is unit-testable without a display.
"""

import argparse
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx


@dataclass
class PendingApproval:
    approval_nonce: str
    tool_name: str = ""
    target: str = ""
    session_id: str = ""


class ApprovalServiceClient:
    """Loopback HTTP client for the approval queue (same user, same machine)."""

    def __init__(self, base_url: str = "http://127.0.0.1:8765", transport: Optional[httpx.BaseTransport] = None):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, transport=transport, timeout=5.0)

    def get_pending(self) -> List[PendingApproval]:
        """Return currently awaiting approvals; empty list when offline."""
        try:
            resp = self._client.get("/api/v1/approval/pending")
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []
        items = []
        for entry in data.get("pending", []):
            if entry.get("approval_nonce"):
                items.append(PendingApproval(
                    approval_nonce=entry["approval_nonce"],
                    tool_name=entry.get("tool_name", ""),
                    target=entry.get("target", ""),
                    session_id=entry.get("session_id", ""),
                ))
        return items

    def respond(self, nonce: str, decision: str) -> bool:
        """Submit APPROVED/DENIED for a nonce. True when the server accepted it."""
        if decision not in ("APPROVED", "DENIED"):
            return False
        try:
            resp = self._client.post(
                "/api/v1/approval/respond",
                json={"approval_nonce": nonce, "user_decision": decision},
            )
            return resp.status_code == 200
        except Exception:
            return False

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass


class ApprovalOverlay:
    """Tiny always-on-top tkinter button + approval panel."""

    POLL_MS = 2000
    BADGE_SIZE = 76

    def __init__(self, client: ApprovalServiceClient):
        import tkinter as tk

        self._tk = tk
        self.client = client
        self.pending: List[PendingApproval] = []
        self.panel: Optional[Any] = None

        self.root = tk.Tk()
        self.root.title("WinAI Approvals")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-toolwindow", True)
        try:
            self.root.attributes("-alpha", 0.95)
        except Exception:
            pass
        self._place_initial()

        self.badge = tk.Label(
            self.root,
            text="\U0001F510",
            font=("Segoe UI Emoji", 26),
            bg="#1f2937",
            fg="#9ca3af",
            width=3,
            height=2,
            relief="raised",
            borderwidth=2,
        )
        self.badge.pack()
        self.badge.bind("<ButtonPress-1>", self._on_press)
        self.badge.bind("<B1-Motion>", self._on_drag)
        self.badge.bind("<ButtonRelease-1>", self._on_release)
        self._press_xy: Optional[tuple] = None
        self._window_xy: tuple = (0, 0)

        self.root.after(self.POLL_MS, self._poll)
        self._poll()

    def _place_initial(self) -> None:
        w = self.root.winfo_screenwidth()
        h = self.root.winfo_screenheight()
        x = max(0, w - self.BADGE_SIZE - 24)
        y = max(0, h - self.BADGE_SIZE - 80)
        self.root.geometry(f"{self.BADGE_SIZE}x{self.BADGE_SIZE}+{x}+{y}")

    # -- badge drag vs click -------------------------------------------------
    def _on_press(self, event: Any) -> None:
        self._press_xy = (event.x_root, event.y_root)
        self._window_xy = (self.root.winfo_x(), self.root.winfo_y())

    def _on_drag(self, event: Any) -> None:
        if not self._press_xy:
            return
        dx = event.x_root - self._press_xy[0]
        dy = event.y_root - self._press_xy[1]
        self.root.geometry(f"+{self._window_xy[0] + dx}+{self._window_xy[1] + dy}")

    def _on_release(self, event: Any) -> None:
        moved = 0
        if self._press_xy:
            moved = abs(event.x_root - self._press_xy[0]) + abs(event.y_root - self._press_xy[1])
        self._press_xy = None
        if moved < 6:
            self.toggle_panel()

    # -- polling + rendering -------------------------------------------------
    def _poll(self) -> None:
        # Synchronous: the server is loopback-local so this returns in
        # milliseconds. (An earlier threaded version updated tkinter widgets
        # from a worker thread, which tkinter does not allow.)
        try:
            self.pending = self.client.get_pending()
        except Exception:
            self.pending = []
        count = len(self.pending)
        if count > 0:
            self.badge.config(text=f"\U0001F6CE\n{count}", bg="#7f1d1d", fg="#ffffff")
        else:
            self.badge.config(text="\U0001F510", bg="#1f2937", fg="#9ca3af")
        if self.panel is not None and self.panel.winfo_exists():
            self._render_panel()
        self.root.after(self.POLL_MS, self._poll)

    def toggle_panel(self) -> None:
        if self.panel is not None and self.panel.winfo_exists():
            self.panel.destroy()
            self.panel = None
            return
        tk = self._tk
        self.panel = tk.Toplevel(self.root)
        self.panel.title("Pending approvals")
        self.panel.attributes("-topmost", True)
        self.panel.geometry(f"380x10+{self.root.winfo_x() - 320}+{self.root.winfo_y() - 120}")
        self._render_panel()

    def _render_panel(self) -> None:
        tk = self._tk
        assert self.panel is not None
        for child in self.panel.winfo_children():
            child.destroy()
        if not self.pending:
            tk.Label(self.panel, text="No pending approvals.", padx=12, pady=12).pack()
            self.panel.geometry("230x60")
            return
        for item in self.pending:
            frame = tk.Frame(self.panel, relief="groove", borderwidth=1, padx=8, pady=6)
            frame.pack(fill="x", padx=8, pady=4)
            tk.Label(frame, text=f"{item.tool_name}", font=("Segoe UI", 10, "bold")).pack(anchor="w")
            tk.Label(frame, text=f"Target: {item.target[:80]}", font=("Segoe UI", 9)).pack(anchor="w")
            tk.Label(frame, text=f"Session: {item.session_id}", font=("Segoe UI", 8), fg="gray").pack(anchor="w")
            btns = tk.Frame(frame)
            btns.pack(anchor="e", pady=(4, 0))
            tk.Button(btns, text="Approve",
                      command=lambda n=item.approval_nonce: self._decide(n, "APPROVED")).pack(side="left", padx=2)
            tk.Button(btns, text="Deny",
                      command=lambda n=item.approval_nonce: self._decide(n, "DENIED")).pack(side="left", padx=2)
        self.panel.geometry(f"400x{min(120 + 110 * len(self.pending), 600)}")

    def _decide(self, nonce: str, decision: str) -> None:
        self.client.respond(nonce, decision)
        self._poll()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(description="WinAI-OE on-screen approval button")
    parser.add_argument("--server", default="http://127.0.0.1:8765")
    args = parser.parse_args()
    client = ApprovalServiceClient(base_url=args.server)
    try:
        ApprovalOverlay(client).run()
    finally:
        client.close()


if __name__ == "__main__":
    main()
