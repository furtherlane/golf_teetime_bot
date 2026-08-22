#!/Users/steve/apps/foreup-autores/.venv/bin/python3
"""
ForeUp tee time booking bot — pure HTTP API (no browser/Selenium).
Logs in at 8:59pm, polls for tee times at 9pm, books the earliest slot.
CAPTCHA is not enforced server-side, so we skip it entirely and book instantly.
"""
import os
import subprocess
import threading
import time
import warnings
warnings.filterwarnings("ignore")

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from dotenv import dotenv_values

# ---------------------------------------------------------------------------
# ForeUp constants (from HAR analysis 2026-08-02)
# ---------------------------------------------------------------------------
COURSE_ID        = "22528"
SCHEDULE_ID      = "11078"   # Francis A. Byrne Golf Course
SCHEDULE_IDS     = ["11078", "11075", "13190", "11077"]  # all Essex County teesheets
BOOKING_CLASS_ID = "49773"   # Gold Cardholders
BASE_URL         = "https://foreupsoftware.com"
BOOKING_PAGE     = f"{BASE_URL}/index.php/booking/{COURSE_ID}/{SCHEDULE_ID}"

NUM_PARALLEL = 3   # number of simultaneous booking attempts

LOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "teetime.lock")
config    = dotenv_values(".env")
DRY_RUN   = os.environ.get("DRY_RUN",   "").lower() == "true"
SKIP_WAIT = os.environ.get("SKIP_WAIT", "").lower() == "true"

# ---------------------------------------------------------------------------
# Timestamped print
# ---------------------------------------------------------------------------
_builtin_print = print
_print_lock = threading.Lock()

def print(*args, **kwargs):
    eastern = ZoneInfo("America/New_York")
    ts = datetime.now(eastern).strftime("%H:%M:%S.%f")[:-3]
    with _print_lock:
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
def wait_until_eastern(hour, minute, second=0):
    eastern = ZoneInfo("America/New_York")
    now = datetime.now(eastern)
    target = now.replace(hour=hour, minute=minute, second=second, microsecond=0)
    if target <= now:
        print(f"Target time {target.strftime('%I:%M:%S %p %Z')} already passed, proceeding immediately.")
        return
    secs = (target - now).total_seconds()
    print(f"Waiting {secs:.1f}s until {target.strftime('%I:%M:%S %p %Z')}...")
    time.sleep(secs)
    print("Target time reached.")


# ---------------------------------------------------------------------------
# Lock file
# ---------------------------------------------------------------------------
def acquire_lock():
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE) as f:
                existing_pid = int(f.read().strip())
            os.kill(existing_pid, 0)
            print(f"Another instance is already running (PID {existing_pid}). Exiting.")
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
# Build a session carrying the JWT
# ---------------------------------------------------------------------------
def make_session(jwt_token=""):
    s = requests.Session()
    headers = {
        "User-Agent":           "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/149.0.0.0 Safari/537.36",
        "X-Requested-With":     "XMLHttpRequest",
        "X-Fu-Golfer-Location": "foreup",
        "Origin":               BASE_URL,
        "Referer":              BOOKING_PAGE,
    }
    if jwt_token:
        headers["X-Authorization"] = f"Bearer {jwt_token}"
    s.headers.update(headers)
    return s


# ---------------------------------------------------------------------------
# Main booking flow
# ---------------------------------------------------------------------------
def run():
    acquire_lock()
    ffmpeg_proc = start_screen_recording()

    try:
        # ----------------------------------------------------------------
        # 8:59 pm — login
        # ----------------------------------------------------------------
        if not SKIP_WAIT:
            wait_until_eastern(20, 59)

        print("Initialising session...")
        login_session = make_session()
        login_session.get(BOOKING_PAGE, timeout=15)

        print("Logging in via API...")
        resp = login_session.post(
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
        print(f"Logged in (person_id={user['person_id']}).")

        target_date = datetime.now(ZoneInfo("America/New_York")) + timedelta(days=14)
        target_date_str = target_date.strftime("%m-%d-%Y")

        poll_session = make_session(jwt_token)
        poll_params = [
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
            poll_params.append(("schedule_ids[]", sid))

        # ----------------------------------------------------------------
        # Pre-warm: fire one tee times request immediately after login to
        # establish the TCP/TLS connection. Response will be empty (pre-9pm)
        # but the socket stays open so the 9pm polls skip connection setup.
        # ----------------------------------------------------------------
        print("Pre-warming connection to tee times endpoint...")
        try:
            poll_session.get(
                f"{BASE_URL}/index.php/api/booking/times",
                params=poll_params,
                timeout=10,
            )
            print("Connection pre-warmed.")
        except Exception as e:
            print(f"Pre-warm failed (non-fatal): {e}")

        # ----------------------------------------------------------------
        # Start hammering the endpoint at 8:59:58 — 2 seconds before 9pm.
        # A request fired at 8:59:59.5 with a ~500ms round-trip arrives just
        # as slots are released. Book immediately on the first non-empty
        # response; don't wait for a "better" slot to appear.
        # ----------------------------------------------------------------
        if not SKIP_WAIT:
            wait_until_eastern(20, 59, second=58)

        print(f"Polling tee times for {target_date_str}...")
        tee_times = []
        poll_attempt = 0
        poll_deadline = time.time() + 60  # give up after 60s
        eastern = ZoneInfo("America/New_York")
        while time.time() < poll_deadline:
            poll_attempt += 1
            try:
                resp = poll_session.get(
                    f"{BASE_URL}/index.php/api/booking/times",
                    params=poll_params,
                    timeout=5,
                )
                data = resp.json() if resp.ok else []
            except Exception as e:
                print(f"Poll error (attempt {poll_attempt}): {e}")
                data = []

            if isinstance(data, list) and data:
                data.sort(key=lambda t: t["time"])
                morning = [t for t in data if int(t["time"].split()[1].split(":")[0]) < 10]
                now_et = datetime.now(eastern)
                after_9pm = SKIP_WAIT or (now_et.hour >= 21)
                print(f"Poll {poll_attempt}: {len(data)} tee times, {len(morning)} before 10am"
                      f"{' — booking immediately.' if after_9pm else ' — waiting for 9pm before booking.'}")
                if morning and after_9pm:
                    # Morning slots found after 9pm — book immediately.
                    tee_times = data
                    break
                elif after_9pm and not tee_times:
                    # Past 9pm, no morning slots — take what we have.
                    tee_times = data
                    break
                elif not after_9pm:
                    # Before 9pm — keep polling; don't book pre-released afternoon slots
                    # before morning slots have a chance to appear at exactly 9pm.
                    pass

        if not tee_times:
            raise RuntimeError(f"No tee times for {target_date_str} after 60s of polling.")

        candidates = tee_times[:NUM_PARALLEL]
        print(f"Candidates: {[t['time'] for t in candidates]}")

        if DRY_RUN:
            for t in candidates:
                print(f"DRY_RUN — would attempt: {t['time']} @ {t['course_name']} (${t['green_fee']})")
            return

        # ----------------------------------------------------------------
        # Book candidates — no CAPTCHA wait needed (not enforced server-side).
        # Submissions are serialized: if slot 1 succeeds, slot 2 skips.
        # If slot 1 is rejected, slot 2 fires immediately.
        # ----------------------------------------------------------------
        booking_winner = {}
        winner_lock = threading.Lock()
        submit_lock = threading.Lock()

        def attempt_booking(idx, tee_time):
            label = f"[slot {idx + 1}]"
            booking_body = {
                **tee_time,
                "holes":     "18",
                "players":   1,
                "captchaid": "0",  # not enforced server-side
            }
            with submit_lock:
                with winner_lock:
                    if booking_winner:
                        return
                print(f"{label} Submitting booking for {tee_time['time']}...")
                try:
                    thread_session = make_session(jwt_token)
                    resp = thread_session.post(
                        f"{BASE_URL}/index.php/api/booking/users/reservations",
                        json=booking_body,
                        timeout=15,
                    )
                    result = resp.json()
                except Exception as e:
                    print(f"{label} Request failed: {e}")
                    return

            if "teetime_id" in result:
                with winner_lock:
                    if not booking_winner:
                        booking_winner["result"] = result
                        booking_winner["tee_time"] = tee_time
                        print(f"{label} Booking succeeded!")
            else:
                print(f"{label} Rejected: {resp.text[:200]}")

        booking_threads = [
            threading.Thread(target=attempt_booking, args=(i, tt), daemon=True)
            for i, tt in enumerate(candidates)
        ]
        for t in booking_threads:
            t.start()
        for t in booking_threads:
            t.join(timeout=30)

        if booking_winner:
            result = booking_winner["result"]
            print(f"\nBooking completed successfully!")
            print(f"  Tee time:  {result.get('reservation_time')}")
            print(f"  Date/time: {result.get('time')}")
            print(f"  Course:    {result.get('course_name')}")
            print(f"  Players:   {result.get('player_count')}")
            print(f"  Details:   {result.get('details')}")
            time.sleep(10)
        else:
            print(f"\nAll {len(candidates)} booking attempts failed.")

    except Exception:
        print("\nError occurred.", flush=True)
        raise
    finally:
        stop_screen_recording(ffmpeg_proc)
        release_lock()


if __name__ == "__main__":
    run()
