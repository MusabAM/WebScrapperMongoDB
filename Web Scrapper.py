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


def scrape_detail_page_amenities(driver, detail_url):
    driver.get(detail_url)
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, 'Amenities'))
        )
    except:
        print(f"Timed out waiting for amenities on {detail_url}. Skipping amenities for this listing.")
        return []

    soup = BeautifulSoup(driver.page_source, 'lxml')
    amenities_list = []
    amenities_table = soup.find('table', class_='npAmenitiesTable')
    if amenities_table:
        for row in amenities_table.find_all('tr'):
            amenity_span = row.find('span')
            if amenity_span:
                amenities_list.append(amenity_span.text.strip())

    return amenities_list


def scrape_listings_and_save_one_by_one_to_mongodb(html_content, driver, collection, start_time, run_duration_seconds,
                                                   scraped_count):
    visited_data = 0
    soup = BeautifulSoup(html_content, 'lxml')
    listings = soup.find_all('article', class_='listing-card')
    print(f"Found {len(listings)} listings on this search results page.")

    for listing in listings:
        elapsed_time = time.time() - start_time
        if elapsed_time > run_duration_seconds:
            print(f"Time limit of {run_duration_seconds / 60} minutes reached. Stopping individual listing processing.")
            return scraped_count, True

        try:
            name_tag = listing.find('span', class_='project-name')
            apartment_name = name_tag.text.strip() if name_tag else "N/A"

            location_tag = listing.find('div', class_='favorite-btn')
            location = location_tag.get('data-locality', 'N/A') if location_tag else "N/A"

            price_tag = listing.find('p', class_='listing-price').find('strong')
            price = price_tag.text.strip() if price_tag else "N/A"
            min_price = max_price = convert_price_to_number(price)

            photo_tag = listing.find('figure', class_='listing-img').find('img')
            photo_url = photo_tag['src'].strip() if photo_tag and 'src' in photo_tag.attrs else "N/A"

            listing_url_tag = listing.find('h2', class_='heading').find('a')
            listing_url = listing_url_tag[
                'href'].strip() if listing_url_tag and 'href' in listing_url_tag.attrs else "N/A"

            amenities = []
            if listing_url and listing_url != "N/A":
                print(f"Navigating to detail page: {listing_url}")
                amenities = scrape_detail_page_amenities(driver, listing_url)
                driver.back()
                time.sleep(2)

            apartment_data = {
                'Apartment Name': apartment_name,
                'Location': location,
                'Minimum Price': min_price,
                'Maximum Price': max_price,
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
                    'Minimum Price': min_price,
                    'Maximum Price': max_price
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
    MONGO_URI = "mongodb://localhost:27017/"
    DB_NAME = 'local'
    COLLECTION_NAME = 'Squareyard Data'

    # Scraping duration settings
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

    initial_search_url = "https://www.squareyards.com/resale/search?buildingType=1&propertyType=1&propertyTypeName=Apartment&possessionStatus=Ready%20To%20Move&cityId=5"
    driver.get(initial_search_url)
    time.sleep(3)

    start_time = time.time()
    page_number = 1
    max_pages = 50
    scraped_count = 0
    stop_scraping = False

    while not stop_scraping and page_number <= max_pages or page_number < 4:
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

        scraped_count, stop_flag_from_function, visited_data = scrape_listings_and_save_one_by_one_to_mongodb(
            current_page_html, driver, collection, start_time, RUN_DURATION_SECONDS, scraped_count
        )

        VISITED_DATA += visited_data

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