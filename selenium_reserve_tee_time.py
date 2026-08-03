#!/Users/steve/apps/foreup-autores/.venv/bin/python3
"""
ForeUp tee time booking bot — pure HTTP API (no browser/Selenium).
Logs in at 8:59pm via REST API, polls for tee times at 9pm, books the earliest slot.
"""
import os
import re
import subprocess
import threading
import time
import warnings
warnings.filterwarnings("ignore")

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from dotenv import dotenv_values
from twocaptcha import TwoCaptcha

# ---------------------------------------------------------------------------
# ForeUp constants (from HAR analysis 2026-08-02)
# ---------------------------------------------------------------------------
COURSE_ID          = "22528"
SCHEDULE_ID        = "11078"   # Francis A. Byrne Golf Course
SCHEDULE_IDS       = ["11078", "11075", "13190", "11077"]  # all Essex County teesheets
BOOKING_CLASS_ID   = "49773"   # Gold Cardholders
RECAPTCHA_SITE_KEY = "6Le0bf4pAAAAALufPGSllYP0-QN79MW_XTUa-24h"
BASE_URL           = "https://foreupsoftware.com"
BOOKING_PAGE       = f"{BASE_URL}/index.php/booking/{COURSE_ID}/{SCHEDULE_ID}"

LOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "teetime.lock")
config    = dotenv_values(".env")
DRY_RUN   = os.environ.get("DRY_RUN",   "").lower() == "true"
SKIP_WAIT = os.environ.get("SKIP_WAIT", "").lower() == "true"

# ---------------------------------------------------------------------------
# Timestamped print
# ---------------------------------------------------------------------------
_builtin_print = print

def print(*args, **kwargs):
    eastern = ZoneInfo("America/New_York")
    ts = datetime.now(eastern).strftime("%H:%M:%S.%f")[:-3]
    _builtin_print(f"[{ts}]", *args, **kwargs)


# ---------------------------------------------------------------------------
# Screen recording
# ---------------------------------------------------------------------------
def start_screen_recording():
    eastern = ZoneInfo("America/New_York")
    ts = datetime.now(eastern).strftime("%Y-%m-%d_%H%M%S")
    os.makedirs("recordings", exist_ok=True)
    output = os.path.join("recordings", f"teetime_{ts}.mp4")
    ffmpeg_bin = "/usr/local/bin/ffmpeg"
    for screen_idx in ("1", "0"):
        try:
            proc = subprocess.Popen(
                [ffmpeg_bin, "-y", "-f", "avfoundation", "-capture_cursor", "1",
                 "-i", f"{screen_idx}:none", "-r", "15", "-c:v", "libx264",
                 "-preset", "ultrafast", "-pix_fmt", "yuv420p", output],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(1)
            if proc.poll() is None:
                print(f"Screen recording started: {output}")
                return proc
        except (FileNotFoundError, PermissionError):
            print(f"ffmpeg not available at {ffmpeg_bin} — skipping screen recording.")
            return None
    print("Screen recording failed to start.")
    return None


def stop_screen_recording(proc):
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        print("Screen recording saved.")


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------
def wait_until_eastern(hour, minute):
    eastern = ZoneInfo("America/New_York")
    now = datetime.now(eastern)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        print(f"Target time {target.strftime('%I:%M %p %Z')} already passed, proceeding immediately.")
        return
    secs = (target - now).total_seconds()
    print(f"Waiting {secs:.0f}s until {target.strftime('%I:%M:%S %p %Z')}...")
    time.sleep(secs)
    print("Target time reached.")


# ---------------------------------------------------------------------------
# Lock file (prevents double-booking from simultaneous invocations)
# ---------------------------------------------------------------------------
def acquire_lock():
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE) as f:
                existing_pid = int(f.read().strip())
            os.kill(existing_pid, 0)
            print(f"Another instance is already running (PID {existing_pid}). Exiting to avoid double-booking.")
            raise SystemExit(1)
        except (ValueError, ProcessLookupError, PermissionError):
            print("Stale lock file found — overwriting.")
    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))


def release_lock():
    try:
        os.remove(LOCK_FILE)
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Main booking flow
# ---------------------------------------------------------------------------
def run():
    acquire_lock()
    ffmpeg_proc = start_screen_recording()

    try:
        # Build a persistent session that automatically carries cookies and JWT.
        session = requests.Session()
        session.headers.update({
            "User-Agent":           "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                                    "Chrome/149.0.0.0 Safari/537.36",
            "X-Requested-With":     "XMLHttpRequest",
            "X-Fu-Golfer-Location": "foreup",
            "Origin":               BASE_URL,
            "Referer":              BOOKING_PAGE,
        })

        # ----------------------------------------------------------------
        # 8:59 pm — establish session + login
        # (No need to wait until 9pm to login; the server session persists
        #  across the 9pm SPA reset that plagued the old browser approach.)
        # ----------------------------------------------------------------
        if not SKIP_WAIT:
            wait_until_eastern(20, 59)

        print("Initialising session...")
        session.get(BOOKING_PAGE, timeout=15)

        print("Logging in via API...")
        resp = session.post(
            f"{BASE_URL}/index.php/api/booking/users/login",
            data={
                "username":         config["FOREUP_USERNAME"],
                "password":         config["FOREUP_PASSWORD"],
                "booking_class_id": "",
                "api_key":          "",
                "course_id":        COURSE_ID,
            },
            timeout=15,
        )
        resp.raise_for_status()
        user = resp.json()
        if not user.get("logged_in"):
            raise RuntimeError(f"Login failed: {resp.text[:300]}")

        jwt_token = user["jwt"]
        session.headers["X-Authorization"] = f"Bearer {jwt_token}"
        print(f"Logged in (person_id={user['person_id']}).")

        # ----------------------------------------------------------------
        # Start CAPTCHA pre-solve immediately after login so the token is
        # ready well before we need it at booking time (~60s later).
        # ----------------------------------------------------------------
        captcha_result = {}

        def solve_captcha():
            try:
                print("CAPTCHA pre-solve started (invisible reCAPTCHA)...")
                solver = TwoCaptcha(config["TWOCAPTCHA_API_KEY"])
                result = solver.recaptcha(
                    sitekey=RECAPTCHA_SITE_KEY,
                    url=BOOKING_PAGE,
                    invisible=1,
                )
                captcha_result["token"] = result["code"]
                print("CAPTCHA pre-solved and ready.")
            except Exception as e:
                captcha_result["error"] = str(e)
                print(f"CAPTCHA pre-solve failed: {e}")

        captcha_thread = threading.Thread(target=solve_captcha, daemon=True)
        captcha_thread.start()

        # ----------------------------------------------------------------
        # 9:00 pm — poll tee times API until target date's slots appear
        # ----------------------------------------------------------------
        if not SKIP_WAIT:
            wait_until_eastern(21, 0)

        eastern = ZoneInfo("America/New_York")
        target_date = datetime.now(eastern) + timedelta(days=14)
        target_date_str = target_date.strftime("%m-%d-%Y")
        print(f"Polling tee times for {target_date_str}...")

        tee_times = []
        for attempt in range(60):
            params = [
                ("time",          "all"),
                ("date",          target_date_str),
                ("holes",         "18"),
                ("players",       "1"),
                ("booking_class", BOOKING_CLASS_ID),
                ("schedule_id",   SCHEDULE_ID),
                ("specials_only", "0"),
                ("api_key",       ""),
            ]
            for sid in SCHEDULE_IDS:
                params.append(("schedule_ids[]", sid))

            try:
                resp = session.get(
                    f"{BASE_URL}/index.php/api/booking/times",
                    params=params,
                    timeout=10,
                )
                data = resp.json() if resp.ok else []
            except Exception as e:
                print(f"Tee times poll error (attempt {attempt + 1}): {e}")
                data = []

            if isinstance(data, list) and data:
                tee_times = data
                print(f"Got {len(tee_times)} tee times (attempt {attempt + 1}).")
                break

            time.sleep(1)

        if not tee_times:
            raise RuntimeError(f"No tee times for {target_date_str} after 60s of polling.")

        # Sort chronologically; prefer slots before 10am.
        tee_times.sort(key=lambda t: t["time"])
        early = [t for t in tee_times if int(t["time"].split()[1].split(":")[0]) < 10]
        candidates = early if early else tee_times

        print(f"Times before 10am: {[t['time'] for t in early] or 'none'}")

        # Try each candidate in order; fall back if a slot is taken.
        MAX_BOOKING_ATTEMPTS = 3

        for booking_attempt, tee_time in enumerate(candidates[:MAX_BOOKING_ATTEMPTS], 1):
            print(f"\n--- Booking attempt {booking_attempt}/{MAX_BOOKING_ATTEMPTS} ---")
            print(f"  Time:    {tee_time['time']}")
            print(f"  Course:  {tee_time['course_name']}")
            print(f"  Holes:   18")
            print(f"  Fee:     ${tee_time['green_fee']}")
            print(f"  Spots:   {tee_time['available_spots']}")

            if DRY_RUN:
                print("DRY_RUN set — stopping before booking.")
                return

            # POST pending_reservation to atomically hold the slot.
            print("Claiming slot (pending reservation)...")
            resp = session.post(
                f"{BASE_URL}/index.php/api/booking/pending_reservation",
                data={
                    "time":                       tee_time["time"],
                    "holes":                      "18",
                    "players":                    "1",
                    "carts":                      "false",
                    "schedule_id":                str(tee_time["schedule_id"]),
                    "teesheet_side_id":           str(tee_time["teesheet_side_id"]),
                    "course_id":                  str(tee_time["course_id"]),
                    "booking_class_id":           str(tee_time["booking_class_id"]),
                    "duration":                   "1",
                    "foreup_discount":            "false",
                    "foreup_trade_discount_rate": str(tee_time.get("foreup_trade_discount_rate", 0)),
                    "trade_min_players":          str(tee_time.get("trade_min_players", 0)),
                    "cart_fee":                   str(tee_time["cart_fee"]),
                    "cart_fee_tax":               str(tee_time["cart_fee_tax"]),
                    "green_fee":                  str(tee_time["green_fee"]),
                    "green_fee_tax":              str(tee_time["green_fee_tax"]),
                },
                timeout=15,
            )
            pending = resp.json()
            if not pending.get("success"):
                print(f"Slot unavailable (pending_reservation rejected): {resp.text[:200]}")
                print("Trying next available tee time...")
                continue
            reservation_id = pending["reservation_id"]
            print(f"Slot held (reservation_id={reservation_id}).")

            # Wait for CAPTCHA token (should already be ready from pre-solve).
            print("Waiting for CAPTCHA token...")
            captcha_thread.join(timeout=120)
            captchaid = captcha_result.get("token")
            if not captchaid:
                raise RuntimeError(f"CAPTCHA token unavailable: {captcha_result.get('error', 'timeout')}")

            # Build booking payload from the tee time object + our overrides.
            booking_body = {
                **tee_time,
                "holes":     "18",
                "players":   1,
                "captchaid": captchaid,
            }

            # Validate.
            print("Validating booking...")
            resp = session.post(
                f"{BASE_URL}/index.php/api/booking/users/reservations",
                json={**booking_body, "validate_only": True},
                timeout=15,
            )
            validation = resp.json()
            if not validation.get("valid"):
                print(f"Validation failed: {resp.text[:300]}")
                print("Trying next available tee time...")
                continue

            # Confirm.
            print("Confirming booking...")
            resp = session.post(
                f"{BASE_URL}/index.php/api/booking/users/reservations",
                json=booking_body,
                timeout=15,
            )
            result = resp.json()

            if "teetime_id" in result:
                print(f"\nBooking completed successfully!")
                print(f"  Tee time:  {result.get('reservation_time')}")
                print(f"  Date/time: {result.get('time')}")
                print(f"  Course:    {result.get('course_name')}")
                print(f"  Players:   {result.get('player_count')}")
                print(f"  Details:   {result.get('details')}")
                time.sleep(10)
                return

            print(f"Booking rejected: {resp.text[:300]}")
            print("Trying next available tee time...")

        print(f"\nGiving up after {MAX_BOOKING_ATTEMPTS} attempts.")

    except Exception:
        print("\nError occurred.", flush=True)
        raise
    finally:
        stop_screen_recording(ffmpeg_proc)
        release_lock()


if __name__ == "__main__":
    run()
