from flask import Flask, request, jsonify, send_from_directory
import requests
import os
import time
import logging
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

# Set up logging for debugging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__, static_folder=".")

# Replace with your actual Google API key
GOOGLE_API_KEY = "AIzaSyB7N-TvYqntP8X0brWeXmHcDpOl_xN0jvg"

def get_addresses_in_bounding_box(north, east, south, west):
    """
    Use the Google Geocoding API in reverse mode on a grid (3x3) sampling the bounding box.
    Google does not offer a direct “list all addresses in bounding box” endpoint,
    so this function samples several points in the area and collects their formatted addresses.
    """
    addresses = []
    rows, cols = 3, 3  # Adjust grid density as desired
    if rows > 1:
        lat_step = (north - south) / (rows - 1)
    else:
        lat_step = 0
    if cols > 1:
        lon_step = (east - west) / (cols - 1)
    else:
        lon_step = 0

    for i in range(rows):
        for j in range(cols):
            lat = south + i * lat_step
            lon = west + j * lon_step
            reverse_geocode_url = (
                f"https://maps.googleapis.com/maps/api/geocode/json"
                f"?latlng={lat},{lon}&key={GOOGLE_API_KEY}"
            )
            logging.debug("Reverse geocoding URL: %s", reverse_geocode_url)
            try:
                response = requests.get(reverse_geocode_url)
                response.raise_for_status()
                data = response.json()
                logging.debug("Reverse geocoding data for (%s,%s): %s", lat, lon, data)
            except Exception as e:
                logging.error("Error reverse geocoding (%s,%s): %s", lat, lon, e)
                continue

            if data.get("status") == "OK" and data.get("results"):
                # Pick the first result’s formatted address
                formatted_address = data["results"][0]["formatted_address"]
                if formatted_address not in addresses:
                    addresses.append(formatted_address)
                    logging.debug("Added address: %s", formatted_address)
            else:
                logging.warning("Reverse geocoding failed for (%s, %s): %s", lat, lon, data.get("status"))
    logging.debug("Total unique addresses found: %d", len(addresses))
    return addresses

def take_screenshots_of_addresses(addresses):
    """
    For each address, use the Google Geocoding API (forward geocoding) to get coordinates,
    then build a Google Static Maps URL with satellite imagery and use Selenium in headless mode to capture a screenshot.
    """
    screenshot_results = []
    logging.debug("Setting up Selenium for headless Chrome")

    # Configure Chrome options for headless mode
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--window-size=800,600")

    # Initialize the Chrome WebDriver using ChromeDriverManager
    service = ChromeService(executable_path=ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)

    screenshot_dir = "screenshots"
    if not os.path.exists(screenshot_dir):
        os.makedirs(screenshot_dir)
        logging.debug("Created screenshots directory at %s", screenshot_dir)

    for address in addresses:
        logging.debug("Processing address: %s", address)
        # Forward geocoding to get lat/lon for the address
        geocode_url = (
            f"https://maps.googleapis.com/maps/api/geocode/json"
            f"?address={requests.utils.quote(address)}&key={GOOGLE_API_KEY}"
        )
        try:
            geocode_response = requests.get(geocode_url)
            geocode_response.raise_for_status()
            geocode_data = geocode_response.json()
            logging.debug("Geocode data for %s: %s", address, geocode_data)
        except Exception as geocode_error:
            logging.error("Error geocoding %s: %s", address, geocode_error)
            screenshot_results.append({
                "address": address,
                "screenshot": None,
                "error": "Geocoding failed"
            })
            continue

        if geocode_data.get("status") == "OK" and geocode_data.get("results"):
            location = geocode_data["results"][0]["geometry"]["location"]
            lat = location["lat"]
            lon = location["lng"]
            # Build the Static Map URL using satellite imagery
            static_map_url = (
                f"https://maps.googleapis.com/maps/api/staticmap?"
                f"center={lat},{lon}&zoom=18&size=800x600"
                f"&maptype=satellite"
                f"&markers=color:red%7C{lat},{lon}"
                f"&key={GOOGLE_API_KEY}"
            )
            logging.debug("Fetching static map for address %s at %s", address, static_map_url)
            try:
                driver.get(static_map_url)
                time.sleep(2)  # give the map a moment to load
                # Create a safe filename for the address screenshot
                safe_address = "".join([c if c.isalnum() else "_" for c in address])
                screenshot_path = os.path.join(screenshot_dir, f"{safe_address}.png")
                driver.save_screenshot(screenshot_path)
                logging.debug("Saved screenshot for %s at %s", address, screenshot_path)
                screenshot_results.append({
                    "address": address,
                    "screenshot": screenshot_path
                })
            except Exception as screenshot_error:
                logging.error("Error taking screenshot for %s: %s", address, screenshot_error)
                screenshot_results.append({
                    "address": address,
                    "screenshot": None,
                    "error": "Screenshot failed"
                })
        else:
            logging.warning("No geocoding result for address %s", address)
            screenshot_results.append({
                "address": address,
                "screenshot": None,
                "error": "Geocoding returned no results"
            })

    driver.quit()
    return screenshot_results

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")

@app.route("/screenshots/<filename>")
def get_screenshot(filename):
    """Serve screenshot files from the screenshots folder."""
    return send_from_directory("screenshots", filename)

@app.route("/api/addresses_in_area", methods=["POST"])
def addresses_in_area():
    data = request.get_json()
    logging.debug("Received data for addresses_in_area: %s", data)
    north = data.get("north")
    east = data.get("east")
    south = data.get("south")
    west = data.get("west")

    if not all([north, east, south, west]):
        error_msg = "Missing bounding box parameters."
        logging.error(error_msg)
        return jsonify({"error": error_msg}), 400

    try:
        addresses = get_addresses_in_bounding_box(north, east, south, west)
        logging.debug("Addresses returned: %s", addresses)
        return jsonify({"addresses": addresses})
    except Exception as error:
        logging.error("Error in /api/addresses_in_area: %s", error)
        return jsonify({"error": str(error)}), 500

@app.route("/api/screenshot_addresses", methods=["POST"])
def screenshot_addresses():
    data = request.get_json()
    addresses = data.get("addresses", [])
    logging.debug("Received addresses for screenshotting: %s", addresses)
    try:
        results = take_screenshots_of_addresses(addresses)
        return jsonify({"success": True, "results": results})
    except Exception as error:
        logging.error("Error in /api/screenshot_addresses: %s", error)
        return jsonify({"error": str(error)}), 500

if __name__ == "__main__":
    logging.debug("Starting Flask server on port 5000...")
    app.run(debug=True, port=5000)
