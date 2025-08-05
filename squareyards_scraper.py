import requests
from bs4 import BeautifulSoup
import csv
import os
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
    if 'PER SQ. FT' in price_str.upper():
        price_str = price_str.upper().replace('PER SQ. FT', '').strip()

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


def convert_area_to_sqft(area_str):
    if not area_str:
        return {"value": "N/A", "unit": "N/A"}

    parts = area_str.split()
    if len(parts) < 2:
        return {"value": "N/A", "unit": "N/A"}

    try:
        value = float(parts[0])
        unit = " ".join(parts[1:]).strip()
        if unit.upper() in ['ACRES']:
            return {"value": value * 43560, "unit": "Sq. Ft"}
        elif unit.upper() in ['SQ. FT.', 'SQ. FT', 'SQ.FT.']:
            return {"value": value, "unit": "Sq. Ft"}
        else:
            return {"value": area_str, "unit": "N/A"}
    except (ValueError, IndexError):
        return {"value": area_str, "unit": "N/A"}


def extract_lat_long_from_html(html_content):
    soup = BeautifulSoup(html_content, 'lxml')
    latitude = "N/A"
    longitude = "N/A"
    lat_input = soup.find('input', id='hd_plat')
    long_input = soup.find('input', id='hd_plang')
    if lat_input and 'value' in lat_input.attrs:
        latitude = float(lat_input['value'].strip())
    if long_input and 'value' in long_input.attrs:
        longitude = float(long_input['value'].strip())
    return {
        'latitude': latitude,
        'longitude': longitude
    }


def scrape_detail_page_info(detail_url):
    try:
        print(f"Fetching data from: {detail_url}")
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(detail_url, headers=headers, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'lxml')

        per_sqft_cost = "N/A"
        num_units = "N/A"
        total_area = "N/A"
        amenities = []
        latitude = "N/A"
        longitude = "N/A"

        per_sqft_input = soup.find('input', id='hd_perSqFt')
        if per_sqft_input and 'value' in per_sqft_input.attrs and per_sqft_input['value'].strip():
            try:
                per_sqft_cost = float(per_sqft_input['value'].strip())
            except ValueError:
                per_sqft_cost = "N/A"
        else:
            per_sqft_tag = soup.find('span', class_='per-sqft')
            if per_sqft_tag:
                try:
                    price_text = per_sqft_tag.text.strip().replace('₹', '').replace(',', '').replace('Per Sq. Ft',
                                                                                                     '').strip()
                    per_sqft_cost = float(price_text)
                except ValueError:
                    per_sqft_cost = "N/A"

        num_units_span = soup.find('span', string='Number of Units')
        if num_units_span:
            num_units_strong = num_units_span.find_next_sibling('strong')
            if num_units_strong:
                try:
                    num_units = int(num_units_strong.text.strip())
                except (ValueError, TypeError):
                    num_units = "N/A"

        total_area_span = soup.find('span', string='Total area')
        if total_area_span:
            total_area_strong = total_area_span.find_next_sibling('strong')
            if total_area_strong:
                total_area = total_area_strong.text.strip()

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
                                amenities.append(amenity_span.text.strip())
        else:
            amenities_list_box = soup.find('div', class_='amenities-list-box')
            if amenities_list_box:
                for li in amenities_list_box.find_all('li'):
                    span_tag = li.find('span')
                    if span_tag and 'More' not in span_tag.text:
                        amenities.append(span_tag.text.strip())

        lat_long_data = extract_lat_long_from_html(soup.prettify())
        latitude = lat_long_data['latitude']
        longitude = lat_long_data['longitude']

        return {
            'per_sqft_cost': per_sqft_cost,
            'num_units': num_units,
            'total_area': total_area,
            'amenities': amenities,
            'latitude': latitude,
            'longitude': longitude
        }

    except requests.exceptions.RequestException as e:
        print(f"Error fetching the page {detail_url}: {e}")
        return None
    except Exception as e:
        print(f"An error occurred during scraping {detail_url}: {e}")
        return None


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
            return True, visited_data, scraped_count

        try:
            name_link_tag = listing.find('h2', class_='npProjectName').find('a')
            apartment_name = name_link_tag.find('strong').text.strip() if name_link_tag and name_link_tag.find(
                'strong') else "N/A"
            location = name_link_tag.find('span',
                                          class_='npProjectCity').text.strip() if name_link_tag and name_link_tag.find(
                'span', class_='npProjectCity') else "N/A"
            listing_url = name_link_tag['href'].strip() if name_link_tag and 'href' in name_link_tag.attrs else "N/A"

            query_by_url = {'Listing URL': listing_url}
            existing_document_by_url = collection.find_one(query_by_url)

            if existing_document_by_url:
                visited_data += 1
                print(f"Skipping duplicate by name: {apartment_name}. Already exists in DB.")
                print("-" * 50)
                continue

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
            photo_url = photo_tag['data-src'].strip() if photo_tag and 'data-src' in photo_tag.attrs else "N/A"

            amenities = []
            latitude = "N/A"
            longitude = "N/A"
            per_sqft_cost = "N/A"
            num_units = "N/A"
            total_area = "N/A"

            if listing_url and listing_url != "N/A":
                if not listing_url.startswith('http'):
                    full_url = "https://www.squareyards.com" + listing_url
                else:
                    full_url = listing_url

                print(f"Scraping details for: {full_url}")

                detail_data = scrape_detail_page_info(full_url)

                if detail_data:
                    amenities = detail_data['amenities']
                    latitude = detail_data['latitude']
                    longitude = detail_data['longitude']
                    per_sqft_cost = detail_data['per_sqft_cost']
                    num_units = detail_data['num_units']
                    total_area = detail_data['total_area']

            apartment_data = {
                'Apartment Name': apartment_name,
                'Location': location,
                'Minimum Price': min_price_num,
                'Maximum Price': max_price_num,
                'Per Sqft Cost': per_sqft_cost,
                'Number of Units': num_units,
                'Total Area': total_area,
                'Photo URL': photo_url,
                'Listing URL': listing_url,
                'Amenities': amenities,
                'Latitude': latitude,
                'Longitude': longitude
            }

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

    return False, visited_data, scraped_count


if __name__ == "__main__":
    MONGO_URI = "mongodb+srv://<username>:<password>@cluster0.1zk9pu5.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0" # <-- Change Password and Username
    DB_NAME = 'ccube_research'
    COLLECTION_NAME = 'apartment'

    RUN_DURATION_HOURS = 3
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
    # chrome_options.add_argument("--headless")
    service = Service("C:/Users/musab/Downloads/chromedriver-win64/chromedriver-win64/chromedriver.exe")
    driver = webdriver.Chrome(service=service, options=chrome_options)

    initial_search_url = "https://www.squareyards.com/ready-to-move-projects-in-bangalore"
    driver.get(initial_search_url)
    time.sleep(3)

    start_time = time.time()
    page_number = 1
    max_pages = 200  # Set a reasonable limit to avoid infinite loops, adjust as needed
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

        stop_flag_from_function, visited_data_on_page, scraped_count = scrape_listings_and_save_one_by_one_to_mongodb(
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
