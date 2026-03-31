# LinkedIn Tech News Auto-Poster

Automated pipeline that fetches the latest TechCrunch headline, generates a professional LinkedIn post with AI, and publishes it to LinkedIn using a saved browser session.

## What This Project Does

1. Pulls the latest article from TechCrunch RSS (last 24 hours only)
2. Generates a LinkedIn-ready post using Gemini or Ollama
3. Publishes the post to LinkedIn through Playwright
4. Runs daily via GitHub Actions
5. Uses session-based authentication to reduce repeated 2FA prompts

## Architecture

- main.py: Single entry point for the entire workflow (fetch, generate, publish, logging)
- save_linkedin_session.py: One-time helper to create/update LinkedIn session state
- .github/workflows/daily-linkedin-post.yml: Daily scheduler and CI runner
- .env.example: Environment variable template

## Requirements

- Python 3.11+
- Playwright Chromium browser
- Gemini API key (optional if using Ollama-only)

Install dependencies:

```bash
pip install -r requirements.txt
playwright install chromium
```

## Local Setup

1. Copy .env.example to .env
2. Fill required values
3. Create a LinkedIn session file
4. Run main.py

### Environment Variables

Core:

- FEED_URL=https://techcrunch.com/feed/
- MODEL_PROVIDER=gemini
- GEMINI_API_KEY=your_key
- GEMINI_MODEL=gemini-2.5-flash-lite
- LINKEDIN_STATE_FILE=linkedin_state.json
- HEADLESS=true
- LOG_LEVEL=INFO

Resilience flags:

- ENABLE_OLLAMA_FALLBACK=true
- SKIP_ON_GEMINI_QUOTA=true
- SKIP_ON_GEMINI_MODEL_NOT_FOUND=true
- SKIP_ON_LINKEDIN_TIMEOUT=true

Fallback login (optional, recommended):

- LINKEDIN_EMAIL=you@example.com
- LINKEDIN_PASSWORD=your_password

Ollama fallback (optional):

- OLLAMA_MODEL=llama3.1
- OLLAMA_URL=http://localhost:11434/api/generate

## Session-Based LinkedIn Authentication

This project posts with a saved Playwright auth state file instead of logging in from scratch on each run.

Generate session state:

```bash
python save_linkedin_session.py
```

The script opens a real browser. Complete LinkedIn login and any 2FA, then press Enter in terminal to save session state.

If session expires later, rerun save_linkedin_session.py and update the GitHub secret.

## Run Locally

```bash
python main.py
```

## GitHub Actions Automation

Workflow file:

- .github/workflows/daily-linkedin-post.yml

Current behavior:

- Runs daily at 09:00 UTC
- Supports manual trigger with workflow_dispatch
- Installs dependencies and Playwright
- Decodes LinkedIn session from secret
- Executes main.py

### Required GitHub Secrets

- GEMINI_API_KEY
- LINKEDIN_SESSION_B64
- LINKEDIN_EMAIL
- LINKEDIN_PASSWORD

To create LINKEDIN_SESSION_B64 locally (PowerShell):

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("linkedin_state.json"))
```

Paste the output into the LINKEDIN_SESSION_B64 repository secret.

## Reliability and Failure Handling

Built-in skip-safe behavior:

- No news in 24h: skips run cleanly
- Gemini quota exhausted: optional skip instead of fail
- Gemini model mismatch: optional skip instead of fail
- LinkedIn timeout/UI drift: optional skip instead of fail

This keeps CI healthy while still logging actionable causes.

## Common Troubleshooting

### 1) Gemini 429 RESOURCE_EXHAUSTED

Cause: quota/rate limit reached.

Fixes:

- Wait for quota reset
- Ensure correct project/key pairing
- Keep Ollama fallback enabled

### 2) Gemini 404 model not found

Cause: model name not supported for your API/version/project.

Fixes:

- Use GEMINI_MODEL=gemini-2.5-flash-lite
- Keep model fallback logic enabled

### 3) LinkedIn start post timeout

Cause: expired session, verification checkpoint, or UI variant.

Fixes:

- Refresh session with save_linkedin_session.py
- Update LINKEDIN_SESSION_B64 secret
- Keep SKIP_ON_LINKEDIN_TIMEOUT=true for non-blocking CI

## Security Notes

- Do not commit .env
- Do not commit linkedin_state.json
- Keep all sensitive data in GitHub Secrets

## Portfolio Notes

This repository demonstrates:

- Practical workflow orchestration in Python
- API fallback and resilience design
- Browser automation with session persistence
- CI/CD automation with scheduled jobs
- Production-style logging and graceful degradation
