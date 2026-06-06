# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

Automates booking golf tee times on ForeUp Software booking websites using Selenium. The script logs in, navigates to the last available date on the calendar, selects 3 players, and prints the first available tee time. The actual booking click is commented out pending user confirmation.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env_example .env
# Edit .env with your FOREUP_SOFTWARE_URL, FOREUP_USERNAME, FOREUP_PASSWORD
```

## Running

```bash
python selenium_reserve_tee_time.py
```

Requires Firefox installed (or swap to Chrome by editing line 20). Uses WebDriverWait throughout to handle page load timing.

## Architecture

Single-file script (`selenium_reserve_tee_time.py`) with one `run()` function. Flow:

1. Load credentials from `.env` via `dotenv_values`
2. Launch Firefox WebDriver, navigate to `FOREUP_SOFTWARE_URL`
3. Click the "Annual Member" booking class button (button[4] — hard-coded position)
4. Log in with credentials
5. Wait for calendar, find all non-disabled `.day` elements, click the last one
6. Select 3-player filter (hard-coded CSS selector)
7. Wait for `li.time-legacy` tee time elements, print details of the first one
8. **Booking line (`first_tee_time.click()`) is intentionally commented out** — uncomment only when ready to actually book

## Key Selectors

The XPath/CSS selectors are brittle and tied to ForeUp's current DOM structure. If the site updates, selectors on lines 26, 29, 36, 52, 62–63, 71, 76 may need updating.

## To Actually Book

Uncomment line 92 (`first_tee_time.click()`) and optionally uncomment line 96 (`driver.close()`).
