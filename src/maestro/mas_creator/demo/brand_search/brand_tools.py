import time
import os
import warnings
import selenium
from PIL import Image
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from google.cloud import bigquery
from google.genai import types
from google.adk.tools import ToolContext

warnings.filterwarnings("ignore", category=UserWarning)

# We use the mocked BigQuery client as the real project config is missing.
try:
    client = bigquery.Client()
except Exception as e:
    print(f"Error initializing BigQuery client: {e}")
    client = None

def get_product_details_for_brand(tool_context: ToolContext) -> str:
    """Retrieves product details from a BigQuery table."""
    brand = tool_context.user_content.parts[0].text
    print(f"DEBUG: Mocking BigQuery result for brand: {brand}")
    return f'''| Title | Description | Attributes | Brand |
|---|---|---|---|
| {brand} Travel Guide | Comprehensive guide for travelers | Genre: Travel, Pages: 300 | {brand} |
| {brand} Mystery Novel | A thrilling mystery story | Genre: Mystery, Hardcover | {brand} |
| {brand} History of Art | Detailed history of art movements | Genre: Art, Illustrated | {brand} |
'''

# Optional Selenium init
disable_webdriver = int(os.getenv("DISABLE_WEB_DRIVER", "0"))
driver = None
if not disable_webdriver:
    try:
        options = Options()
        options.add_argument("--window-size=1920x1080")
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        driver = selenium.webdriver.Chrome(options=options)
    except Exception as e:
        print(f"WebDriver initialization failed: {e}")

def go_to_url(url: str) -> str:
    """Navigates the browser to the given URL."""
    if not driver: return "Webdriver disabled"
    print(f"🌐 Navigating to URL: {url}")
    driver.get(url.strip())
    return f"Navigated to URL: {url}"

async def take_screenshot(tool_context: ToolContext) -> dict:
    """Takes a screenshot and saves it with the given filename. called 'load artifacts' after to load the image"""
    if not driver: return {"status": "error", "message": "Webdriver disabled."}
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    filename = f"screenshot_{timestamp}.png"
    driver.save_screenshot(filename)
    image = Image.open(filename)
    await tool_context.save_artifact(
        filename,
        types.Part.from_bytes(data=image.tobytes(), mime_type="image/png"),
    )
    return {"status": "ok", "filename": filename}

def find_element_with_text(text: str) -> str:
    """Finds an element on the page with the given text."""
    if not driver: return "Webdriver disabled"
    try:
        element = driver.find_element(By.XPATH, f"//*[text()='{text}']")
        if element: return "Element found."
        return "Element not found."
    except Exception:
        return "Element not found."

def click_element_with_text(text: str) -> str:
    """Clicks on an element on the page with the given text."""
    if not driver: return "Webdriver disabled"
    try:
        element = driver.find_element(By.XPATH, f"//*[text()='{text}']")
        element.click()
        return f"Clicked element with text: {text}"
    except Exception:
        return "Element not found, cannot click."

def enter_text_into_element(text_to_enter: str, element_id: str) -> str:
    """Enters text into an element with the given ID."""
    if not driver: return "Webdriver disabled"
    try:
        input_element = driver.find_element(By.ID, element_id)
        input_element.send_keys(text_to_enter)
        return f"Entered text '{text_to_enter}' into element with ID: {element_id}"
    except Exception:
        return "Element with given ID not found."

def scroll_down_screen() -> str:
    """Scrolls down the screen by a moderate amount."""
    if not driver: return "Webdriver disabled"
    driver.execute_script("window.scrollBy(0, 500)")
    return "Scrolled down the screen."

def get_page_source() -> str:
    """Returns the current page source."""
    if not driver: return "Webdriver disabled"
    LIMIT = 1000000
    return driver.page_source[0:LIMIT]

def analyze_webpage_and_determine_action(
    page_source: str, user_task: str, tool_context: ToolContext
) -> str:
    """Analyzes the webpage and determines the next action (scroll, click, etc.)."""
    return f"""
    You are an expert web page analyzer...
    [Prompt abstracted for length - use instructions contextually]
    """
