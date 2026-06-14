#!/Users/steve/apps/foreup-autores/.venv/bin/python3
import os
import re
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
from twocaptcha import TwoCaptcha

from rich import print

config = dotenv_values(".env")


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

    print("Launching Chrome...", flush=True)
    options = uc.ChromeOptions()
    options.add_argument("--window-size=1920,1080")
    driver = uc.Chrome(options=options, headless=False)
    driver.set_page_load_timeout(60)
    print("Chrome launched.", flush=True)

    try:
        print(f"Loading {config['FOREUP_SOFTWARE_URL']}...", flush=True)
        driver.get(config["FOREUP_SOFTWARE_URL"])
        print("Page loaded.", flush=True)

        # click Gold Member booking class button
        WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, "/html/body/div[2]/div/div[2]/div/div/button[3]")))
        driver.find_element(By.XPATH, "/html/body/div[2]/div/div[2]/div/div/button[3]").click()
        print("Clicked booking class button.", flush=True)

        # log in
        WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, "/html/body/div[3]/div/div/div[3]/div[1]/button[1]")))
        driver.find_element(By.XPATH, "/html/body/div[3]/div/div/div[3]/div[1]/button[1]").click()
        driver.find_element(By.XPATH, '//*[@id="login_email"]').send_keys(config["FOREUP_USERNAME"])
        driver.find_element(By.XPATH, '//*[@id="login_password"]').send_keys(config["FOREUP_PASSWORD"])
        driver.find_element(By.XPATH, "/html/body/div[3]/div/div/div[3]/div[1]/button[1]").click()
        print("Logged in.", flush=True)

        # wait until exactly 9:00 PM Eastern before checking the calendar
        if os.environ.get("SKIP_WAIT", "").lower() == "true":
            print("SKIP_WAIT is set, proceeding without waiting.", flush=True)
        else:
            wait_until_eastern(21, 0)

        # wait for calendar and select the last available day
        # Note: for Essex County, tee times open 7 days in advance (14 days for Gold Members)
        # To test before 9PM EST, change calendar_day_list[-1] to calendar_day_list[-2]
        print("Waiting for calendar to load...")
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".datepicker-switch")))
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

        first_tee_time = driver.find_element(By.CSS_SELECTOR, '#times > div > div:nth-child(1)')

        if "no tee times available" in first_tee_time.text.lower():
            print("\nNo tee times available for this date with the current filters.")
            driver.close()
            return

        # parse and display tee time info
        course_select = driver.find_element(By.XPATH, "/html/body/div[2]/div/div[2]/div[1]/div/div[1]/div/select")
        course = course_select.find_element(By.XPATH, "option[@selected]").text
        parts = first_tee_time.text.split("\n")
        tee_time = parts[0] if len(parts) > 0 else "?"
        holes_players = parts[2].split() if len(parts) > 2 else []
        holes = holes_players[0] if len(holes_players) > 0 else "?"
        players = holes_players[1] if len(holes_players) > 1 else "?"

        print(f"\nFirst Available Tee Time:")
        print(f"  Time:    {tee_time}")
        print(f"  Course:  {course}")
        print(f"  Holes:   {holes}")
        print(f"  Players: {players}")

        # click the tee time to open the booking modal
        first_tee_time.click()

        # verify the modal's date matches the calendar day we selected before booking anything.
        # If the selected day's tee times aren't released yet, #times can fall back to showing
        # a different date's availability while the calendar still shows our day as selected.
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "book_time")))
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

        # wait for confirmation
        try:
            WebDriverWait(driver, 20).until(
                EC.any_of(
                    EC.invisibility_of_element_located((By.ID, "book_time")),
                    EC.presence_of_element_located((By.CSS_SELECTOR, ".alert-success")),
                    EC.presence_of_element_located((By.CSS_SELECTOR, ".alert-danger")),
                    EC.presence_of_element_located((By.CSS_SELECTOR, ".js-booking-confirmation")),
                    EC.url_contains("confirmation"),
                )
            )
            for selector in [".alert-success", ".alert-danger", ".js-booking-confirmation"]:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                if elements:
                    print(f"\nBooking result: {elements[0].text}")
                    break
            else:
                print(f"\nBooking completed successfully!")
        except:
            print(f"\nTimed out waiting for confirmation. Check the booking system to verify.")

        time.sleep(60)
        driver.close()

    except Exception:
        print("\nError occurred - saving screenshot and page source for debugging.", flush=True)
        driver.save_screenshot("error_screenshot.png")
        with open("error_page_source.html", "w") as f:
            f.write(driver.page_source)
        raise


if __name__ == "__main__":
    run()
