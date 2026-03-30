"""Create a reusable LinkedIn Playwright session state file.

Use this locally (headless=False) to complete login + 2FA once, then save
session cookies/local storage for CI reuse.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright


load_dotenv()


def save_session_state() -> None:
    output_path = os.getenv("LINKEDIN_STATE_FILE", "linkedin_state.json").strip()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=120)
        context = browser.new_context()
        page = context.new_page()

        page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
        print("Complete LinkedIn login and any 2FA in the opened browser.")
        input("After you can see your LinkedIn feed, press Enter here to save session... ")

        context.storage_state(path=output_path)
        print(f"Saved LinkedIn session state to: {output_path}")

        context.close()
        browser.close()


if __name__ == "__main__":
    save_session_state()
