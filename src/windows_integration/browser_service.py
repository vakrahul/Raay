"""Controlled Browser Automation Service (Phase 66).

Uses Playwright with installed Google Chrome binary under explicit user policy approval.
Never steals cookies or circumvents authentication controls.
"""

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional
from playwright.async_api import async_playwright
from pydantic import BaseModel


class BrowserActionResult(BaseModel):
    success: bool
    url: str
    page_title: str
    first_post_text: Optional[str] = None
    author: Optional[str] = None
    notes: Optional[str] = None


class BrowserService:
    """Provides controlled browser automation using the system's Google Chrome."""

    CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

    def __init__(self, executable_path: Optional[str] = None):
        self.executable_path = executable_path or self.CHROME_PATH

    async def open_x_and_read_first_tweet(self, headless: bool = False, timeout_ms: int = 30000) -> BrowserActionResult:
        """Launch Google Chrome, navigate to X (Twitter), and extract the first visible post."""
        if not Path(self.executable_path).exists():
            return BrowserActionResult(
                success=False,
                url="",
                page_title="",
                notes=f"Google Chrome binary not found at '{self.executable_path}'.",
            )

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                executable_path=self.executable_path,
                headless=headless,
                args=["--start-maximized", "--no-first-run", "--no-default-browser-check"],
            )
            # Create browser context
            context = await browser.new_context(viewport=None)
            page = await context.new_page()

            try:
                # 1. Navigate to X explore or home
                await page.goto("https://x.com/explore", timeout=timeout_ms, wait_until="domcontentloaded")
                await page.wait_for_timeout(4000)

                url = page.url
                title = await page.title()

                # 2. Try locators for tweets / posts
                post_text = None
                author = None

                # Look for tweet text elements
                tweet_locators = [
                    page.locator('[data-testid="tweetText"]'),
                    page.locator('article div[lang]'),
                    page.locator('article'),
                ]

                for loc in tweet_locators:
                    count = await loc.count()
                    if count > 0:
                        first_el = loc.first
                        text = await first_el.inner_text()
                        if text and len(text.strip()) > 0:
                            post_text = text.strip()
                            break

                # Also look for author handle / username
                author_loc = page.locator('[data-testid="User-Name"]').first
                if await author_loc.count() > 0:
                    author = (await author_loc.inner_text()).replace("\n", " ")

                # If login dialog obscured the page
                notes = None
                if not post_text:
                    # Check if login prompt was triggered
                    login_header = page.locator('text="Sign in to X"')
                    if await login_header.count() > 0:
                        notes = "X displayed a login requirement dialog. Extracted landing page state."
                    else:
                        notes = "Page loaded, but no public tweets were immediately readable."

                # Keep browser open for 3 seconds so user can see it
                if not headless:
                    await page.wait_for_timeout(3000)

                return BrowserActionResult(
                    success=True if post_text else False,
                    url=url,
                    page_title=title,
                    first_post_text=post_text,
                    author=author,
                    notes=notes,
                )

            except Exception as e:
                return BrowserActionResult(
                    success=False,
                    url=page.url if page else "",
                    page_title="",
                    notes=f"Automation error: {str(e)}",
                )
            finally:
                await browser.close()
