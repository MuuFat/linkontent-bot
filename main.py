"""End-to-end LinkedIn auto-post workflow in a single file.

Workflow:
1) Fetch latest tech headline from RSS (TechCrunch by default)
2) Generate a professional LinkedIn post via Gemini or Ollama
3) Publish to LinkedIn with Playwright using saved session cookies/state
4) Log success/failure details

Environment variables:
- FEED_URL (optional, default: https://techcrunch.com/feed/)
- MODEL_PROVIDER (optional, gemini|ollama, default: gemini)
- GEMINI_API_KEY (required if MODEL_PROVIDER=gemini)
- GEMINI_MODEL (optional, default: gemini-1.5-flash)
- OLLAMA_MODEL (optional, default: llama3.1)
- OLLAMA_URL (optional, default: http://localhost:11434/api/generate)
- LINKEDIN_STATE_FILE (optional, default: linkedin_state.json)
- LINKEDIN_EMAIL (optional, used for fallback login)
- LINKEDIN_PASSWORD (optional, used for fallback login)
- HEADLESS (optional, default: false)
- LOG_LEVEL (optional, default: INFO)
"""

from __future__ import annotations

import calendar
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import feedparser
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright


PLAYWRIGHT_TIMEOUT_MS = 60000
REALISTIC_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
GEMINI_DEFAULT_MODEL = "gemini-2.0-flash"
GEMINI_FALLBACK_MODELS = (GEMINI_DEFAULT_MODEL, "gemini-2.0-flash-lite")


def configure_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def optional_env(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


def entry_datetime_utc(entry: dict[str, Any]) -> datetime | None:
    time_struct = entry.get("published_parsed") or entry.get("updated_parsed")
    if not time_struct:
        return None
    return datetime.fromtimestamp(calendar.timegm(time_struct), tz=timezone.utc)


def clean_summary(summary_html: str) -> str:
    if not summary_html:
        return ""
    soup = BeautifulSoup(summary_html, "html.parser")
    return " ".join(soup.get_text(separator=" ", strip=True).split())


def fetch_latest_news(feed_url: str) -> dict[str, str] | None:
    logging.info("Fetching RSS feed: %s", feed_url)
    feed = feedparser.parse(feed_url)
    if getattr(feed, "bozo", False):
        logging.warning("Feed parser reported bozo flag; feed may be malformed.")

    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(hours=24)

    recent_items: list[tuple[datetime, dict[str, str]]] = []
    for entry in feed.entries:
        published_at = entry_datetime_utc(entry)
        if not published_at or published_at < cutoff:
            continue
        recent_items.append(
            (
                published_at,
                {
                    "title": (entry.get("title") or "").strip(),
                    "summary": clean_summary(entry.get("summary", "")),
                    "original_url": (entry.get("link") or "").strip(),
                },
            )
        )

    if not recent_items:
        logging.info("No new articles found, skipping.")
        return None

    recent_items.sort(key=lambda x: x[0], reverse=True)
    top_item = recent_items[0][1]
    logging.info("Selected headline: %s", top_item["title"])
    return top_item


def build_linkedin_prompt(title: str, summary: str) -> str:
    return f"""
You are a senior technology professional writing on LinkedIn.

Create one polished LinkedIn post using the following input.

News Title:
{title}

News Summary:
{summary}

Requirements:
1) First line must be a catchy hook headline.
2) Summarize the news in 2-4 concise sentences.
3) Add one professional insight sentence on impact for developers or tech teams.
4) Keep tone expert yet conversational.
5) End with 3 to 5 relevant hashtags.

Return only the final post text.
""".strip()


def generate_with_gemini(prompt: str) -> str:
    api_key = required_env("GEMINI_API_KEY")
    model_name = os.getenv("GEMINI_MODEL", GEMINI_DEFAULT_MODEL).strip() or GEMINI_DEFAULT_MODEL

    try:
        from google import genai
    except ImportError as exc:
        raise ImportError(
            "google-genai is not installed. Run: pip install google-genai"
        ) from exc

    client = genai.Client(api_key=api_key)
    candidate_models = [model_name, *GEMINI_FALLBACK_MODELS]
    last_error: Exception | None = None

    for candidate_model in candidate_models:
        try:
            response = client.models.generate_content(
                model=candidate_model,
                contents=prompt,
            )
            text = (getattr(response, "text", "") or "").strip()
            if text:
                if candidate_model != model_name:
                    logging.warning(
                        "Primary Gemini model '%s' unavailable; used fallback '%s'.",
                        model_name,
                        candidate_model,
                    )
                return text
        except Exception as exc:
            last_error = exc

    raise RuntimeError(
        "Gemini generation failed for all candidate models. "
        "Set GEMINI_MODEL to one available in your project/region."
    ) from last_error


def generate_with_ollama(prompt: str) -> str:
    url = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate").strip()
    model = os.getenv("OLLAMA_MODEL", "llama3.1").strip() or "llama3.1"

    payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode("utf-8")
    request = Request(url=url, data=payload, method="POST")
    request.add_header("Content-Type", "application/json")

    try:
        with urlopen(request, timeout=90) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        raise RuntimeError(f"Ollama HTTP error: {exc.code} {exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not connect to Ollama at {url}") from exc

    parsed = json.loads(body)
    text = (parsed.get("response") or "").strip()
    if not text:
        raise RuntimeError("Ollama returned an empty response.")
    return text


def generate_linkedin_post(title: str, summary: str) -> str:
    provider = os.getenv("MODEL_PROVIDER", "gemini").strip().lower()
    prompt = build_linkedin_prompt(title=title, summary=summary)

    logging.info("Generating LinkedIn post with provider: %s", provider)
    if provider == "gemini":
        return generate_with_gemini(prompt)
    if provider == "ollama":
        return generate_with_ollama(prompt)
    raise ValueError("MODEL_PROVIDER must be either 'gemini' or 'ollama'.")


def dismiss_cookie_popup(page: Page) -> None:
    selectors = [
        "button:has-text('Accept')",
        "button:has-text('Accept all')",
        "button:has-text('Allow cookies')",
        "button:has-text('Got it')",
        "#artdeco-global-alert-container button:has-text('Accept')",
    ]
    for selector in selectors:
        try:
            button = page.locator(selector).first
            if button.is_visible(timeout=1000):
                button.click(timeout=1000)
                return
        except Exception:
            continue


def login_with_credentials_if_possible(page: Page, email: str | None, password: str | None) -> bool:
    if not email or not password:
        return False

    logging.info("Session appears expired. Attempting credential-based fallback login.")
    page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=PLAYWRIGHT_TIMEOUT_MS)
    dismiss_cookie_popup(page)
    page.locator("#username").fill(email, timeout=PLAYWRIGHT_TIMEOUT_MS)
    page.locator("#password").fill(password, timeout=PLAYWRIGHT_TIMEOUT_MS)
    page.locator("button[type='submit']").click(timeout=PLAYWRIGHT_TIMEOUT_MS)
    page.wait_for_url("**/feed/**", timeout=PLAYWRIGHT_TIMEOUT_MS)
    return True


def create_context_with_optional_state(browser, state_file: str):
    state_path = Path(state_file)
    if state_path.exists():
        logging.info("Using existing LinkedIn session file: %s", state_file)
        return browser.new_context(
            storage_state=state_file,
            user_agent=REALISTIC_USER_AGENT,
        )
    logging.warning("LinkedIn session file not found: %s", state_file)
    return browser.new_context(user_agent=REALISTIC_USER_AGENT)


def publish_to_linkedin(
    post_text: str,
    news_url: str,
    state_file: str,
    headless: bool,
    email: str | None,
    password: str | None,
) -> None:

    content = f"{post_text.strip()}\n\n{news_url.strip()}"
    logging.info("Opening LinkedIn with saved session state.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, slow_mo=120 if not headless else 0)
        context = create_context_with_optional_state(browser, state_file)
        page = context.new_page()
        page.set_default_timeout(PLAYWRIGHT_TIMEOUT_MS)
        page.set_default_navigation_timeout(PLAYWRIGHT_TIMEOUT_MS)

        try:
            page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=PLAYWRIGHT_TIMEOUT_MS)
            dismiss_cookie_popup(page)

            if "/login" in page.url:
                logged_in = login_with_credentials_if_possible(page, email, password)
                if not logged_in:
                    raise RuntimeError(
                        "LinkedIn session is invalid and fallback credentials are missing. "
                        "Provide LINKEDIN_EMAIL and LINKEDIN_PASSWORD or refresh session state."
                    )

            # Persist refreshed auth state after successful fallback login.
            context.storage_state(path=state_file)

            start_post = page.locator("button:has-text('Start a post')").first
            start_post.wait_for(state="visible", timeout=PLAYWRIGHT_TIMEOUT_MS)
            start_post.click(timeout=PLAYWRIGHT_TIMEOUT_MS)

            editor = page.locator("div[role='textbox']").first
            editor.wait_for(state="visible", timeout=PLAYWRIGHT_TIMEOUT_MS)
            editor.click(timeout=PLAYWRIGHT_TIMEOUT_MS)
            editor.fill(content, timeout=PLAYWRIGHT_TIMEOUT_MS)

            post_button = page.locator("button:has-text('Post')").last
            post_button.wait_for(state="visible", timeout=PLAYWRIGHT_TIMEOUT_MS)
            post_button.click(timeout=PLAYWRIGHT_TIMEOUT_MS)

            time.sleep(3)
            logging.info("LinkedIn post submitted successfully.")

        except PlaywrightTimeoutError as exc:
            raise RuntimeError(
                "Timed out while posting to LinkedIn. UI may have changed or verification appeared."
            ) from exc
        finally:
            context.close()
            browser.close()


def main() -> int:
    load_dotenv()
    configure_logging()

    feed_url = os.getenv("FEED_URL", "https://techcrunch.com/feed/").strip()
    state_file = os.getenv("LINKEDIN_STATE_FILE", "linkedin_state.json").strip() or "linkedin_state.json"
    email = optional_env("LINKEDIN_EMAIL")
    password = optional_env("LINKEDIN_PASSWORD")
    headless = env_bool("HEADLESS", default=False)

    try:
        news = fetch_latest_news(feed_url)
        if not news:
            return 0

        generated_post = generate_linkedin_post(news["title"], news["summary"])
        logging.info("Generated post preview: %s", generated_post[:220].replace("\n", " "))

        publish_to_linkedin(
            post_text=generated_post,
            news_url=news["original_url"],
            state_file=state_file,
            headless=headless,
            email=email,
            password=password,
        )
        logging.info("Workflow completed successfully.")
        return 0

    except Exception as exc:
        logging.exception("Workflow failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
