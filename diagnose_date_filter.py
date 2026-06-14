#!/Users/steve/apps/foreup-autores/.venv/bin/python3
"""Read-only diagnostic: does clicking the player-count filter reset the selected calendar date?

Logs in, selects the last available calendar day, prints the URL, clicks the
"1 player" filter, prints the URL again, then exits. Does NOT touch tee times
or the booking flow.
"""
import warnings
warnings.filterwarnings("ignore")

from dotenv import dotenv_values
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from rich import print

config = dotenv_values(".env")


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

        # wait for calendar and select the last available day
        print("Waiting for calendar to load...")
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".datepicker-switch")))
        calendar_day_list = driver.find_elements(By.CSS_SELECTOR, ".day:not(.disabled)")
        print(f"Number of days available to reserve: {len(calendar_day_list)}")

        last_available_day = calendar_day_list[-1]
        print(f"Selecting date: {last_available_day.text}")
        last_available_day.click()

        print(f"\nURL after selecting date: {driver.current_url}")
        print(f"Page title: {driver.title}")
        driver.save_screenshot("diag_after_date_select.png")

        # filter to 1 player
        WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.CSS_SELECTOR, '#nav > div > div:nth-child(3) > div > div > a:nth-child(1)')))
        btn = driver.find_element(By.CSS_SELECTOR, '#nav > div > div:nth-child(3) > div > div > a:nth-child(1)')
        print(f"\nPlayer filter link text: {btn.text!r}, href: {btn.get_attribute('href')!r}")
        driver.execute_script("arguments[0].click();", btn)

        print(f"\nURL after clicking player filter: {driver.current_url}")
        print(f"Page title: {driver.title}")
        driver.save_screenshot("diag_after_player_filter.png")

        # check what date the calendar now shows as "selected"
        try:
            selected_day = driver.find_element(By.CSS_SELECTOR, ".day.active")
            print(f"Calendar day marked active: {selected_day.text!r}")
        except Exception:
            print("No '.day.active' element found.")

        # wait for tee times to load
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "times")))
        WebDriverWait(driver, 20).until(
            lambda d: d.find_element(By.CSS_SELECTOR, '#times > div').text != "Loading Tee times..."
        )

        # if no times for this day, walk backwards through earlier available days
        # until we find one with results (read-only - just need *a* tee time to inspect)
        for i in range(2, 6):
            times_text = driver.find_element(By.ID, "times").text
            if "no tee times available" not in times_text.lower():
                break
            print(f"\nNo times for this day, trying calendar_day_list[-{i}]...")
            # re-query: clicking a day re-renders the calendar, invalidating old element refs
            fresh_day_list = driver.find_elements(By.CSS_SELECTOR, ".day:not(.disabled)")
            day = fresh_day_list[-i]
            print(f"Selecting date: {day.text}")
            day.click()
            WebDriverWait(driver, 20).until(
                lambda d: d.find_element(By.CSS_SELECTOR, '#times > div').text != "Loading Tee times..."
            )

        try:
            selected_day = driver.find_element(By.CSS_SELECTOR, ".day.active")
            print(f"\nCalendar day marked active: {selected_day.text!r}")
        except Exception:
            print("\nNo '.day.active' element found.")

        times_text = driver.find_element(By.ID, "times").text
        print(f"\n--- #times text (first 500 chars) ---")
        print(times_text[:500])

        if "no tee times available" in times_text.lower():
            print("\nNo available days found to inspect modal. Stopping here.")
        else:
            # open the booking modal (read-only: do NOT click 'Book Time')
            first_tee_time = driver.find_element(By.CSS_SELECTOR, '#times > div > div:nth-child(1)')
            first_tee_time.click()
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "book_time")))
            driver.save_screenshot("diag_book_time_modal.png")

            modal_text = driver.find_element(By.ID, "book_time").text
            print("\n--- #book_time modal text ---")
            print(modal_text)

            # close the modal without booking
            close_btn = driver.find_element(By.CSS_SELECTOR, "#book_time .close[data-dismiss='modal']")
            close_btn.click()
            print("\nClosed modal via close button (no booking action taken).")

    finally:
        print("\nDone. Closing browser.", flush=True)
        driver.quit()


if __name__ == "__main__":
    run()
