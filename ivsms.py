import requests
import json
import time
from bs4 import BeautifulSoup
import re
import urllib.parse
from datetime import datetime

# --- Configuration ---
# Replace with your actual Telegram Bot Token and Chat ID
TELEGRAM_BOT_TOKEN = "8106314263:AAE5hF1a0DHcRJGS2DSqcuWTDpttakZUT4Q"  # Replace with your bot's token
TELEGRAM_CHAT_ID = "-1002805004101"       # Replace with your group's chat ID (e.g., -123456789)

# --- IVA SMS URLs ---
LOGIN_URL = "https://www.ivasms.com/login"
SMS_RECEIVED_PAGE_URL = "https://www.ivasms.com/portal/sms/received"
SMS_DATA_ENDPOINT = "https://www.ivasms.com/portal/sms/received/getsms/number/sms"

# --- Your IVA SMS Credentials ---
# IMPORTANT: For production, use environment variables or a secure configuration method
# instead of hardcoding sensitive information directly in the script.
YOUR_EMAIL = "mehidiha94@gmail.com"  # Replace with your actual IVA SMS email
YOUR_PASSWORD = "Xd62924826"      # Replace with your actual IVA SMS password

# --- SMS Parameters to Query For ---
# This is the specific number you want to monitor.
# The script will try to find its corresponding 'Range' dynamically.
TARGET_NUMBER = "2250150830396" # <--- REPLACE THIS with your desired monitoring number

# Global session object for persistent cookies across requests
session = requests.Session()

# Global variables to store dynamically retrieved values
DYNAMIC_CSRF_TOKEN = ""
AVAILABLE_NUMBERS = {}  # To store number -> range mapping parsed from the portal
SELECTED_NUMBER_TO_QUERY = TARGET_NUMBER # This will be the number actually used in the query
SELECTED_RANGE_TO_QUERY = "" # This will be the range dynamically found for SELECTED_NUMBER_TO_QUERY


def get_csrf_token(html_content):
    """
    Extracts the CSRF token from the HTML content.
    Looks for a meta tag with name 'csrf-token' or a hidden input field named '_token'.
    """
    soup = BeautifulSoup(html_content, 'html.parser')

    # Try finding in meta tag first (common in Laravel)
    csrf_meta = soup.find('meta', {'name': 'csrf-token'})
    if csrf_meta and 'content' in csrf_meta.attrs:
        print("🔗 Found CSRF Token in meta tag.")
        return csrf_meta['content']

    # If not found in meta, try finding in hidden input field (also common in forms)
    csrf_input = soup.find('input', {'name': '_token'})
    if csrf_input and 'value' in csrf_input.attrs:
        print("🔗 Found CSRF Token in hidden input.")
        return csrf_input['value']

    print("⚠️ Warning: Could not find CSRF token in HTML content. This might cause issues.")
    return None

def send_telegram_message(chat_id, message_text):
    """
    Sends a message to the specified Telegram chat ID, supporting HTML parse mode
    and splitting long messages into chunks.
    """
    telegram_api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    MAX_MESSAGE_LENGTH = 4096 # Telegram's message character limit
    
    chunks = []
    current_chunk = ""
    
    # Attempt to split message into chunks, prioritizing breaking at natural SMS entry boundaries
    if "➖➖➖➖➖➖➖➖➖➖" in message_text:
        raw_entries = message_text.split("➖➖➖➖➖➖➖➖➖➖")
        for i, entry in enumerate(raw_entries):
            entry_to_add = entry.strip()
            if i > 0: # Add separator back for subsequent entries if they are not the first
                entry_to_add = "\n\n➖➖➖➖➖➖➖➖➖➖\n\n" + entry_to_add

            if len(current_chunk) + len(entry_to_add) > MAX_MESSAGE_LENGTH:
                if current_chunk: # Add current chunk if not empty
                    chunks.append(current_chunk.strip())
                current_chunk = entry_to_add # Start new chunk with this entry
            else:
                if current_chunk:
                    current_chunk += entry_to_add
                else:
                    current_chunk = entry_to_add # For the very first entry
        if current_chunk: # Add the very last chunk
            chunks.append(current_chunk.strip())
    else: # Fallback to splitting by lines if no specific separator or short message
        lines = message_text.split('\n')
        for line in lines:
            if len(current_chunk) + len(line) + 1 > MAX_MESSAGE_LENGTH: # +1 for newline
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
            "parse_mode": "HTML", # Use HTML for formatting (bold, italics, code)
            "disable_web_page_preview": True # Prevents Telegram from creating link previews for URLs
        }
        try:
            response = requests.post(telegram_api_url, json=payload, timeout=10)
            response.raise_for_status() # Raises an HTTPError for bad responses (4xx or 5xx)
            time.sleep(0.5) # Short delay to adhere to Telegram API rate limits (approx 30 messages/sec for user bots)
        except requests.exceptions.RequestException as e:
            print(f"❌ Error sending message part {i+1} to Telegram: {e}")
            # If sending fails, we might want to stop trying subsequent parts
            send_telegram_message(chat_id, f"<b>⚠️ Critical Error:</b> Failed to send further messages to Telegram. Check bot token/chat ID or API limits. Details: <code>{e}</code>")
            break # Stop trying to send remaining chunks if first one fails


def perform_login():
    """
    Handles the entire login process to IVA SMS portal.
    1. Fetches the login page to get initial CSRF token and cookies.
    2. Submits login credentials with the obtained token.
    3. Returns True if login appears successful (redirects to portal), False otherwise.
    """
    global DYNAMIC_CSRF_TOKEN # We'll need to set this after login

    print("🔑 Initiating login process...")
    send_telegram_message(TELEGRAM_CHAT_ID, "<i>Attempting to log in to IVA SMS portal...</i> 🔄")

    # Step 1: GET the login page to retrieve initial cookies and CSRF token for the login form
    print(f"🌐 Fetching login page: {LOGIN_URL}")
    try:
        response = session.get(LOGIN_URL, timeout=15)
        response.raise_for_status()
        initial_login_csrf_token = get_csrf_token(response.text)
        if not initial_login_csrf_token:
            print("❌ Failed to get initial CSRF token from login page.")
            send_telegram_message(TELEGRAM_CHAT_ID, "<b>❌ Login Failed:</b> Could not retrieve initial CSRF token from login page. Website structure might have changed.")
            return False

        print(f"🍪 Initial cookies acquired for login: {session.cookies.get_dict()}")

    except requests.exceptions.RequestException as e:
        print(f"❌ Error fetching login page: {e}")
        send_telegram_message(TELEGRAM_CHAT_ID, f"<b>❌ Login Failed:</b> Network error accessing login page: <code>{e}</code>")
        return False

    # Step 2: Prepare and send POST data for login
    # WARNING: The 'g-recaptcha-response' field is a major hurdle for automation.
    # A real reCAPTCHA token needs to be obtained by solving a CAPTCHA (manually or via a service).
    # The current placeholder will cause login to FAIL unless the site has no active reCAPTCHA.
    login_data = {
        "_token": initial_login_csrf_token,
        "email": YOUR_EMAIL,
        "password": YOUR_PASSWORD,
        "remember": "on",
        "g-recaptcha-response": "MANUAL_OR_SOLVED_RECAPTCHA_TOKEN", # <--- CRITICAL: THIS MUST BE A VALID, SOLVED TOKEN
        "submit": "register" # This is likely the name of the submit button in the form
    }

    login_headers = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "accept-language": "en-US,en;q=0.9",
        "cache-control": "max-age=0",
        "content-type": "application/x-www-form-urlencoded",
        "origin": "https://www.ivasms.com",
        "referer": LOGIN_URL,
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
        "priority": "u=0, i" # Keep as provided in curl, might be important for some sites
    }

    print("➡️ Sending login credentials...")
    try:
        login_response = session.post(LOGIN_URL, headers=login_headers, data=login_data, allow_redirects=True, timeout=20)
        login_response.raise_for_status()

        # Check if login was successful by examining the final URL after redirects
        if "/portal" in login_response.url or "/dashboard" in login_response.url:
            print(f"✅ Login successful! Redirected to: {login_response.url}")
            send_telegram_message(TELEGRAM_CHAT_ID, "<b>✅ Login Successful!</b> Fetching SMS data now... 🚀")
            # The 'session' object now automatically holds the authenticated cookies.
            return True
        else:
            print(f"❌ Login failed. Not redirected to portal. Final URL: {login_response.url}")
            print(f"Response content snippet: {login_response.text[:1000]}...")
            send_telegram_message(TELEGRAM_CHAT_ID, "<b>❌ Login Failed:</b> Incorrect credentials, reCAPTCHA not solved, or unexpected redirect. Please check manually.")
            return False

    except requests.exceptions.RequestException as e:
        print(f"❌ Error during login POST request: {e}")
        send_telegram_message(TELEGRAM_CHAT_ID, f"<b>❌ Login Failed:</b> Network error during POST request: <code>{e}</code>")
        return False

def get_dynamic_sms_params():
    """
    After successful login, fetches the /portal/sms/received page to extract
    the CSRF token needed for the SMS data query and available numbers/ranges from dropdowns.
    """
    global DYNAMIC_CSRF_TOKEN, AVAILABLE_NUMBERS, SELECTED_NUMBER_TO_QUERY, SELECTED_RANGE_TO_QUERY

    print(f"✨ Fetching SMS received page to get dynamic parameters: {SMS_RECEIVED_PAGE_URL}")
    send_telegram_message(TELEGRAM_CHAT_ID, "<i>Retrieving dynamic SMS query parameters...</i> 🔍")

    try:
        response = session.get(SMS_RECEIVED_PAGE_URL, timeout=15)
        response.raise_for_status() # Check for HTTP errors

        soup = BeautifulSoup(response.text, 'html.parser')

        # 1. Extract CSRF Token for the SMS data request form
        # This token is crucial for the POST request to getsms/number/sms
        current_csrf_token = get_csrf_token(response.text)
        if current_csrf_token:
            DYNAMIC_CSRF_TOKEN = current_csrf_token
            print(f"🔑 Updated CSRF Token for SMS data request: {DYNAMIC_CSRF_TOKEN}")
        else:
            print("⚠️ Warning: Could not get a new CSRF token from SMS received page. This might indicate an issue or a different token management.")
            send_telegram_message(TELEGRAM_CHAT_ID, "<b>⚠️ Warning:</b> Could not find dynamic token for SMS query. Might fail.")
        
        # 2. Extract available numbers and their associated ranges from dropdowns
        # You need to carefully inspect the HTML of the 'sms/received' page for the SELECT element
        # that corresponds to the phone number selection.
        # Look for attributes like 'name' or 'id'. Common names are 'Number', 'msisdn', 'phone_number'.
        
        # Example: Assuming the select tag has name="Number"
        number_select = soup.find('select', {'name': 'Number'})
        if number_select:
            print("🔢 Found 'Number' selection element. Parsing options...")
            for option in number_select.find_all('option'):
                number_value = option.get('value')
                number_text = option.get_text(strip=True)
                
                if number_value and number_text:
                    # The 'Range' value is often embedded in the option's text like "Number (Range)"
                    # Adjust this regex to perfectly match how your 'Range' appears in the dropdown text.
                    # e.g., "2250150830396 (IVORY COAST 9662)" -> Extracts "IVORY COAST 9662"
                    match = re.search(r'\((.*?)\)', number_text)
                    range_from_text = match.group(1).strip() if match else "Unknown Range"
                    
                    AVAILABLE_NUMBERS[number_value] = range_from_text
                    print(f"  - Discovered Number: {number_value}, Range: {range_from_text}")
            
            if AVAILABLE_NUMBERS:
                # Prioritize using the pre-configured TARGET_NUMBER
                if TARGET_NUMBER in AVAILABLE_NUMBERS:
                    SELECTED_NUMBER_TO_QUERY = TARGET_NUMBER
                    SELECTED_RANGE_TO_QUERY = AVAILABLE_NUMBERS[TARGET_NUMBER]
                    print(f"🎯 Successfully matched configured target number: {SELECTED_NUMBER_TO_QUERY} (Range: {SELECTED_RANGE_TO_QUERY})")
                    send_telegram_message(TELEGRAM_CHAT_ID, f"<i>Parameters found! Target Number: </i><code>{SELECTED_NUMBER_TO_QUERY}</code> <i>(Range: {SELECTED_RANGE_TO_QUERY})</i>")
                else:
                    # If TARGET_NUMBER is not found among available options, use the first one available
                    first_available_number = list(AVAILABLE_NUMBERS.keys())[0]
                    first_available_range = AVAILABLE_NUMBERS[first_available_number]
                    SELECTED_NUMBER_TO_QUERY = first_available_number
                    SELECTED_RANGE_TO_QUERY = first_available_range
                    print(f"⚠️ Configured target number {TARGET_NUMBER} not found among available options. Using first available: {SELECTED_NUMBER_TO_QUERY} ({SELECTED_RANGE_TO_QUERY})")
                    send_telegram_message(TELEGRAM_CHAT_ID, f"<b>⚠️ Warning:</b> Configured target number <code>{TARGET_NUMBER}</code> not found in portal options. Using first available: <code>{SELECTED_NUMBER_TO_QUERY}</code> <i>(Range: {SELECTED_RANGE_TO_QUERY})</i>")
            else:
                print("⛔ No numbers found in the dropdown on the SMS received page. Cannot proceed dynamically.")
                send_telegram_message(TELEGRAM_CHAT_ID, "<b>⛔ Error:</b> No phone numbers found on the SMS received page. Please check portal access.")
                return False # Cannot proceed without a number
        else:
            print("⛔ Could not find the 'Number' select element on the page. Website structure might have changed.")
            send_telegram_message(TELEGRAM_CHAT_ID, "<b>⛔ Error:</b> The 'Number' selection element was not found on the page. Portal structure may have changed.")
            return False # Cannot proceed if the select element isn't found

        return True
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
    print(f"📦 Attempting to fetch SMS data for Number: {SELECTED_NUMBER_TO_QUERY} ({SELECTED_RANGE_TO_QUERY})...")
    
    # Headers for the actual SMS data request (from your initial curl)
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
        "x-requested-with": "XMLHttpRequest", # Indicates an AJAX request
    }
    
    # Payload for the POST request
    # You can specify a date range here if needed, e.g., for "today"
    # today_date_str = datetime.now().strftime("%Y-%m-%d")
    
    sms_data_payload = {
        "_token": DYNAMIC_CSRF_TOKEN,
        "start": "", # Keep empty for all history, or set a date like "2025-07-01"
        "end": "",   # Keep empty, or set a date like today_date_str
        "Number": SELECTED_NUMBER_TO_QUERY,
        "Range": SELECTED_RANGE_TO_QUERY,
    }

    try:
        # Use the established session object for the POST request
        response = session.post(
            SMS_DATA_ENDPOINT,
            headers=sms_data_headers,
            data=sms_data_payload,
            timeout=20 # Extended timeout for data fetching
        )
        response.raise_for_status() # Check for HTTP errors (e.g., 404, 500)
        print("✅ Successfully fetched SMS data HTML.")
        return response.text
    except requests.exceptions.RequestException as e:
        print(f"❌ Error fetching SMS data from {SMS_DATA_ENDPOINT}: {e}")
        send_telegram_message(TELEGRAM_CHAT_ID, f"<b>❌ Data Fetch Error:</b> Network problem getting SMS data for <code>{SELECTED_NUMBER_TO_QUERY}</code>: <code>{e}</code>")
        return None

def parse_sms_html(html_content):
    """
    Parses the HTML response from the SMS data endpoint to extract individual SMS details.
    This part is HIGHLY dependent on the actual HTML structure of the SMS list on ivasms.com.
    You MUST inspect the HTML returned by get_ivasms_data() and adjust the selectors accordingly.
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    messages = []
    
    # --- CUSTOMIZE THIS PART BASED ON ACTUAL HTML STRUCTURE ---
    # Common scenarios:
    # 1. SMS messages are in rows of a <table> element.
    #    Look for a table with a specific class or ID, then iterate through its <tbody> and <tr> elements.
    # 2. SMS messages are in individual <div> or <p> elements with specific classes.
    #    Use soup.find_all('div', class_='sms-message-container') for example.

    # Example: Assuming the SMS data is returned as part of an HTML table's <tbody> or just raw <tr>s
    # Try finding rows within a common table structure first.
    sms_table = soup.find('table', class_='table') # Replace 'table' with actual class/ID if more specific
    if sms_table:
        rows_to_parse = sms_table.find('tbody').find_all('tr') if sms_table.find('tbody') else sms_table.find_all('tr')
    else:
        # Fallback: Maybe the response itself is just a set of <tr> elements, or divs
        rows_to_parse = soup.find_all('tr') # Try finding any <tr> if not within a specific table
        if not rows_to_parse:
            # If not table rows, try finding specific divs that hold message info
            # Example: rows_to_parse = soup.find_all('div', class_='sms-entry-card')
            pass # Keep this generic for now, requires manual inspection

    if rows_to_parse:
        for row in rows_to_parse:
            cols = row.find_all('td')
            # Adjust column indices and what you extract based on actual data columns
            # Example: Assuming columns are: Sender, Message, Date/Time, Status
            if len(cols) >= 4:
                sender = cols[0].get_text(strip=True) if cols[0] else "N/A"
                message_content = cols[1].get_text(strip=True) if cols[1] else "N/A"
                date_time = cols[2].get_text(strip=True) if cols[2] else "N/A"
                status = cols[3].get_text(strip=True) if cols[3] else "N/A"
                
                # Make the output attractive with emojis and HTML formatting
                messages.append(
                    f"📞 <b>From:</b> <code>{sender}</code>\n"
                    f"💬 <b>Message:</b> <i>{message_content}</i>\n"
                    f"⏰ <b>Time:</b> {date_time}\n"
                    f"📊 <b>Status:</b> {status}"
                )
            else:
                # If a row has an unexpected number of columns or is a header/footer row, handle it gracefully
                row_text = row.get_text(separator=' | ', strip=True)
                if row_text and "No Data Found" not in row_text and "Loading" not in row_text: # Filter out common empty/loading messages
                     # Only add if it's not a known non-data row
                    messages.append(f"ℹ️ <i>Unstructured row (possible metadata):</i> {row_text}")
    
    if not messages:
        # Fallback if no structured messages were parsed or no data found
        print("⚠️ No structured SMS data found or could not parse. Providing raw HTML snippet.")
        all_text = soup.get_text(separator='\n', strip=True)
        # Check for specific "No Data" messages in the raw text
        if "No Data Found" in all_text or "No matching records found" in all_text:
            return f"<b>✨ No new SMS messages found for</b> <code>{SELECTED_NUMBER_TO_QUERY}</code> <i>({SELECTED_RANGE_TO_QUERY})</i> 😔"
        
        # If it's not explicitly "No Data", but still unparsed
        if len(all_text) > 1500:
            return (f"<b>⚠️ Could not parse detailed SMS for</b> <code>{SELECTED_NUMBER_TO_QUERY}</code>.\n\n"
                    f"<i>Here's a raw snippet of the response (first 1500 chars). "
                    f"You may need to update <code>parse_sms_html</code>:</i>\n"
                    f"<code>{all_text[:1500]}...</code>")
        else:
            return (f"<b>⚠️ Could not parse detailed SMS for</b> <code>{SELECTED_NUMBER_TO_QUERY}</code>.\n\n"
                    f"<i>Here's the full raw response. "
                    f"You may need to update <code>parse_sms_html</code>:</i>\n"
                    f"<code>{all_text}</code>")
    else:
        # Join parsed messages with an attractive separator
        return "\n\n➖➖➖➖➖➖➖➖➖➖\n\n".join(messages)

def main():
    print(f"🚀 IVA SMS Telegram Bot starting at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}...")
    send_telegram_message(TELEGRAM_CHAT_ID, "🤖 *IVA SMS Bot Initiated!*") # Initial bot start message

    # Step 1: Perform Login
    if not perform_login():
        print("🛑 Login failed. Bot cannot proceed without successful authentication.")
        send_telegram_message(TELEGRAM_CHAT_ID, "<b>🛑 Bot Halted:</b> Login to IVA SMS portal failed. Please resolve the issue (e.g., reCAPTCHA, credentials).")
        return

    # Step 2: Get Dynamic SMS Query Parameters (CSRF token, available numbers/ranges)
    if not get_dynamic_sms_params():
        print("🛑 Failed to get dynamic SMS parameters. Bot cannot proceed with data fetching.")
        send_telegram_message(TELEGRAM_CHAT_ID, "<b>🛑 Bot Halted:</b> Failed to retrieve dynamic SMS parameters from the portal. This is crucial for querying.")
        return

    # Step 3: Fetch SMS Data
    sms_html_data = get_ivasms_data()

    if sms_html_data:
        # Step 4: Parse HTML and format for Telegram
        formatted_messages = parse_sms_html(sms_html_data)
        final_telegram_message = (
            f"🌟 <b>Latest SMS Data for</b> <code>{SELECTED_NUMBER_TO_QUERY}</code> "
            f"<i>({SELECTED_RANGE_TO_QUERY})</i> 🌟\n\n"
            f"{formatted_messages}"
        )
        send_telegram_message(TELEGRAM_CHAT_ID, final_telegram_message)
        print("🎉 SMS data successfully fetched and sent to Telegram.")
    else:
        # Error message already sent by get_ivasms_data()
        print("❌ Failed to retrieve SMS data. See Telegram for details or check logs.")

    print(f"✅ Bot run completed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    send_telegram_message(TELEGRAM_CHAT_ID, "😴 *IVA SMS Bot Finished Current Run.*")

if __name__ == "__main__":
    main()
