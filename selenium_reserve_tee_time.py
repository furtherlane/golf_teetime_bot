#!/Users/steve/apps/foreup-autores/.venv/bin/python3
from dotenv import dotenv_values
from selenium import webdriver
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from rich import print

# load environment variables from .env.
# make sure you have copied the .env_example file to .env
# the project's base directory and updated the values as appropriate
config = dotenv_values(".env")


def run():

    # if you wish to use Chrome instead of Firefox, change this to the Chrome line
    driver = webdriver.Chrome()
    #driver = webdriver.Firefox()

    # open a browser window and go to the foreup website
    # no course selection is needed as Francis Byrne option is in the URL (11078)
    driver.get(config["FOREUP_SOFTWARE_URL"])

    # click on the Gold Member booking class button
    # driver.find_element(By.XPATH, "/html/body/div[2]/div/div[2]/div/div/button[4]").click()
    driver.find_element(By.XPATH, "/html/body/div[2]/div/div[2]/div/div/button[3]").click()

    # click on the login button
    #driver.find_element(By.XPATH, "/html/body/div[2]/div/div[2]/div/div/div/p[1]/button").click()
    driver.find_element(By.XPATH, "/html/body/div[3]/div/div/div[3]/div[1]/button[1]").click()


    # fill out the email input field
    driver.find_element(By.XPATH, '//*[@id="login_email"]').send_keys(config["FOREUP_USERNAME"])
    driver.find_element(By.XPATH, '//*[@id="login_password"]').send_keys(config["FOREUP_PASSWORD"])

    # click the form log in button
#    driver.find_element(By.XPATH, '/html/body/div[3]/div/div/div[3]/div/button[1]').click()
    driver.find_element(By.XPATH, "/html/body/div[3]/div/div/div[3]/div[1]/button[1]").click()

    '''
        you will now be logged into the website, and be presented with the booking date selection
        and time select page.
    '''

    # find the last non-disabled date on the calendar and click on it to show the tee times
    # for that day

    # wait for the page to load the calendar before continuing
    print("Waiting for the calendar datepicker-switch element to be clickable (page to load calendar)")
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, ".datepicker-switch"))
    )
    print("Calendar is there, find the calendar days which are not disabled")
    calendar_day_list = driver.find_elements(By.CSS_SELECTOR, ".day:not(.disabled)")

    print(f"Number of days available to reserve: {len(calendar_day_list)}")

    # the last non-disabled calendar day is the one we want to click on, so let's do it!
    last_available_day = calendar_day_list[-2]
    print(f"Last available day to reserve is: {last_available_day.text}, clicking it to bring up that date's tee times")
    last_available_day.click()

    # click on the # of players == 3 button to indicate we want 3
#    WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'div.hidden-xs:nth-child(2) > a:nth-child(2)')))
#   click on the # of players == first button to indicate booking for only 1 player
    WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.CSS_SELECTOR, '#nav > div > div:nth-child(3) > div > div > a:nth-child(1)')))

    # driver.find_element(By.CSS_SELECTOR, 'div.hidden-xs:nth-child(2) > a:nth-child(2)').click()
    driver.find_element(By.CSS_SELECTOR, '#nav > div > div:nth-child(3) > div > div > a:nth-child(1)').click()

    # wait for the available tee times to load
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.ID, "times"))
    )


#    WebDriverWait(driver, 10).until(
#        EC.presence_of_element_located((By.CSS_SELECTOR, 'li.time-legacy'))
#    )

    WebDriverWait(driver, 20).until(
        lambda d: d.find_element(By.CSS_SELECTOR, '#times > div').text != "Loading Tee times..."
    )


    # now find the first element in the tee times, it's the one we want to click to reserve the tee time!
    # it's the first LI element in the <ul> on the page
 #   first_tee_time = driver.find_element(By.CSS_SELECTOR, 'li.time-legacy:nth-child(1)')

# foreup changed their website and the tee time element is now a div grid instead of an li, so this is the new selector for the first tee time element
    first_tee_time = driver.find_element(By.CSS_SELECTOR, '#times > div > div:nth-child(1)')

#times > div > div:nth-child(1) > div > div > div > div.time-summary-left.time-summary-grid > div.time-summary-left-top > div.booking-start-time-label
    # get the text of the tee time and print it out to the console
    parts = first_tee_time.text.split("\n")
    time = parts[0] if len(parts) > 0 else "?"
    course = parts[1] if len(parts) > 1 else "?"
    holes_players = parts[2].split() if len(parts) > 2 else []
    holes = holes_players[0] if len(holes_players) > 0 else "?"
    players = holes_players[1] if len(holes_players) > 1 else "?"

    print(f"\nFirst Available Tee Time:")
    print(f"  Time:    {time}")
    print(f"  Course:  {course}")
    print(f"  Holes:   {holes}")
    print(f"  Players: {players}")


    # TODO:
    # when you are certain you want to continue, uncomment this line which will cick on the 1st available tee time
    # first_tee_time.click()

    # re-add this after you see the reservation working so it closes the browser window automatically
    # or leave it commented out and manually close the selenium web window
    # driver.close()


if __name__ == "__main__":
    run()