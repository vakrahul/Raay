"""Local development server lifecycle management and selective browser verification (Module 6)."""
import asyncio
from pathlib import Path
import time
from typing import Any, Dict, List, Optional
import httpx
from playwright.async_api import async_playwright
from pydantic import BaseModel


class VerificationResult(BaseModel):
    is_up: bool
    status_code: int
    url: str
    page_title: str
    console_errors: List[str]
    screenshot_saved: bool
    screenshot_path: Optional[str] = None
    duration_ms: float


class DevServerVerifier:
    """Verifies rendered frontend and API states, capturing screenshots selectively to minimize token usage."""

    def __init__(self, base_url: str = "http://127.0.0.1:8000"):
        self.base_url = base_url.rstrip("/")

    async def wait_for_server(self, timeout_seconds: float = 10.0, poll_interval: float = 0.5) -> bool:
        """Poll server endpoint until ready or timed out."""
        start = time.time()
        async with httpx.AsyncClient(timeout=2.0) as client:
            while time.time() - start < timeout_seconds:
                try:
                    res = await client.get(self.base_url)
                    if res.status_code in [200, 404, 301, 302]:
                        return True
                except Exception:
                    pass
                await asyncio.sleep(poll_interval)
        return False

    async def verify_page(
        self,
        path: str = "/",
        capture_screenshot: bool = False,
        screenshot_dir: Optional[Path] = None,
    ) -> VerificationResult:
        """Inspect rendered page and collect console errors."""
        target_url = f"{self.base_url}{path}"
        start_time = time.perf_counter()
        console_errors = []
        page_title = ""
        status_code = 0
        saved_screenshot = False
        out_path = None

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()

                # Listen for browser console errors
                page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
                page.on("pageerror", lambda err: console_errors.append(str(err)))

                response = await page.goto(target_url, timeout=10000, wait_until="domcontentloaded")
                status_code = response.status if response else 0
                page_title = await page.title()

                # Selectively capture screenshot only if requested or if errors detected (Token conservation)
                if capture_screenshot or len(console_errors) > 0:
                    out_dir = screenshot_dir or Path("tests/verification_shots")
                    out_dir.mkdir(parents=True, exist_ok=True)
                    out_path = str(out_dir / f"verify_{int(time.time()*1000)}.png")
                    await page.screenshot(path=out_path)
                    saved_screenshot = True

                await browser.close()

            duration = (time.perf_counter() - start_time) * 1000
            return VerificationResult(
                is_up=(status_code == 200),
                status_code=status_code,
                url=target_url,
                page_title=page_title,
                console_errors=console_errors,
                screenshot_saved=saved_screenshot,
                screenshot_path=out_path,
                duration_ms=duration,
            )

        except Exception as e:
            duration = (time.perf_counter() - start_time) * 1000
            return VerificationResult(
                is_up=False,
                status_code=0,
                url=target_url,
                page_title="",
                console_errors=[str(e)],
                screenshot_saved=False,
                duration_ms=duration,
            )
