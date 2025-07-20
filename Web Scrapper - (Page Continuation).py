from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, CollectionInvalid, PyMongoError

# --- MongoDB Connection Details ---
MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = 'local'
COLLECTION_NAME = 'Squareyard Data'

# --- Scraper Control Parameters ---
RUN_DURATION_HOURS = 1  # Total time the scraper will run
RUN_DURATION_SECONDS = RUN_DURATION_HOURS * 3600
MAX_PAGES_TO_SCRAPE = 50

# --- START PAGE CONFIGURATION ---
START_PAGE_NUMBER = 4  # The page number to start scraping from
BASE_SEARCH_URL = "https://www.squareyards.com/resale/search?buildingType=1&propertyType=1&propertyTypeName=Apartment&possessionStatus=Ready%20To%20Move&cityId=5"


processed_detail_urls_global_run = set()


def convert_price_to_number(price_str):
    if not price_str:
        return 0
    price_str = price_str.replace('₹', '').replace(',', '').strip().upper()
    try:
        if 'CR' in price_str:
            value = float(price_str.replace('CR', '').strip())
            return int(value * 1_00_00_000)
        elif 'L' in price_str:
            value = float(price_str.replace('L', '').strip())
            return int(value * 1_00_000)
        elif 'K' in price_str:
            value = float(price_str.replace('K', '').strip())
            return int(value * 1_000)
        else:
            return int(float(price_str))
    except ValueError:
        return 0


def scrape_listing_cards_from_search_page(html_content):

    soup = BeautifulSoup(html_content, 'lxml')
    listings = soup.find_all('article', class_='listing-card')
    page_links_and_basic_info = []

    for listing in listings:
        try:
            listing_url_tag = listing.find('h2', class_='heading').find('a')
            listing_url = listing_url_tag[
                'href'].strip() if listing_url_tag and 'href' in listing_url_tag.attrs else "N/A"
            if listing_url and not listing_url.startswith('http'):
                listing_url = f"https://www.squareyards.com{listing_url}"

            name_tag = listing.find('span', class_='project-name')
            apartment_name = name_tag.text.strip() if name_tag else "N/A"

            page_links_and_basic_info.append({
                'Listing URL': listing_url,
                'Apartment Name (from search page)': apartment_name
            })
        except Exception as e:
            print(f"Error extracting basic info from listing card on search page: {e}")
            continue
    return page_links_and_basic_info


def parse_single_apartment_detail(html_content):

    soup = BeautifulSoup(html_content, 'lxml')
    apartment_data = {}

    try:
        project_name_tag = soup.find('div', class_='dProjectName')
        apartment_data['Apartment Name'] = project_name_tag.text.strip() if project_name_tag else "N/A"


        location_input = soup.find('input', id='hd_subLocalityName')
        apartment_data['Location'] = location_input['value'].strip() if location_input else "N/A"

        price_tag = soup.find('div', class_='dProjectPrice').find('strong')
        price_str = price_tag.text.strip() if price_tag else "N/A"
        apartment_data['Price'] = convert_price_to_number(price_str)

        main_photo_tag = soup.find('div', class_='dPslider').find('img')
        photo_url = main_photo_tag['src'].strip() if main_photo_tag and 'src' in main_photo_tag.attrs else "N/A"
        if photo_url and photo_url.startswith('//'):
            photo_url = 'https:' + photo_url
        apartment_data['Photo URL'] = photo_url

        canonical_link = soup.find('link', rel='canonical')
        apartment_data['Listing URL'] = canonical_link[
            'href'].strip() if canonical_link and 'href' in canonical_link.attrs else "N/A"

        amenities = []
        amenities_table_div = soup.find('div', class_='npAmenitiesTableBox')
        if amenities_table_div:
            amenities_table = amenities_table_div.find('table', class_='npAmenitiesTable')
            if amenities_table:
                for row in amenities_table.find_all('tr'):
                    amenity_span = row.find('span')
                    if amenity_span:
                        amenities.append(amenity_span.text.strip())
        apartment_data['Amenities'] = amenities if amenities else []

    except Exception as e:
        print(f"Error extracting details from parsed HTML: {e}")
        apartment_data = {
            'Apartment Name': "N/A", 'Location': "N/A",
            'Price': 0, 'Photo URL': "N/A", 'Listing URL': "N/A",
            'Amenities': []
        }

    return apartment_data


if __name__ == "__main__":
    mongo_client = None
    try:
        mongo_client = MongoClient(MONGO_URI)
        mongo_client.admin.command('ping')
        db = mongo_client[DB_NAME]
        collection = db[COLLECTION_NAME]
        print(f"Successfully connected to MongoDB: Database '{DB_NAME}', Collection '{COLLECTION_NAME}'")
    except ConnectionFailure as e:
        print(f"Could not connect to MongoDB at {MONGO_URI}: {e}")
        print("Please ensure MongoDB is running and accessible. Exiting.")
        exit()
    except PyMongoError as e:
        print(f"A PyMongo error occurred: {e}")
        print("Exiting script.")
        exit()

    driver = None
    try:
        chrome_options = Options()
        chrome_options.add_argument("--start-maximized")

        service = Service("C:/Users/musab/Downloads/chromedriver-win64/chromedriver-win64/chromedriver.exe")
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.set_page_load_timeout(30)  # Set a timeout for page loads

    except Exception as e:
        print(f"Error initializing Chrome WebDriver: {e}")
        print("Please ensure chromedriver.exe path is correct and Chrome browser is installed. Exiting.")
        if mongo_client:
            mongo_client.close()
        exit()

    start_time = time.time()
    current_page_number = 1
    total_listings_inserted = 0
    stop_scraping = False

    try:
        print(f"\n--- Phase 1: Navigating to Page {START_PAGE_NUMBER} ---")
        driver.get(BASE_SEARCH_URL)
        time.sleep(3)

        while current_page_number < START_PAGE_NUMBER:
            elapsed_time = time.time() - start_time
            if elapsed_time > RUN_DURATION_SECONDS:
                print(f"\nAuto-stopping (Phase 1): Time limit reached before reaching start page.")
                stop_scraping = True
                break

            print(f"Clicking to reach page {current_page_number + 1}...")
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)

            next_page_selector = f'li.applyPagination[data-page="{current_page_number + 1}"]'
            try:
                next_button = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, next_page_selector))
                )
                driver.execute_script("arguments[0].click();", next_button)
                current_page_number += 1
                time.sleep(4)
                print(f"Successfully navigated to page {current_page_number}.")
            except Exception as e:
                print(f"Could not click to reach page {current_page_number + 1}: {e}. Stopping initial navigation.")
                stop_scraping = True
                break

        if stop_scraping:
            raise KeyboardInterrupt
        print(
            f"\n--- Phase 1 Complete: Successfully reached Page {current_page_number}. Starting main scraping from here. ---")
        current_search_page_url = driver.current_url

        while not stop_scraping and current_page_number <= MAX_PAGES_TO_SCRAPE:
            elapsed_time = time.time() - start_time
            if elapsed_time > RUN_DURATION_SECONDS:
                print(f"\nAuto-stopping (Phase 2): Total run time of {RUN_DURATION_HOURS} hour(s) elapsed.")
                stop_scraping = True
                break

            print(
                f"\nScraping Page {current_page_number} (Elapsed: {int(elapsed_time // 60)}m {int(elapsed_time % 60)}s)...")

            last_height = driver.execute_script("return document.body.scrollHeight")
            for _ in range(5):
                driver.execute_script("window.scrollBy(0, 1000);")
                time.sleep(1.5)
                new_height = driver.execute_script("return document.body.scrollHeight")
                if new_height == last_height:
                    break
                last_height = new_height

            current_page_html_source = driver.page_source

            listings_info_on_current_page = scrape_listing_cards_from_search_page(current_page_html_source)
            print(
                f"Found {len(listings_info_on_current_page)} potential listings to process on page {current_page_number}.")

            if not listings_info_on_current_page:
                print(f"No listings found on page {current_page_number}. Ending scraping process.")
                stop_scraping = True
                break

            for i, basic_listing_info in enumerate(listings_info_on_current_page):
                elapsed_time = time.time() - start_time
                if elapsed_time > RUN_DURATION_SECONDS:
                    print(f"Time limit reached during detail page processing. Stopping.")
                    stop_scraping = True
                    break  # Break from inner loop

                detail_url = basic_listing_info['Listing URL']
                if detail_url == "N/A":
                    print(f"Skipping listing {i + 1} due to missing URL.")
                    continue

                if detail_url in processed_detail_urls_global_run:
                    # print(f"Skipping listing {i+1} ({detail_url}) as already processed in this run.") # Uncomment for verbose logging
                    continue

                try:
                    print(f"({i + 1}/{len(listings_info_on_current_page)}) Navigating to detail: {detail_url}")

                    driver.get(detail_url)
                    WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.CLASS_NAME, 'dProjectName'))
                    )
                    time.sleep(1)

                    full_apartment_data = parse_single_apartment_detail(driver.page_source)
                    full_apartment_data['Scrape Timestamp'] = int(time.time())

                    if full_apartment_data['Listing URL'] != "N/A":
                        if collection.find_one({'Listing URL': full_apartment_data['Listing URL']}) is None:
                            collection.insert_one(full_apartment_data)
                            total_listings_inserted += 1
                            print(
                                f"Inserted new: '{full_apartment_data.get('Apartment Name', 'N/A')}' (ID: {full_apartment_data['Listing URL']}). Total new: {total_listings_inserted}")
                        else:
                            print(f"Listing URL '{full_apartment_data}' already in DB. Skipping insertion.")
                            pass
                    else:
                        print(f"Skipping insertion for URL {detail_url} due to invalid Listing URL.")

                    processed_detail_urls_global_run.add(detail_url)

                except Exception as e:
                    print(f"Error scraping or saving detail for {detail_url}: {e}")
                finally:

                    try:
                        driver.get(current_search_page_url)
                        time.sleep(3)  # Give time to load back to search page
                    except Exception as back_e:
                        print(
                            f"Warning: Failed to navigate back to search page {current_search_page_url}: {back_e}. This might affect subsequent pagination and is critical for correct flow.")
                        stop_scraping = True
                        break

            if stop_scraping:
                break

            current_page_number += 1
            if current_page_number > MAX_PAGES_TO_SCRAPE:
                print(f"Reached MAX_PAGES_TO_SCRAPE ({MAX_PAGES_TO_SCRAPE}). Ending scraping.")
                break

            try:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(2)

                next_page_selector = f'li.applyPagination[data-page="{current_page_number}"]'
                next_button_elements = driver.find_elements(By.CSS_SELECTOR, next_page_selector)

                if next_button_elements:
                    next_button = next_button_elements[0]
                    driver.execute_script("arguments[0].click();", next_button)
                    print(f"Navigated to page {current_page_number} by clicking numbered button.")
                else:
                    next_arrow_selector = 'li.applyPagination span em.icon-arrow-right'
                    next_arrow_elements = driver.find_elements(By.CSS_SELECTOR, next_arrow_selector)

                    if next_arrow_elements:
                        next_arrow_button = next_arrow_elements[0]
                        driver.execute_script("arguments[0].click();", next_arrow_button)
                        print(f"Navigated to page {current_page_number} by clicking 'Next' arrow.")
                    else:
                        print(f"No further pagination button found for page {current_page_number}. Ending scraping.")
                        stop_scraping = True
                        break
                current_search_page_url = driver.current_url
                time.sleep(4)

            except Exception as e:
                print(f"An error occurred during pagination to page {current_page_number}: {e}. Ending scraping.")
                stop_scraping = True
                break

    except KeyboardInterrupt:
        print("\nScraping interrupted by user (Ctrl+C). Performing cleanup...")
    except Exception as e:
        print(f"\nAn unexpected critical error occurred during scraping: {e}. Performing cleanup...")
    finally:
        # --- Cleanup ---
        if driver:
            driver.quit()
            print("\nSelenium WebDriver closed.")
        if mongo_client:
            mongo_client.close()
            print("MongoDB connection closed.")

    print(
        f"\nScraping process finished. Total NEW unique listings inserted into MongoDB in this run: {total_listings_inserted}")