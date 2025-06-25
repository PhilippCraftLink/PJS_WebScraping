import urllib.parse
import re
import json
from bs4 import BeautifulSoup
from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError

"""
This module provides functionality to scrape job listings from Indeed for specified job titles.
It utilizes SeleniumBase for browser automation to handle dynamic content and bypass CAPTCHA tests,
and BeautifulSoup for parsing HTML. The scraped data is stored in a MongoDB database for further analysis.

Key Features:
- Automates browser interactions to search for job titles on Indeed.
- Collects job links from multiple search result pages.
- Extracts detailed job information including location, benefits, and description.
- Stores the scraped data in a MongoDB collection, ensuring no duplicates.

Dependencies:
- seleniumbase: For browser automation and CAPTCHA handling.
- beautifulsoup4: For HTML parsing.
- pymongo: For MongoDB interactions.

Note:
- SeleniumBase is used to handle CAPTCHA tests automatically. For reliable operation, it is recommended to run SeleniumBase locally rather than in a Docker container, as virtual environments may complicate browser interactions.
- The script is designed to scrape a given number of pages and jobs. Adjust the `max_pages` and job link slicing as needed for full-scale scraping or testing purposes.
"""

# Identifiers for HTML elements containing job details
TEXT_IDS = ["jobLocationText"]  # List of IDs for text elements like job location
LIST_ID = "benefits"  # ID for the benefits list section
DESCRIPTION_ID = "jobDescriptionText"  # ID for the job description section

def scrape_indeed_for_title(job_title, sb, db):
    """
    Scrape job listings from Indeed for the given job title and store them in MongoDB.

    This function performs the following steps:
    1. Sets up a MongoDB collection for the specified job title with a unique index on jobID.
    2. Constructs the Indeed search URL for the job title and opens the search page using SeleniumBase.
    3. Scrapes job links from the defined number of pages of search results.
    4. For each job link, extracts detailed job information including location, benefits, description, and additional data from embedded JSON.
    5. Stores the extracted job data in the MongoDB collection, skipping duplicates.

    Parameters:
    - job_title (str): The job title to search for.
    - sb (seleniumbase.SB): An instance of SeleniumBase for browser automation.
    - db (pymongo.database.Database): MongoDB database instance to store the scraped data.

    Note:
    - Currently limits scraping to 10 pages and processes 100 job links; adjust these limits for full scraping or testing purposes.
    - Uses static sleep delays (e.g., sb.sleep(5)) for page loading; consider explicit waits for production use.
    - JSON extraction relies on Indeed's current page structure and may break if the site changes.
    """

    # Section: Setup MongoDB Collection
    # Create a collection for the job title and ensure unique jobIDs to prevent duplicates
    collection_name = job_title.replace(" ", "_").lower()
    collection = db["indeed_" + collection_name]
    collection.create_index([("jobID", ASCENDING)], unique=True)

    # Section: Construct Search URL and Open Page
    # Build the Indeed search URL and initiate browser navigation
    encoded_job_title = urllib.parse.quote(job_title)
    url = f"https://de.indeed.com/jobs?q={encoded_job_title}"
    print(f"\n🔍 Suche nach: {job_title}")
    print(url)

    sb.activate_cdp_mode(url)  # Enable Chrome DevTools Protocol for enhanced control
    sb.open(url)
    sb.sleep(15)  # Wait for initial page load; static delay, replaceable with explicit waits

    # Section: Scrape Job Links from Multiple Pages
    # Collect unique job URLs across multiple search result pages
    job_links = []
    max_pages = 10
    for page in range(max_pages):
        print(f"Scraping Seite {page + 1} für {job_title}")
        raw_html = sb.get_page_source()
        soup = BeautifulSoup(raw_html, "html.parser")
        for link in soup.select("a[data-mobtk]"):  # Select job links with specific attribute
            job_url = "https://de.indeed.com" + link["href"]
            if job_url not in job_links:
                job_links.append(job_url)

        if page < max_pages - 1:
            try:
                next_button = sb.find_element('a[aria-label="Nächste Seite"]')
                sb.click(next_button)
                sb.sleep(5)  # Wait for next page; static delay
            except:
                print("Keine weiteren Seiten verfügbar")
                break

    print(f"🔎 {len(job_links)} Jobangebote gefunden für {job_title}")
    print(job_links)
    job_links = job_links[:100]
    if len(job_links) == 0:
        print("Keine Jobangebote gefunden. Programm wird beendet.")
        return

    # Section: Extract and Store Job Details
    # Visit each job page, extract details, and save to MongoDB
    for idx, job_url in enumerate(job_links, start=1):
        try:
            sb.open(job_url)
            sb.sleep(5)  # Wait for job page load
            raw_html = sb.get_page_source()
            soup = BeautifulSoup(raw_html, "html.parser")

            job_data = {
                "Job Title": job_title,
                "URL": job_url,
            }

            # Extract text from specified IDs (e.g., location)
            for text_id in TEXT_IDS:
                element = soup.find(id=text_id)
                job_data[text_id] = element.get_text(separator=" ", strip=True) if element else "Nicht gefunden"

            # Extract benefits list
            benefits_div = soup.find(id=LIST_ID)
            if benefits_div:
                benefits = [li.get_text(strip=True) for li in benefits_div.find_all("li")]
                job_data[LIST_ID] = benefits if benefits else "Keine Vorteile angegeben"
            else:
                job_data[LIST_ID] = "Nicht gefunden"

            # Extract job description from paragraphs and list items
            description_div = soup.find(id=DESCRIPTION_ID)
            if description_div:
                elements = description_div.find_all(["p", "li"])
                paragraphs = [elem.get_text(strip=True) for elem in elements if elem.get_text(strip=True)]
                job_data["paragraphs"] = paragraphs
            else:
                job_data["paragraphs"] = "Nicht gefunden"

            # Extract additional data from embedded JSON object
            scripts = soup.find_all("script")
            pattern = r"window\._initialData\s*=\s*(\{.*?\});"  # Matches initial data JSON in script tags
            for script in scripts:
                script_text = script.string
                if script_text:
                    cleaned_script = " ".join(script_text.split())
                    match = re.search(pattern, cleaned_script)
                    if match:
                        json_str = match.group(1)
                        try:
                            initial_data = json.loads(json_str)
                            host_query_result = initial_data.get("hostQueryExecutionResult", {})
                            job_dataElement = host_query_result.get("data", {}).get("jobData", {})
                            results = job_dataElement.get("results", [])
                            if results and len(results) > 0:
                                first_result = results[0]
                                jobElement = first_result.get("job", {})
                                key = jobElement.get("key")
                                CompanyName = jobElement.get("sourceEmployerName")
                                if key:
                                    job_data["jobID"] = key
                                if CompanyName:
                                    job_data["Company Name"] = CompanyName
                        except json.JSONDecodeError:
                            print("Fehler beim Parsen des JSON-Strings")
                            continue

            # Insert job data into MongoDB, handling duplicates
            try:
                collection.insert_one(job_data)
                print(f"✅ {job_title} - Job {idx} erfolgreich gespeichert")
            except DuplicateKeyError:
                print(f"⏩ Übersprungen: {job_url} existiert bereits")

        except Exception as e:
            print(f"⚠️ Fehler bei Job {job_url}: {str(e)}")  # Broad exception catch; refine in production
            continue
