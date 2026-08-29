#!/Users/steve/apps/foreup-autores/.venv/bin/python3
"""
ForeUp tee time booking bot — pure HTTP API (no browser/Selenium).
Logs in at 8:59pm, polls both Francis A. Byrne and Hendricks Field in parallel
at 9pm, and books the earliest available slot across both courses.
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
BOOKING_CLASS_ID = "49773"   # Gold Cardholders
BASE_URL         = "https://foreupsoftware.com"

# Courses to poll simultaneously. The tee time objects returned by each
# course's API already contain all fields needed for booking (schedule_id,
# teesheet_side_id, course_id, etc.), so no extra config is needed here.
COURSES = [
    {"name": "Francis A. Byrne", "schedule_id": "11078"},
    {"name": "Hendricks Field",  "schedule_id": "11075"},
]

BOOKING_PAGE = f"{BASE_URL}/index.php/booking/{COURSE_ID}/{COURSES[0]['schedule_id']}"
NUM_PARALLEL = 3   # top N slots (across both courses) to attempt booking

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


def poll_params_for(schedule_id, date_str):
    return [
        ("time",          "all"),
        ("date",          date_str),
        ("holes",         "18"),
        ("players",       "1"),
        ("booking_class", BOOKING_CLASS_ID),
        ("schedule_id",   schedule_id),
        ("specials_only", "0"),
        ("api_key",       ""),
        ("schedule_ids[]", schedule_id),
    ]


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

        # ----------------------------------------------------------------
        # Pre-warm: fire one request per course immediately after login to
        # establish TCP/TLS so the 9pm polls reuse warm connections.
        # ----------------------------------------------------------------
        print("Pre-warming connections...")
        for course in COURSES:
            s = make_session(jwt_token)
            try:
                s.get(f"{BASE_URL}/index.php/api/booking/times",
                      params=poll_params_for(course["schedule_id"], target_date_str),
                      timeout=10)
                print(f"  {course['name']} pre-warmed.")
            except Exception as e:
                print(f"  {course['name']} pre-warm failed (non-fatal): {e}")
            course["_warmup_session"] = s  # reuse for polling

        # ----------------------------------------------------------------
        # Poll both courses in parallel from 8:59:58, don't book until 9pm.
        # On first post-9pm poll, merge results from both courses, sort by
        # time, and immediately book the top NUM_PARALLEL earliest slots.
        # ----------------------------------------------------------------
        if not SKIP_WAIT:
            wait_until_eastern(20, 59, second=58)

        print(f"Polling both courses for {target_date_str}...")
        combined_tee_times = []
        poll_attempt = 0
        poll_deadline = time.time() + 60
        eastern = ZoneInfo("America/New_York")

        while time.time() < poll_deadline:
            poll_attempt += 1
            results = {}

            def fetch_course(course, res=results):
                sid = course["schedule_id"]
                try:
                    r = course["_warmup_session"].get(
                        f"{BASE_URL}/index.php/api/booking/times",
                        params=poll_params_for(sid, target_date_str),
                        timeout=5,
                    )
                    res[sid] = r.json() if r.ok else []
                except Exception as e:
                    print(f"Poll error [{course['name']}] attempt {poll_attempt}: {e}")
                    res[sid] = []

            threads = [threading.Thread(target=fetch_course, args=(c,), daemon=True)
                       for c in COURSES]
            for t in threads: t.start()
            for t in threads: t.join(timeout=6)

            merged = []
            for course in COURSES:
                data = results.get(course["schedule_id"], [])
                if isinstance(data, list):
                    merged.extend(data)

            if merged:
                merged.sort(key=lambda t: t["time"])
                morning = [t for t in merged if int(t["time"].split()[1].split(":")[0]) < 10]
                now_et = datetime.now(eastern)
                after_9pm = SKIP_WAIT or (now_et.hour >= 21)

                by_course = {}
                for t in merged:
                    by_course.setdefault(t["course_name"], 0)
                    by_course[t["course_name"]] += 1
                summary = ", ".join(f"{n}: {c}" for n, c in by_course.items())
                print(f"Poll {poll_attempt}: {len(merged)} total ({summary}), "
                      f"{len(morning)} before 10am"
                      f"{' — booking immediately.' if after_9pm else ' — waiting for 9pm.'}")

                if after_9pm:
                    combined_tee_times = merged
                    break

        if not combined_tee_times:
            raise RuntimeError(f"No tee times found after 60s of polling.")

        candidates = combined_tee_times[:NUM_PARALLEL]
        print(f"Candidates: {[(t['time'], t['course_name']) for t in candidates]}")

        if DRY_RUN:
            for t in candidates:
                print(f"DRY_RUN — would attempt: {t['time']} @ {t['course_name']} (${t['green_fee']})")
            return

        # ----------------------------------------------------------------
        # Book candidates — serialized to prevent double booking.
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
                "captchaid": "0",
            }
            with submit_lock:
                with winner_lock:
                    if booking_winner:
                        return
                print(f"{label} Submitting booking for {tee_time['time']} @ {tee_time['course_name']}...")
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
        for t in booking_threads: t.start()
        for t in booking_threads: t.join(timeout=30)

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
