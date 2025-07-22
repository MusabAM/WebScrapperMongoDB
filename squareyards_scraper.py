from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
from pymongo import MongoClient


def convert_price_to_number(price_str):
    if not price_str:
        return 0
    price_str = price_str.replace('₹', '').replace(',', '').strip().upper()
    try:
        if 'CR' in price_str:
            value = float(price_str.replace('CR', '').strip())
            return int(value * 1_00_00_000)
        elif 'LAC' in price_str:
            value = float(price_str.replace('LAC', '').strip())
            return int(value * 1_00_000)
        elif 'K' in price_str:
            value = float(price_str.replace('K', '').strip())
            return int(value * 1_000)
        elif 'PRICE ON REQUEST' in price_str:
            return -1
        else:
            return int(float(price_str))
    except ValueError:
        return 0


def scrape_detail_page_amenities(driver, detail_url):
    driver.get(detail_url)
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, 'amenities'))
        )
    except Exception:
        print(f"Amenities list not available on Page / Timed out waiting for main amenities section on {detail_url}. Skipping amenities.")
        return []

    soup = BeautifulSoup(driver.page_source, 'lxml')
    amenities_list = []

    more_amenities_button_present = False
    try:
        driver.find_element(By.ID, 'amenitiesModalBtn')
        more_amenities_button_present = True
    except:
        pass

    if more_amenities_button_present:
        print(f"Found 'More Amenities' button on {detail_url}. Attempting to click and scrape from modal.")
        try:
            more_amenities_button = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.ID, 'amenitiesModalBtn'))
            )
            driver.execute_script("arguments[0].click();", more_amenities_button)
            time.sleep(2)

            WebDriverWait(driver, 10).until(
                EC.visibility_of_element_located((By.ID, 'amenitiesModalBox'))
            )
            print(f"Amenities modal is visible for {detail_url}")

            # Re-parse the page source after the modal opens
            soup = BeautifulSoup(driver.page_source, 'lxml')
            amenities_modal_box = soup.find('div', id='amenitiesModalBox')
            if amenities_modal_box:
                accordion_items = amenities_modal_box.find_all('div', class_='accordion-item')
                for item in accordion_items:
                    amenities_table = item.find('table', class_='amenities-popup-table')
                    if amenities_table:
                        for row in amenities_table.find_all('tr'):
                            for td in row.find_all('td'):
                                amenity_span = td.find('span')
                                if amenity_span:
                                    amenities_list.append(amenity_span.text.strip())
            else:
                print(f"Amenities modal box (ID: amenitiesModalBox) not found after click simulation.")

            try:
                close_button = driver.find_element(By.CSS_SELECTOR, '#amenitiesModalBox .modal-close.button')
                driver.execute_script("arguments[0].click();", close_button)
                time.sleep(1)
                print(f"Closed amenities modal for {detail_url}")
            except Exception as close_e:
                print(f"Could not close amenities modal for {detail_url}. Error: {close_e}")

        except Exception as click_e:
            print(f"Failed to click 'More Amenities' button or modal did not appear for {detail_url}. Error: {click_e}")
            print("Attempting to scrape visible amenities directly from the page.")
            amenities_list = []
            amenities_list_box = soup.find('div', class_='amenities-list-box')
            if amenities_list_box:
                for li in amenities_list_box.find_all('li'):
                    span_tag = li.find('span')
                    if span_tag and 'More' not in span_tag.text:
                        amenities_list.append(span_tag.text.strip())
    else:
        print(f"No 'More Amenities' button found on {detail_url}. Scraping available amenities directly.")
        amenities_list_box = soup.find('div', class_='amenities-list-box')
        if amenities_list_box:
            for li in amenities_list_box.find_all('li'):
                span_tag = li.find('span')
                if span_tag and 'More' not in span_tag.text:
                    amenities_list.append(span_tag.text.strip())

    return amenities_list

def scrape_listings_and_save_one_by_one_to_mongodb(html_content, driver, collection, start_time, run_duration_seconds,
                                                   scraped_count):
    visited_data = 0
    soup = BeautifulSoup(html_content, 'lxml')
    listings = soup.find_all('div', class_='npTile')
    print(f"Found {len(listings)} listings on this search results page.")

    for listing in listings:
        elapsed_time = time.time() - start_time
        if elapsed_time > run_duration_seconds:
            print(f"Time limit of {run_duration_seconds / 60} minutes reached. Stopping individual listing processing.")
            return scraped_count, True, visited_data

        try:
            name_link_tag = listing.find('h2', class_='npProjectName').find('a')
            apartment_name = name_link_tag.find('strong').text.strip() if name_link_tag and name_link_tag.find('strong') else "N/A"
            location = name_link_tag.find('span', class_='npProjectCity').text.strip() if name_link_tag and name_link_tag.find('span', class_='npProjectCity') else "N/A"
            listing_url = name_link_tag['href'].strip() if name_link_tag and 'href' in name_link_tag.attrs else "N/A"

            price_box_tag = listing.find('div', class_='npPriceBox')
            price_text = price_box_tag.text.strip() if price_box_tag else "N/A"

            min_price_num = 0
            max_price_num = 0
            if ' - ' in price_text:
                price_parts = price_text.split(' - ')
                min_price_num = convert_price_to_number(price_parts[0])
                max_price_num = convert_price_to_number(price_parts[1])
            else:
                single_price = convert_price_to_number(price_text)
                min_price_num = single_price
                max_price_num = single_price

            photo_tag = listing.find('figure', class_='npTileFigure').find('img')
            photo_url = photo_tag['src'].strip() if photo_tag and 'src' in photo_tag.attrs else "N/A"

            amenities = []
            if listing_url and listing_url != "N/A":
                print(f"Navigating to detail page: {listing_url}")
                amenities = scrape_detail_page_amenities(driver, listing_url)
                driver.back()
                time.sleep(2)

            apartment_data = {
                'Apartment Name': apartment_name,
                'Location': location,
                'Minimum Price': min_price_num,
                'Maximum Price': max_price_num,
                'Photo URL': photo_url,
                'Listing URL': listing_url,
                'Amenities': amenities
            }

            query = {}
            if listing_url and listing_url != "N/A":
                query = {'Listing URL': listing_url}
            else:

                query = {
                    'Apartment Name': apartment_name,
                    'Location': location,
                    'Minimum Price': min_price_num,
                    'Maximum Price': max_price_num
                }

            existing_document = collection.find_one(query)

            if existing_document:
                visited_data += 1
                print(f"Skipping duplicate: {apartment_name} (URL: {listing_url}). Already exists.")
            else:
                value = collection.insert_one(apartment_data)
                print(f"Inserted: {apartment_name} with ID: {value.inserted_id}")
                scraped_count += 1
                visited_data += 1

            print(f"Visited Data in current Page: {visited_data}")
            print("-" * 50)


        except Exception as e:
            print(f"Skipping listing due to error: {e}")
            print("-" * 50)
            continue

    return scraped_count, False, visited_data

if __name__ == "__main__":
    MONGO_URI = "mongodb+srv://<username>:<password>@cluster0.xkcr7jt.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
    DB_NAME = 'Svastha'
    COLLECTION_NAME = 'Squareyard Data'


    RUN_DURATION_HOURS = 1
    RUN_DURATION_SECONDS = RUN_DURATION_HOURS * 3600

    VISITED_DATA = 0

    client = None
    try:
        client = MongoClient(MONGO_URI)
        db = client[DB_NAME]
        collection = db[COLLECTION_NAME]
        print(f"Connected to MongoDB: {MONGO_URI}, Database: {DB_NAME}, Collection: {COLLECTION_NAME}")

    except Exception as e:
        print(f"Error connecting to MongoDB: {e}")
        print("Exiting script as database connection failed.")
        exit()

    chrome_options = Options()
    chrome_options.add_argument("--start-maximized")

    service = Service("C:/Users/musab/Downloads/chromedriver-win64/chromedriver-win64/chromedriver.exe")
    driver = webdriver.Chrome(service=service, options=chrome_options)

    initial_search_url = "https://www.squareyards.com/ready-to-move-projects-in-bangalore"
    driver.get(initial_search_url)
    time.sleep(3)

    start_time = time.time()
    page_number = 1
    max_pages = 50 # Set a reasonable limit to avoid infinite loops, adjust as needed
    scraped_count = 0
    stop_scraping = False

    while not stop_scraping and page_number <= max_pages:
        elapsed_time = time.time() - start_time
        if elapsed_time > RUN_DURATION_SECONDS:
            print(f"\nTotal run time of {RUN_DURATION_HOURS} hour(s) elapsed. Stopping scraping.")
            stop_scraping = True
            break

        print(
            f"\nProcessing Search Results Page {page_number} (Elapsed: {int(elapsed_time // 60)}m {int(elapsed_time % 60)}s)...")

        last_height = driver.execute_script("return document.body.scrollHeight")
        for _ in range(5):
            driver.execute_script("window.scrollBy(0, 1000);")
            time.sleep(1.5)
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                break
            last_height = new_height

        current_page_html = driver.page_source

        print(f"Total Visited Data: {VISITED_DATA}")

        scraped_count, stop_flag_from_function, visited_data_on_page = scrape_listings_and_save_one_by_one_to_mongodb(
            current_page_html, driver, collection, start_time, RUN_DURATION_SECONDS, scraped_count
        )

        VISITED_DATA += visited_data_on_page

        if stop_flag_from_function:
            stop_scraping = True
            break

        page_number += 1
        if page_number <= max_pages:
            try:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(2)

                selector = f'li.applyPagination[data-page="{page_number}"]'
                next_button_elements = driver.find_elements(By.CSS_SELECTOR, selector)

                if next_button_elements:
                    next_button = next_button_elements[0]
                    driver.execute_script("arguments[0].click();", next_button)
                    print(f"Clicked pagination button for page {page_number} using JavaScript.")
                    time.sleep(4)
                else:
                    next_arrow_selector = 'li.applyPagination span em.icon-arrow-right'
                    next_arrow_elements = driver.find_elements(By.CSS_SELECTOR, next_arrow_selector)

                    if next_arrow_elements:
                        next_arrow_button = next_arrow_elements[0]
                        driver.execute_script("arguments[0].click();", next_arrow_button)
                        print(f"Clicked 'Next' arrow pagination button using JavaScript.")
                        time.sleep(4)
                    else:
                        print(
                            f"No pagination button found for page {page_number} (neither numbered nor arrow). Ending pagination attempts.")
                        stop_scraping = True
                        break
            except Exception as e:
                print(f"An error occurred during pagination attempt: {e}. Ending scraping process.")
                stop_scraping = True
                break
        else:
            print(f"Reached maximum page limit ({max_pages}). Ending scraping.")
            stop_scraping = True
            break

    driver.quit()
    if client:
        client.close()
        print("MongoDB connection closed.")

    print(f"\nScraping finished. Total listings inserted: {scraped_count}")
