# 🏙️ Real Estate Web Scraper

## 📖 Overview

This project is a **real estate web scraper** that extracts **apartment listings from SquareYards.com** and saves the data into **MongoDB**.

It can:

- Scrape apartment names, prices, locations, amenities, images, and links.
- Handle pagination and page continuation.
- Avoid duplicates using MongoDB checks.
- Convert Indian real estate prices (e.g., ₹75L, ₹1.5CR) into numeric values.

---

## Project Structure

| File Name                          | Description                                           |
|-----------------------------------|-------------------------------------------------------|
| `Web Scrapper.py`                 | Main scraper (starts from page 1)                    |
| `Web Scrapper - (Page Continuation).py` | Continues scraping from page 4 onwards (or set your own start page) |

---

## Features

**Selenium & BeautifulSoup** integration  
**MongoDB storage** for real-time saving  
**Dynamic pagination** handling  
**Duplicate avoidance**  
**Robust error handling & time control**  
**Human-like scrolling to load content**  

---

## Setup Guide

### Prerequisites

- **Python 3.8+**
- **MongoDB** running locally (`localhost:27017`)
- **Chrome browser & ChromeDriver**

### Installation

Clone the repository:

```bash
git clone https://github.com/MusabAM/WebScrapperMongoDB
cd WebScrapperMongoDB
