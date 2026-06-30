#!/Users/steve/apps/foreup-autores/.venv/bin/python3
import os
import re
import subprocess
import time
import warnings
warnings.filterwarnings("ignore")

from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import dotenv_values
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from twocaptcha import TwoCaptcha

from rich import print

config = dotenv_values(".env")


def get_chrome_major_version():
    """Detect the installed Chrome major version so uc downloads the matching ChromeDriver.
    uc 3.5.5 has a bug where it downloads the latest ChromeDriver regardless of Chrome version."""
    try:
        out = subprocess.check_output(
            ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "--version"],
            text=True, stderr=subprocess.DEVNULL
        )
        m = re.search(r"Chrome (\d+)", out)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return None


def wait_until_eastern(hour, minute):
    """Sleep until the given hour:minute today in America/New_York (handles EST/EDT automatically)."""
    eastern = ZoneInfo("America/New_York")
    now = datetime.now(eastern)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        print(f"Target time {target.strftime('%I:%M %p %Z')} already passed, proceeding immediately.")
        return
    wait_seconds = (target - now).total_seconds()
    print(f"Waiting {wait_seconds:.0f}s until {target.strftime('%I:%M:%S %p %Z')} before checking calendar...")
    time.sleep(wait_seconds)
    print("Target time reached.")


def run():

    # Pre-warm Chrome 1 minute before 9pm: load the page and sit on the booking
    # class selection screen WITHOUT logging in. ForeUp resets the session at
    # exactly 9pm when new tee times are released — any pre-9pm login gets wiped.
    # By clicking the booking class button at 9pm (not before), we login fresh after
    # the reset and land on the calendar with just-released tee times in ~9 seconds.
    if os.environ.get("SKIP_WAIT", "").lower() != "true":
        wait_until_eastern(20, 59)

    print("Launching Chrome...", flush=True)
    options = uc.ChromeOptions()
    options.add_argument("--window-size=1920,1080")
    chrome_major = get_chrome_major_version()
    print(f"Detected Chrome major version: {chrome_major}", flush=True)
    driver = uc.Chrome(options=options, headless=False, version_main=chrome_major)
    driver.set_page_load_timeout(60)
    print("Chrome launched.", flush=True)

    try:
        print(f"Loading {config['FOREUP_SOFTWARE_URL']}...", flush=True)
        driver.get(config["FOREUP_SOFTWARE_URL"])
        print("Page loaded. Waiting for booking class button...", flush=True)
        # Pre-warm: confirm the booking class button is ready, but don't click it yet.
        WebDriverWait(driver, 30).until(EC.element_to_be_clickable((By.XPATH, "/html/body/div[2]/div/div[2]/div/div/button[3]")))
        print("Page pre-warmed. Waiting until 9pm to click booking class...", flush=True)

        # Wait until exactly 9pm, then click and login immediately.
        if os.environ.get("SKIP_WAIT", "").lower() == "true":
            print("SKIP_WAIT is set, proceeding without waiting.", flush=True)
        else:
            wait_until_eastern(21, 0)

        # Click booking class and login at/after 9pm - no prior session to expire.
        # Re-find the button in case the page refreshed at 9pm.
        WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, "/html/body/div[2]/div/div[2]/div/div/button[3]")))
        booking_class_btn = driver.find_element(By.XPATH, "/html/body/div[2]/div/div[2]/div/div/button[3]")
        driver.execute_script("arguments[0].click();", booking_class_btn)
        print("Clicked booking class button.", flush=True)

        # log in - wait for the login modal, fill credentials, submit.
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.ID, "login_email")))
        driver.find_element(By.ID, "login_email").send_keys(config["FOREUP_USERNAME"])
        driver.find_element(By.ID, "login_password").send_keys(config["FOREUP_PASSWORD"])
        WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button.login")))
        login_btn = driver.find_element(By.CSS_SELECTOR, "button.login")
        driver.execute_script("arguments[0].click();", login_btn)
        print("Logged in.", flush=True)

        # wait for calendar and select the last available day
        # Note: for Essex County, tee times open 7 days in advance (14 days for Gold Members)
        # To test before 9PM EST, change calendar_day_list[-1] to calendar_day_list[-2]
        print("Waiting for calendar to load...")
        WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".datepicker-switch")))
        calendar_day_list = driver.find_elements(By.CSS_SELECTOR, ".day:not(.disabled)")
        print(f"Number of days available to reserve: {len(calendar_day_list)}")

        last_available_day = calendar_day_list[-1]
        expected_day = last_available_day.text.strip()
        print(f"Selecting date: {expected_day}")
        last_available_day.click()

        # filter to 1 player
        WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.CSS_SELECTOR, '#nav > div > div:nth-child(3) > div > div > a:nth-child(1)')))
        btn = driver.find_element(By.CSS_SELECTOR, '#nav > div > div:nth-child(3) > div > div > a:nth-child(1)')
        driver.execute_script("arguments[0].click();", btn)

        # wait for tee times to load
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "times")))
        WebDriverWait(driver, 20).until(
            lambda d: d.find_element(By.CSS_SELECTOR, '#times > div').text != "Loading Tee times..."
        )

        MAX_BOOKING_ATTEMPTS = 3

        # parse and display tee time info
        course_select = driver.find_element(By.XPATH, "/html/body/div[2]/div/div[2]/div[1]/div/div[1]/div/select")
        course = course_select.find_element(By.XPATH, "option[@selected]").text

        def print_tee_time_info(tee_time_element, label):
            parts = tee_time_element.text.split("\n")
            tee_time = parts[0] if len(parts) > 0 else "?"
            holes_players = parts[2].split() if len(parts) > 2 else []
            holes = holes_players[0] if len(holes_players) > 0 else "?"
            players = holes_players[1] if len(holes_players) > 1 else "?"
            print(f"\n{label}:")
            print(f"  Time:    {tee_time}")
            print(f"  Course:  {course}")
            print(f"  Holes:   {holes}")
            print(f"  Players: {players}")

        def close_booking_modal():
            close_buttons = driver.find_elements(By.CSS_SELECTOR, "#book_time .close[data-dismiss='modal']")
            if close_buttons:
                try:
                    close_buttons[0].click()
                except Exception:
                    pass  # modal may already be closing/closed
                try:
                    WebDriverWait(driver, 5).until(EC.invisibility_of_element_located((By.ID, "book_time")))
                except TimeoutException:
                    pass

        # try tee times for this date in order. If a booking attempt is rejected because
        # the slot was grabbed by someone else in the meantime, fall back to the next
        # available tee time for this date (up to MAX_BOOKING_ATTEMPTS).
        for booking_attempt in range(1, MAX_BOOKING_ATTEMPTS + 1):
            tee_time_selector = f'#times > div > div:nth-child({booking_attempt})'
            tee_time_elements = driver.find_elements(By.CSS_SELECTOR, tee_time_selector)
            if not tee_time_elements or "no tee times available" in tee_time_elements[0].text.lower():
                print("\nNo more tee times available for this date with the current filters.")
                driver.close()
                return
            tee_time_element = tee_time_elements[0]

            label = "First Available Tee Time" if booking_attempt == 1 else f"Next Available Tee Time (attempt {booking_attempt}/{MAX_BOOKING_ATTEMPTS})"
            print_tee_time_info(tee_time_element, label)

            # click the tee time to open the booking modal. Right around the 9pm release,
            # #times can refresh out from under us, so if the modal doesn't open, re-read
            # the tee time at this position and try again.
            for attempt in range(3):
                tee_time_element.click()
                try:
                    WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.ID, "book_time")))
                    break
                except TimeoutException:
                    print(f"\nBooking modal didn't open (attempt {attempt + 1}/3) - tee times list may have refreshed. Retrying...")
                    tee_time_elements = driver.find_elements(By.CSS_SELECTOR, tee_time_selector)
                    if not tee_time_elements or "no tee times available" in tee_time_elements[0].text.lower():
                        print("\nNo tee times available for this date with the current filters.")
                        driver.close()
                        return
                    tee_time_element = tee_time_elements[0]
                    print_tee_time_info(tee_time_element, label)
            else:
                raise TimeoutException("Booking modal did not open after 3 attempts")

            # verify the modal's date matches the calendar day we selected before booking anything.
            # If the selected day's tee times aren't released yet, #times can fall back to showing
            # a different date's availability while the calendar still shows our day as selected.
            modal_lines = driver.find_element(By.ID, "book_time").text.split("\n")
            try:
                modal_date_text = modal_lines[modal_lines.index("Date") + 1]
            except (ValueError, IndexError):
                modal_date_text = ""

            modal_day_match = re.search(r"(\d{1,2}),", modal_date_text)
            modal_day = modal_day_match.group(1) if modal_day_match else None

            if modal_day != expected_day:
                print(f"\nABORTING: selected calendar day '{expected_day}' but booking modal shows date '{modal_date_text}'.")
                print("The selected day's tee times may not be released yet. Not booking.")
                driver.save_screenshot("date_mismatch_screenshot.png")
                driver.close()
                return

            print(f"\nDate verified: modal shows '{modal_date_text}', matching selected calendar day '{expected_day}'.")

            if os.environ.get("DRY_RUN", "").lower() == "true":
                print("\nDRY_RUN is set - stopping before 'Book Time'. Would have booked the time/date above.")
                driver.close()
                return

            # select 1 player in the modal
            WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "#book_time > div > div.modal-body.container-fluid > div.row.js-booking-players-row > div.col-sm-6.col-md-4.js-booking-players > div > a.btn.btn-primary.active")))
            driver.find_element(By.CSS_SELECTOR, "#book_time > div > div.modal-body.container-fluid > div.row.js-booking-players-row > div.col-sm-6.col-md-4.js-booking-players > div > a.btn.btn-primary.active").click()

            # click Book Time
            WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "#book_time > div > div.modal-footer > button.btn.btn-success.js-book-button.pull-left")))
            driver.find_element(By.CSS_SELECTOR, "#book_time > div > div.modal-footer > button.btn.btn-success.js-book-button.pull-left").click()
            print("\nBook button clicked, checking for CAPTCHA...")

            # solve reCAPTCHA via 2captcha if it appears
            time.sleep(2)
            recaptcha_elements = driver.find_elements(By.CSS_SELECTOR, ".g-recaptcha, iframe[src*='recaptcha']")
            if recaptcha_elements:
                print("CAPTCHA detected, solving via 2captcha...")
                site_key = driver.find_element(By.CSS_SELECTOR, ".g-recaptcha").get_attribute("data-sitekey")
                solver = TwoCaptcha(config["TWOCAPTCHA_API_KEY"])
                result = solver.recaptcha(sitekey=site_key, url=driver.current_url, invisible=1)
                captcha_token = result["code"]
                print("CAPTCHA solved, injecting token...")

                driver.execute_script(f"""
                    document.querySelectorAll('[name="g-recaptcha-response"]').forEach(function(el) {{
                        el.value = "{captcha_token}";
                    }});
                """)
                time.sleep(1)
                book_btn = driver.find_element(By.CSS_SELECTOR, "#book_time > div > div.modal-footer > button.btn.btn-success.js-book-button.pull-left")
                driver.execute_script("arguments[0].click();", book_btn)
            else:
                print("No CAPTCHA detected.")

            # wait for confirmation - use visibility conditions to avoid matching
            # the always-present but hidden #login-error div (.alert-danger)
            try:
                WebDriverWait(driver, 20).until(
                    EC.any_of(
                        EC.invisibility_of_element_located((By.ID, "book_time")),
                        EC.visibility_of_element_located((By.CSS_SELECTOR, ".alert-success")),
                        EC.visibility_of_element_located((By.CSS_SELECTOR, ".alert-danger")),
                        EC.presence_of_element_located((By.CSS_SELECTOR, ".js-booking-confirmation")),
                        EC.url_contains("confirmation"),
                    )
                )
            except TimeoutException:
                print(f"\nTimed out waiting for confirmation. Check the booking system to verify.")
                time.sleep(60)
                driver.close()
                return

            # if the booking was rejected (e.g. someone else grabbed this slot first),
            # close the modal and fall back to the next available tee time for this date.
            # only count visible, non-empty danger alerts (ignore the hidden #login-error div)
            danger_elements = [el for el in driver.find_elements(By.CSS_SELECTOR, ".alert-danger")
                               if el.is_displayed() and el.text.strip()]
            if danger_elements:
                print(f"\nBooking attempt rejected: {danger_elements[0].text}")
                if booking_attempt < MAX_BOOKING_ATTEMPTS:
                    print("This tee time may have just been booked by someone else - trying the next available time...")
                    close_booking_modal()
                    continue
                print(f"\nGiving up after {MAX_BOOKING_ATTEMPTS} attempts.")
                time.sleep(60)
                driver.close()
                return

            for selector in [".alert-success", ".js-booking-confirmation"]:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                if elements:
                    print(f"\nBooking result: {elements[0].text}")
                    break
            else:
                print(f"\nBooking completed successfully!")

            time.sleep(60)
            driver.close()
            return

    except Exception:
        print("\nError occurred - saving screenshot and page source for debugging.", flush=True)
        driver.save_screenshot("error_screenshot.png")
        with open("error_page_source.html", "w") as f:
            f.write(driver.page_source)
        raise


if __name__ == "__main__":
    run()
