import requests
import json
import time
from bs4 import BeautifulSoup
import re
import urllib.parse
from datetime import datetime

# --- Configuration ---
# You MUST replace these placeholders with your actual values.

# Telegram Bot API Token and Chat ID
TELEGRAM_BOT_TOKEN = "8106314263:AAE5hF1a0DHcRJGS2DSqcuWTDpttakZUT4Q"  # Replace with your bot's token
TELEGRAM_CHAT_ID = "-1002805004101"       # Replace with your group's chat ID (e.g., -123456789)

# IVA SMS Portal Credentials
# IMPORTANT: For production, consider using environment variables or a secure
# configuration management system instead of hardcoding sensitive data.
YOUR_EMAIL = "mehidiha94@gmail.com"  # Replace with your actual IVA SMS email
YOUR_PASSWORD = "Xd62924826"      # Replace with your actual IVA SMS password

# --- Dynamic Target Number Selection Configuration ---
# Configure ONE of these options (set the other to None).

# OPTION 1: Select a number by its index in the dynamically loaded dropdown list.
# (0 for the first usable number, 1 for the second, etc.)
# This is useful if the order of your numbers is consistent and you always want a specific position.
# Set to None if you prefer to use OPTION 2 (specific number string).
CONFIGURED_TARGET_NUMBER_INDEX = 0  # <--- Set your desired index (e.g., 0, 1, 2...), or None

# OPTION 2: Select a specific phone number string from the dynamically loaded list.
# The script will search for this exact number. This is generally more reliable
# if the order of numbers can change, but the number itself remains constant.
# Set to None if you strictly want to use OPTION 1 (index-based selection).
CONFIGURED_SPECIFIC_NUMBER = "2250150830396" # <--- Set your desired specific number string, or None

# If both are None, the script will default to the very first available number found.
# If both are set, CONFIGURED_TARGET_NUMBER_INDEX will take precedence if it's a valid index.


# --- IVA SMS Portal URLs (Generally don't need to be changed) ---
LOGIN_URL = "https://www.ivasms.com/login"
SMS_RECEIVED_PAGE_URL = "https://www.ivasms.com/portal/sms/received"
SMS_DATA_ENDPOINT = "https://www.ivasms.com/portal/sms/received/getsms/number/sms"


# Global session object for persistent cookies across requests.
# This automatically handles session cookies received after login.
session = requests.Session()

# Global variables to store dynamically retrieved data
DYNAMIC_CSRF_TOKEN = "" # CSRF token for making POST requests
AVAILABLE_NUMBERS_LIST = [] # A list of dictionaries: [{"number": "...", "range": "..."}, ...]
SELECTED_NUMBER_TO_QUERY = None # The phone number chosen for the current query
SELECTED_RANGE_TO_QUERY = "" # The range associated with the chosen phone number


def get_csrf_token(html_content):
    """
    Extracts the CSRF token from the provided HTML content.
    Looks for a meta tag with 'csrf-token' name or a hidden input named '_token'.
    """
    soup = BeautifulSoup(html_content, 'html.parser')

    # Attempt to find CSRF token in a meta tag (common in Laravel applications)
    csrf_meta = soup.find('meta', {'name': 'csrf-token'})
    if csrf_meta and 'content' in csrf_meta.attrs:
        print("🔗 Found CSRF Token in meta tag.")
        return csrf_meta['content']

    # If not found in meta tag, attempt to find in a hidden input field
    csrf_input = soup.find('input', {'name': '_token'})
    if csrf_input and 'value' in csrf_input.attrs:
        print("🔗 Found CSRF Token in hidden input.")
        return csrf_input['value']

    print("⚠️ Warning: Could not find CSRF token in HTML content. This might indicate a change in website structure or missing data.")
    return None

def send_telegram_message(chat_id, message_text):
    """
    Sends a message to the specified Telegram chat ID.
    Supports HTML formatting and automatically splits messages longer than 4096 characters.
    """
    telegram_api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    MAX_MESSAGE_LENGTH = 4096 # Telegram's per-message character limit
    
    chunks = []
    current_chunk = ""
    
    # Prioritize splitting messages at logical separators (e.g., between SMS entries)
    # This makes multi-part messages more readable.
    if "➖➖➖➖➖➖➖➖➖➖" in message_text:
        raw_entries = message_text.split("➖➖➖➖➖➖➖➖➖➖")
        for i, entry in enumerate(raw_entries):
            entry_to_add = entry.strip()
            if i > 0: # Add the separator back for subsequent entries if they are not the first
                entry_to_add = "\n\n➖➖➖➖➖➖➖➖➖➖\n\n" + entry_to_add

            if len(current_chunk) + len(entry_to_add) > MAX_MESSAGE_LENGTH:
                if current_chunk: # Add the current accumulation as a chunk if not empty
                    chunks.append(current_chunk.strip())
                current_chunk = entry_to_add # Start a new chunk with this entry
            else:
                if current_chunk: # Append to current chunk
                    current_chunk += entry_to_add
                else: # Start first chunk
                    current_chunk = entry_to_add
        if current_chunk: # Add any remaining text as the last chunk
            chunks.append(current_chunk.strip())
    else: # Fallback to splitting by lines if no specific separator or message is short
        lines = message_text.split('\n')
        for line in lines:
            if len(current_chunk) + len(line) + 1 > MAX_MESSAGE_LENGTH: # +1 for newline character
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = line.strip()
            else:
                if current_chunk:
                    current_chunk += "\n" + line.strip()
                else:
                    current_chunk = line.strip()
        if current_chunk:
            chunks.append(current_chunk.strip())

    print(f"📤 Message size: {len(message_text)} chars. Sending in {len(chunks)} parts to Telegram.")
    for i, chunk in enumerate(chunks):
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "HTML", # Enable HTML formatting (<b>, <i>, <code>, etc.)
            "disable_web_page_preview": True # Prevent Telegram from creating unwanted link previews
        }
        try:
            response = requests.post(telegram_api_url, json=payload, timeout=10)
            response.raise_for_status() # Raise an HTTPError for bad responses (4xx or 5xx)
            time.sleep(0.5) # Pause briefly to respect Telegram API rate limits
        except requests.exceptions.RequestException as e:
            print(f"❌ Error sending message part {i+1} to Telegram: {e}")
            # Send a critical error to Telegram and stop sending remaining chunks
            send_telegram_message(chat_id, f"<b>⚠️ Critical Error:</b> Failed to send further messages to Telegram. Check bot token/chat ID or API limits. Details: <code>{e}</code>")
            break


def perform_login():
    """
    Handles the entire login process for the IVA SMS portal.
    1. Fetches the login page to acquire an initial CSRF token and session cookies.
    2. Submits login credentials along with the obtained token.
    3. Returns True if login appears successful (indicated by redirection to a portal/dashboard URL), False otherwise.
    """
    global DYNAMIC_CSRF_TOKEN # We'll need to set this after login

    print("🔑 Initiating login process...")
    send_telegram_message(TELEGRAM_CHAT_ID, "<i>Attempting to log in to IVA SMS portal...</i> 🔄")

    # Step 1: GET the login page to retrieve initial cookies and CSRF token for the login form
    print(f"🌐 Fetching login page: {LOGIN_URL}")
    try:
        response = session.get(LOGIN_URL, timeout=15)
        response.raise_for_status() # Check for HTTP errors (e.g., 404, 500)
        initial_login_csrf_token = get_csrf_token(response.text)
        if not initial_login_csrf_token:
            print("❌ Failed to get initial CSRF token from login page.")
            send_telegram_message(TELEGRAM_CHAT_ID, "<b>❌ Login Failed:</b> Could not retrieve initial CSRF token from login page. Website structure might have changed.")
            return False

        print(f"🍪 Initial cookies acquired during login page fetch: {session.cookies.get_dict()}")

    except requests.exceptions.RequestException as e:
        print(f"❌ Error fetching login page: {e}")
        send_telegram_message(TELEGRAM_CHAT_ID, f"<b>❌ Login Failed:</b> Network error accessing login page: <code>{e}</code>")
        return False

    # Step 2: Prepare and send POST data for login
    # IMPORTANT RECAPTCHA NOTE:
    # The 'g-recaptcha-response' field is a significant challenge for automation.
    # A valid reCAPTCHA token is typically obtained by a user solving a CAPTCHA in a browser,
    # or via integration with a specialized CAPTCHA-solving service.
    # The placeholder "MANUAL_OR_SOLVED_RECAPTCHA_TOKEN" will cause login to FAIL for real
    # reCAPTCHA-protected sites unless replaced with a live, valid token.
    login_data = {
        "_token": initial_login_csrf_token,
        "email": YOUR_EMAIL,
        "password": YOUR_PASSWORD,
        "remember": "on",
        "g-recaptcha-response": "MANUAL_OR_SOLVED_RECAPTCHA_TOKEN", # <--- CRITICAL: THIS MUST BE A VALID, SOLVED TOKEN!
        "submit": "register" # This parameter is often the 'name' attribute of the submit button
    }

    # Headers for the login POST request (mimicking a browser)
    login_headers = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "accept-language": "en-US,en;q=0.9",
        "cache-control": "max-age=0",
        "content-type": "application/x-www-form-urlencoded",
        "origin": "https://www.ivasms.com",
        "referer": LOGIN_URL, # Referer header should point to the page the request originated from
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
        "priority": "u=0, i" # Some sites use this for prioritization
    }

    print("➡️ Sending login credentials...")
    try:
        # Use allow_redirects=True to follow login redirects to the final portal page
        login_response = session.post(LOGIN_URL, headers=login_headers, data=login_data, allow_redirects=True, timeout=20)
        login_response.raise_for_status() # Raise HTTPError for bad responses (e.g., 401 Unauthorized)

        # Check the final URL after redirects to confirm successful login.
        # Successful logins usually redirect to a dashboard or portal page.
        if "/portal" in login_response.url or "/dashboard" in login_response.url:
            print(f"✅ Login successful! Final URL after redirection: {login_response.url}")
            send_telegram_message(TELEGRAM_CHAT_ID, "<b>✅ Login Successful!</b> Proceeding to fetch SMS data... 🚀")
            # The 'session' object now automatically holds the authenticated cookies for subsequent requests.
            return True
        else:
            print(f"❌ Login failed. Not redirected to expected portal URL. Final URL: {login_response.url}")
            print(f"Response content snippet: {login_response.text[:1000]}...")
            send_telegram_message(TELEGRAM_CHAT_ID, "<b>❌ Login Failed:</b> Incorrect credentials, reCAPTCHA not solved, or unexpected redirect. Please check manually.")
            return False

    except requests.exceptions.RequestException as e:
        print(f"❌ Error during login POST request: {e}")
        send_telegram_message(TELEGRAM_CHAT_ID, f"<b>❌ Login Failed:</b> Network error during POST request: <code>{e}</code>")
        return False

def get_dynamic_sms_params():
    """
    After successful login, this function fetches the SMS received page to:
    1. Extract the current CSRF token needed for AJAX SMS data queries.
    2. Populate the list of available phone numbers and their associated ranges
       from the HTML dropdowns on the page.
    3. Selects the target phone number and its range based on the script's configuration.
    """
    global DYNAMIC_CSRF_TOKEN, AVAILABLE_NUMBERS_LIST, SELECTED_NUMBER_TO_QUERY, SELECTED_RANGE_TO_QUERY

    print(f"✨ Fetching SMS received page to get dynamic parameters: {SMS_RECEIVED_PAGE_URL}")
    send_telegram_message(TELEGRAM_CHAT_ID, "<i>Retrieving dynamic SMS query parameters...</i> 🔍")

    try:
        response = session.get(SMS_RECEIVED_PAGE_URL, timeout=15)
        response.raise_for_status() # Check for HTTP errors

        soup = BeautifulSoup(response.text, 'html.parser')

        # 1. Extract CSRF Token for the SMS data request form
        current_csrf_token = get_csrf_token(response.text)
        if current_csrf_token:
            DYNAMIC_CSRF_TOKEN = current_csrf_token
            print(f"🔑 Updated CSRF Token for SMS data request: {DYNAMIC_CSRF_TOKEN}")
        else:
            print("⚠️ Warning: Could not get a new CSRF token from SMS received page. This might indicate an issue.")
            send_telegram_message(TELEGRAM_CHAT_ID, "<b>⚠️ Warning:</b> Could not find dynamic token for SMS query. Might fail.")
        
        # 2. Extract available numbers and their associated ranges from dropdowns
        # You MUST inspect your ivasms.com portal's HTML for the <select> element
        # that corresponds to the phone number selection. Look for its 'name' or 'id'.
        
        # Common name for number dropdown is 'Number'. Adjust if different.
        number_select = soup.find('select', {'name': 'Number'}) 
        if number_select:
            print("🔢 Found 'Number' selection element. Parsing options...")
            # Clear previous list for a fresh run
            AVAILABLE_NUMBERS_LIST.clear() 
            for option in number_select.find_all('option'):
                number_value = option.get('value')
                number_text = option.get_text(strip=True)
                
                # Filter out empty or placeholder options like "Select a Number"
                if number_value and number_text and number_value != '':
                    # The 'Range' value is typically embedded in the option's text like "Number (Range)"
                    # Adjust this regex to perfectly match how your 'Range' appears in the dropdown text.
                    # Example: "2250150830396 (IVORY COAST 9662)" -> Extracts "IVORY COAST 9662"
                    match = re.search(r'\((.*?)\)', number_text)
                    range_from_text = match.group(1).strip() if match else "Unknown Range"
                    
                    AVAILABLE_NUMBERS_LIST.append({"number": number_value, "range": range_from_text})
                    print(f"  - Discovered Number: {number_value}, Range: {range_from_text}")
            
            # 3. Select the target number based on the configuration (INDEX or SPECIFIC_NUMBER)
            if not AVAILABLE_NUMBERS_LIST:
                print("⛔ No usable numbers found in the dropdown on the SMS received page. Cannot proceed dynamically.")
                send_telegram_message(TELEGRAM_CHAT_ID, "<b>⛔ Error:</b> No phone numbers found on the SMS received page. Please check portal access.")
                return False # Cannot proceed without available numbers

            selected_successfully = False

            # Priority 1: Select by index if configured and valid
            if CONFIGURED_TARGET_NUMBER_INDEX is not None:
                if 0 <= CONFIGURED_TARGET_NUMBER_INDEX < len(AVAILABLE_NUMBERS_LIST):
                    selected_item = AVAILABLE_NUMBERS_LIST[CONFIGURED_TARGET_NUMBER_INDEX]
                    SELECTED_NUMBER_TO_QUERY = selected_item['number']
                    SELECTED_RANGE_TO_QUERY = selected_item['range']
                    selected_successfully = True
                    print(f"🎯 Selected number by index {CONFIGURED_TARGET_NUMBER_INDEX}: {SELECTED_NUMBER_TO_QUERY} ({SELECTED_RANGE_TO_QUERY})")
                    send_telegram_message(TELEGRAM_CHAT_ID, f"<i>Parameters found! Querying number at index {CONFIGURED_TARGET_NUMBER_INDEX}:</i> <code>{SELECTED_NUMBER_TO_QUERY}</code> <i>(Range: {SELECTED_RANGE_TO_QUERY})</i>")
                else:
                    print(f"⚠️ Configured index {CONFIGURED_TARGET_NUMBER_INDEX} is out of bounds ({len(AVAILABLE_NUMBERS_LIST)} available numbers).")
                    send_telegram_message(TELEGRAM_CHAT_ID, f"<b>⚠️ Warning:</b> Configured index out of bounds. Trying specific number or first available.")
            
            # Priority 2: Select by specific number string if configured and not already selected by index
            if not selected_successfully and CONFIGURED_SPECIFIC_NUMBER is not None:
                for item in AVAILABLE_NUMBERS_LIST:
                    if item['number'] == CONFIGURED_SPECIFIC_NUMBER:
                        SELECTED_NUMBER_TO_QUERY = CONFIGURED_SPECIFIC_NUMBER
                        SELECTED_RANGE_TO_QUERY = item['range']
                        selected_successfully = True
                        print(f"🎯 Selected configured specific number: {SELECTED_NUMBER_TO_QUERY} ({SELECTED_RANGE_TO_QUERY})")
                        send_telegram_message(TELEGRAM_CHAT_ID, f"<i>Parameters found! Querying specific number:</i> <code>{CONFIGURED_SPECIFIC_NUMBER}</code> <i>(Range: {SELECTED_RANGE_TO_QUERY})</i>")
                        break
                if not selected_successfully:
                    print(f"⚠️ Configured specific number {CONFIGURED_SPECIFIC_NUMBER} not found in the available list.")
                    send_telegram_message(TELEGRAM_CHAT_ID, f"<b>⚠️ Warning:</b> Configured specific number <code>{CONFIGURED_SPECIFIC_NUMBER}</code> not found.")
            
            # Final Fallback: If neither index nor specific number worked or was configured, use the first available
            if not selected_successfully:
                SELECTED_NUMBER_TO_QUERY = AVAILABLE_NUMBERS_LIST[0]['number']
                SELECTED_RANGE_TO_QUERY = AVAILABLE_NUMBERS_LIST[0]['range']
                print(f"🎯 Falling back to first available number: {SELECTED_NUMBER_TO_QUERY} ({SELECTED_RANGE_TO_QUERY})")
                send_telegram_message(TELEGRAM_CHAT_ID, f"<i>Parameters found! Falling back to first available:</i> <code>{SELECTED_NUMBER_TO_QUERY}</code> <i>(Range: {SELECTED_RANGE_TO_QUERY})</i>")
                selected_successfully = True # Mark as successfully selected via fallback

            return selected_successfully

        else:
            print("⛔ Could not find the 'Number' select element on the page. Website structure might have changed (e.g., 'name' attribute changed).")
            send_telegram_message(TELEGRAM_CHAT_ID, "<b>⛔ Error:</b> The 'Number' selection element was not found on the page. Portal structure may have changed.")
            return False # Cannot proceed if the select element isn't found

    except requests.exceptions.RequestException as e:
        print(f"❌ Error fetching SMS received page for dynamic params: {e}")
        send_telegram_message(TELEGRAM_CHAT_ID, f"<b>❌ Error:</b> Network error fetching SMS received page: <code>{e}</code>")
        return False
    except Exception as e:
        print(f"🐞 Error parsing dynamic SMS parameters: {e}")
        send_telegram_message(TELEGRAM_CHAT_ID, f"<b>🐞 Error:</b> Failed to parse dynamic SMS parameters: <code>{e}</code>")
        return False

def get_ivasms_data():
    """
    Makes a POST request to the SMS data endpoint using the authenticated session
    and dynamically obtained parameters (CSRF token, selected number/range).
    Returns the raw HTML response from the data endpoint, or None if the request fails.
    """
    # Ensure a number and range have been successfully selected before attempting to fetch
    if not SELECTED_NUMBER_TO_QUERY or not SELECTED_RANGE_TO_QUERY:
        print("❌ Cannot fetch SMS data: SELECTED_NUMBER_TO_QUERY or SELECTED_RANGE_TO_QUERY not set.")
        send_telegram_message(TELEGRAM_CHAT_ID, "<b>❌ Data Fetch Error:</b> Target number/range not identified. Cannot proceed.")
        return None

    print(f"📦 Attempting to fetch SMS data for Number: {SELECTED_NUMBER_TO_QUERY} ({SELECTED_RANGE_TO_QUERY})...")
    
    # Headers for the actual SMS data request (mimicking the AJAX call)
    sms_data_headers = {
        "accept": "text/html, */*; q=0.01",
        "accept-language": "en-US,en;q=0.9",
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "origin": "https://www.ivasms.com",
        "referer": SMS_RECEIVED_PAGE_URL, # Referer should be the page where this request originates
        "sec-ch-ua": "\"Not)A;Brand\";v=\"8\", \"Chromium\";v=\"138\", \"Google Chrome\";v=\"138\"",
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": "\"Windows\"",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
        "x-requested-with": "XMLHttpRequest", # Crucial for indicating an AJAX request
    }
    
    # Payload for the POST request to get SMS data
    # 'start' and 'end' dates can be specified to filter results.
    # Leaving them empty usually means "all data" or "recent data" as per site's default.
    # Example to fetch today's data in Bangladesh timezone:
    # from pytz import timezone
    # import pytz
    # current_date_bangladesh = datetime.now(pytz.timezone('Asia/Dhaka')).strftime("%Y-%m-%d")
    
    sms_data_payload = {
        "_token": DYNAMIC_CSRF_TOKEN,
        "start": "", # Set to a date string like "2025-07-01" to filter by start date
        "end": "",   # Set to a date string like "2025-07-18" (today) to filter by end date
        "Number": SELECTED_NUMBER_TO_QUERY,
        "Range": SELECTED_RANGE_TO_QUERY,
    }

    try:
        # Use the established requests session object for the POST request
        response = session.post(
            SMS_DATA_ENDPOINT,
            headers=sms_data_headers,
            data=sms_data_payload,
            timeout=20 # Extended timeout for network robustness
        )
        response.raise_for_status() # Raise an HTTPError for bad responses (4xx or 5xx)
        print("✅ Successfully fetched SMS data HTML.")
        return response.text
    except requests.exceptions.RequestException as e:
        print(f"❌ Error fetching SMS data from {SMS_DATA_ENDPOINT}: {e}")
        send_telegram_message(TELEGRAM_CHAT_ID, f"<b>❌ Data Fetch Error:</b> Network problem getting SMS data for <code>{SELECTED_NUMBER_TO_QUERY}</code>: <code>{e}</code>")
        return None

def parse_sms_html(html_content):
    """
    Parses the HTML response from the SMS data endpoint to extract individual SMS details.
    This function is CRITICAL and HIGHLY dependent on the EXACT HTML structure of the SMS list
    as returned by ivasms.com's '/getsms/number/sms' endpoint.
    You MUST inspect the actual HTML response and adjust the selectors accordingly.
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    messages = []
    
    # --- CUSTOMIZE THIS PART BASED ON YOUR INSPECTION OF THE ACTUAL HTML RESPONSE ---
    # The most common scenario is that the response is an HTML table body (<tbody>)
    # or a series of table rows (<tr>) that are injected into a table on the page.
    # Sometimes it's a list of divs, e.g., <div class="sms-message-card">...</div>

    rows_to_parse = []
    # Attempt 1: Find a specific table and its rows
    # Example: If your SMS table has class 'data-table'
    specific_table = soup.find('table', class_='table') # Adjust 'table' with actual class/ID if more specific
    if specific_table:
        # Try to find tbody first, then fallback to direct tr if tbody not found
        rows_to_parse = specific_table.find('tbody').find_all('tr') if specific_table.find('tbody') else specific_table.find_all('tr')
    
    # Attempt 2: If no specific table, assume the response itself is just list of <tr> elements
    if not rows_to_parse:
        rows_to_parse = soup.find_all('tr')

    # Attempt 3: If still no rows, consider if messages are in divs (e.g., class='sms-message')
    if not rows_to_parse:
        # Example of how you would look for divs:
        # messages_divs = soup.find_all('div', class_='sms-message-card')
        # if messages_divs:
        #     for msg_div in messages_divs:
        #         sender = msg_div.find('span', class_='sender').get_text(strip=True) if msg_div.find('span', class_='sender') else "N/A"
        #         content = msg_div.find('p', class_='content').get_text(strip=True) if msg_div.find('p', class_='content') else "N/A"
        #         time = msg_div.find('small', class_='timestamp').get_text(strip=True) if msg_div.find('small', class_='timestamp') else "N/A"
        #         status = msg_div.find('span', class_='status').get_text(strip=True) if msg_div.find('span', class_='status') else "N/A"
        #         messages.append(f"📞 <b>From:</b> <code>{sender}</code>\n💬 <b>Message:</b> <i>{content}</i>\n⏰ <b>Time:</b> {time}\n📊 <b>Status:</b> {status}")
        pass # Add your div-based parsing logic here if applicable


    if rows_to_parse:
        for row in rows_to_parse:
            # Skip header rows often identified by <th> tags
            if row.find('th'):
                continue

            cols = row.find_all('td')
            # Adjust column indices (e.g., cols[0], cols[1], etc.) based on your table's structure.
            # You might have different numbers of columns or different data in each.
            
            # --- FIX FOR SyntaxError: expected 'else' after 'if' expression ---
            # Corrected conditional access to list elements:
            # Check `len(cols) > index` to ensure the index is valid before accessing `cols[index]`
            if len(cols) >= 4: # Ensure there are at least 4 columns before attempting to access up to cols[3]
                sender = cols[0].get_text(strip=True) if len(cols) > 0 and cols[0] else "N/A"
                message_content = cols[1].get_text(strip=True) if len(cols) > 1 and cols[1] else "N/A"
                date_time = cols[2].get_text(strip=True) if len(cols) > 2 and cols[2] else "N/A"
                status = cols[3].get_text(strip=True) if len(cols) > 3 and cols[3] else "N/A"
                
                # Make the output attractive with emojis and HTML formatting
                messages.append(
                    f"📞 <b>From:</b> <code>{sender}</code>\n"
                    f"💬 <b>Message:</b> <i>{message_content}</i>\n"
                    f"⏰ <b>Time:</b> {date_time}\n"
                    f"📊 <b>Status:</b> {status}"
                )
            else:
                # Handle rows that don't match the expected column count (e.g., loading messages, empty rows)
                row_text = row.get_text(separator=' | ', strip=True)
                # Filter out common non-data messages like "No Data Found", "Loading...", etc.
                if row_text and "No Data Found" not in row_text and "Loading" not in row_text and "Showing 0 to 0 of 0 entries" not in row_text:
                    messages.append(f"ℹ️ <i>Unstructured row (possible metadata):</i> {row_text}")
    
    if not messages:
        # Fallback if no structured messages were parsed or no data found
        print("⚠️ No structured SMS data found or could not parse. Providing raw HTML snippet.")
        all_text = soup.get_text(separator='\n', strip=True)
        
        # Check for explicit "No Data" messages within the text to provide a clearer message
        if "No Data Found" in all_text or "No matching records found" in all_text or "Showing 0 to 0 of 0 entries" in all_text:
            return f"<b>✨ No new SMS messages found for</b> <code>{SELECTED_NUMBER_TO_QUERY}</code> <i>({SELECTED_RANGE_TO_QUERY})</i> 😔"
        
        # If it's not explicitly "No Data", but still couldn't parse structured info
        if len(all_text) > 1500: # Truncate if raw text is very long
            return (f"<b>⚠️ Could not parse detailed SMS for</b> <code>{SELECTED_NUMBER_TO_QUERY}</code>.\n\n"
                    f"<i>Here's a raw snippet of the response (first 1500 chars). "
                    f"You may need to update the <code>parse_sms_html</code> function based on actual HTML:</i>\n"
                    f"<code>{all_text[:1500]}...</code>")
        else:
            return (f"<b>⚠️ Could not parse detailed SMS for</b> <code>{SELECTED_NUMBER_TO_QUERY}</code>.\n\n"
                    f"<i>Here's the full raw response. "
                    f"You may need to update the <code>parse_sms_html</code> function based on actual HTML:</i>\n"
                    f"<code>{all_text}</code>")
    else:
        # Join all successfully parsed messages with an attractive separator
        return "\n\n➖➖➖➖➖➖➖➖➖➖\n\n".join(messages)

def main():
    # Get current time for logging purposes, relevant for Bangladesh
    # Current time is Friday, July 18, 2025 at 12:31:50 AM +06.
    current_time_bangladesh = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    print(f"🚀 IVA SMS Telegram Bot starting at {current_time_bangladesh}...")
    send_telegram_message(TELEGRAM_CHAT_ID, "🤖 *IVA SMS Bot Initiated!*") # Send a start-up message to Telegram

    # Step 1: Attempt to log in to the IVA SMS portal
    if not perform_login():
        print("🛑 Login failed. Bot cannot proceed without successful authentication.")
        send_telegram_message(TELEGRAM_CHAT_ID, "<b>🛑 Bot Halted:</b> Login to IVA SMS portal failed. Please resolve the issue (e.g., reCAPTCHA, credentials).")
        return # Exit if login fails

    # Step 2: After successful login, retrieve dynamic parameters for querying SMS data.
    # This includes getting the CSRF token and identifying available phone numbers and their ranges.
    if not get_dynamic_sms_params():
        print("🛑 Failed to get dynamic SMS parameters. Bot cannot proceed with data fetching.")
        send_telegram_message(TELEGRAM_CHAT_ID, "<b>🛑 Bot Halted:</b> Failed to retrieve dynamic SMS parameters from the portal. This is crucial for querying.")
        return # Exit if dynamic parameters cannot be retrieved

    # Step 3: Fetch the actual SMS data using the authenticated session and dynamic parameters.
    sms_html_data = get_ivasms_data()

    if sms_html_data:
        # Step 4: Parse HTML and format it for sending to Telegram.
        formatted_messages = parse_sms_html(sms_html_data)
        final_telegram_message = (
            f"🌟 <b>Latest SMS Data for</b> <code>{SELECTED_NUMBER_TO_QUERY}</code> "
            f"<i>({SELECTED_RANGE_TO_QUERY})</i> 🌟\n\n"
            f"{formatted_messages}"
        )
        send_telegram_message(TELEGRAM_CHAT_ID, final_telegram_message)
        print("🎉 SMS data successfully fetched and sent to Telegram.")
    else:
        # If get_ivasms_data() returned None, an error message would have already been sent to Telegram.
        print("❌ Failed to retrieve SMS data. See Telegram for details or check console logs.")

    print(f"✅ Bot run completed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    send_telegram_message(TELEGRAM_CHAT_ID, "😴 *IVA SMS Bot Finished Current Run.*")

if __name__ == "__main__":
    main()
