import requests
import json
import time
from bs4 import BeautifulSoup
import re
import urllib.parse
from datetime import datetime

# --- Configuration ---
TELEGRAM_BOT_TOKEN = "8106314263:AAE5hF1a0DHcRJGS2DSqcuWTDpttakZUT4Q"  # Replace with your bot's token
TELEGRAM_CHAT_ID = "-1002805004101"       # Replace with your group's chat ID (e.g., -123456789)

# --- IVA SMS URLs ---
LOGIN_URL = "https://www.ivasms.com/login"
SMS_RECEIVED_PAGE_URL = "https://www.ivasms.com/portal/sms/received"
SMS_DATA_ENDPOINT = "https://www.ivasms.com/portal/sms/received/getsms/number/sms"

# --- Your Credentials ---
# IMPORTANT: Store these securely (e.g., environment variables, config file) in production.
# For demonstration, they are here.
YOUR_EMAIL = "mehidiha94@gmail.com"  # Replace with your actual IVA SMS email
YOUR_PASSWORD = "Xd62924826"      # Replace with your actual IVA SMS password

# SMS parameters to retrieve (can be dynamically chosen later)
TARGET_NUMBER = "2250150830396" # Replace with the specific number you want to monitor
# Note: TARGET_RANGE will ideally be extracted dynamically based on TARGET_NUMBER

# Global session object for persistent cookies
session = requests.Session()

# Global variables to store dynamically retrieved values
DYNAMIC_CSRF_TOKEN = ""
AVAILABLE_NUMBERS = {}  # To store number -> range mapping from dropdowns
SELECTED_NUMBER_TO_QUERY = TARGET_NUMBER # This will be the number we ultimately query for
SELECTED_RANGE_TO_QUERY = "" # This will be the range dynamically found for SELECTED_NUMBER_TO_QUERY


def get_csrf_token(html_content):
    """
    Extracts the CSRF token from the HTML content.
    Looks for a meta tag or a hidden input field named '_token'.
    """
    soup = BeautifulSoup(html_content, 'html.parser')

    # Try finding in meta tag first
    csrf_meta = soup.find('meta', {'name': 'csrf-token'})
    if csrf_meta and 'content' in csrf_meta.attrs:
        print("🔗 Found CSRF Token in meta tag.")
        return csrf_meta['content']

    # If not found in meta, try finding in hidden input
    csrf_input = soup.find('input', {'name': '_token'})
    if csrf_input and 'value' in csrf_input.attrs:
        print("🔗 Found CSRF Token in hidden input.")
        return csrf_input['value']

    print("⚠️ Warning: Could not find CSRF token in HTML content.")
    return None

def perform_login():
    """
    Performs the login sequence to ivasms.com to get authenticated cookies and tokens.
    Returns True on successful login, False otherwise.
    """
    print("🔑 Initiating login process...")
    send_telegram_message(TELEGRAM_CHAT_ID, "<i>Attempting to log in to IVA SMS portal...</i> 🔄")

    # Step 1: GET the login page to retrieve initial cookies and CSRF token
    print(f"🌐 Fetching login page: {LOGIN_URL}")
    try:
        response = session.get(LOGIN_URL, timeout=15)
        response.raise_for_status()
        initial_csrf_token = get_csrf_token(response.text)
        if not initial_csrf_token:
            print("❌ Failed to get initial CSRF token from login page.")
            send_telegram_message(TELEGRAM_CHAT_ID, "<b>❌ Login Failed:</b> Could not retrieve initial CSRF token from login page. Website structure might have changed.")
            return False

        print(f"🍪 Initial cookies from login page: {session.cookies.get_dict()}")

    except requests.exceptions.RequestException as e:
        print(f"❌ Error fetching login page: {e}")
        send_telegram_message(TELEGRAM_CHAT_ID, f"<b>❌ Login Failed:</b> Network error accessing login page: {e}")
        return False

    # Step 2: Prepare POST data for login
    # WARNING: reCAPTCHA automation is complex and might require external services.
    # The current value is a placeholder and will likely FAIL for real reCAPTCHA protected logins.
    login_data = {
        "_token": initial_csrf_token,
        "email": YOUR_EMAIL,
        "password": YOUR_PASSWORD,
        "remember": "on",
        "g-recaptcha-response": "MANUAL_OR_SOLVED_RECAPTCHA_TOKEN", # <<< CRITICAL: REPLACE WITH A REAL TOKEN
        "submit": "register" # Based on your curl, this looks like the name of the submit button
    }

    login_headers = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "accept-language": "en-US,en;q=0.9",
        "cache-control": "max-age=0",
        "content-type": "application/x-www-form-urlencoded",
        "origin": "https://www.ivasms.com",
        "referer": LOGIN_URL,
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
        # Keep other sec-ch-ua, sec-fetch headers if they are critical, but basic ones are often enough for login
    }

    print("Sending login credentials... ➡️")
    try:
        login_response = session.post(LOGIN_URL, headers=login_headers, data=login_data, allow_redirects=True, timeout=20) # Increased timeout
        login_response.raise_for_status()

        if "/portal" in login_response.url or "/dashboard" in login_response.url: # Check for common post-login redirects
            print(f"✅ Login successful! Redirected to: {login_response.url}")
            send_telegram_message(TELEGRAM_CHAT_ID, "<b>✅ Login Successful!</b> Fetching SMS data now... 🚀")
            return True
        else:
            print(f"❌ Login failed. Not redirected to portal. Final URL: {login_response.url}")
            print(f"Response content snippet: {login_response.text[:1000]}...")
            send_telegram_message(TELEGRAM_CHAT_ID, "<b>❌ Login Failed:</b> Incorrect credentials, reCAPTCHA not solved, or unexpected redirect. Please check manually.")
            return False

    except requests.exceptions.RequestException as e:
        print(f"❌ Error during login POST request: {e}")
        send_telegram_message(TELEGRAM_CHAT_ID, f"<b>❌ Login Failed:</b> Network error during POST request: {e}")
        return False

def get_dynamic_sms_params():
    """
    After successful login, fetches the sms/received page to extract
    the CSRF token for the SMS data request and available numbers/ranges.
    """
    global DYNAMIC_CSRF_TOKEN, AVAILABLE_NUMBERS, SELECTED_NUMBER_TO_QUERY, SELECTED_RANGE_TO_QUERY

    print(f"✨ Fetching SMS received page to get dynamic parameters: {SMS_RECEIVED_PAGE_URL}")
    send_telegram_message(TELEGRAM_CHAT_ID, "<i>Retrieving dynamic SMS query parameters...</i> 🔍")

    try:
        response = session.get(SMS_RECEIVED_PAGE_URL, timeout=15)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')

        # Extract CSRF Token for the SMS data request (it might be different from login token)
        current_csrf_token = get_csrf_token(response.text)
        if current_csrf_token:
            DYNAMIC_CSRF_TOKEN = current_csrf_token
            print(f"🔑 Updated CSRF Token for SMS data request: {DYNAMIC_CSRF_TOKEN}")
        else:
            print("⚠️ Warning: Could not get a new CSRF token from SMS received page. Proceeding with potentially old one.")

        # Extract available numbers and ranges from dropdowns
        # Inspect your ivasms.com portal's HTML for the <select> element's name/id for numbers.
        # Common names are 'number', 'msisdn', 'phone_number'.
        number_select = soup.find('select', {'name': 'Number'})
        if number_select:
            print("🔢 Found 'Number' select element. Parsing options...")
            for option in number_select.find_all('option'):
                number_value = option.get('value')
                number_text = option.get_text(strip=True)
                if number_value and number_text:
                    # Attempt to parse range from the option text, e.g., "Number (Range)"
                    # Adjust the regex based on the actual format of the option text on ivasms.com
                    match = re.search(r'\((.*?)\)', number_text)
                    range_from_text = match.group(1).strip() if match else "Unknown Range"
                    AVAILABLE_NUMBERS[number_value] = range_from_text
                    print(f"  - Discovered Number: {number_value}, Range: {range_from_text}")

            if AVAILABLE_NUMBERS:
                # Try to use the pre-configured TARGET_NUMBER
                if SELECTED_NUMBER_TO_QUERY in AVAILABLE_NUMBERS:
                    SELECTED_RANGE_TO_QUERY = AVAILABLE_NUMBERS[SELECTED_NUMBER_TO_QUERY]
                    print(f"🎯 Using configured target number: {SELECTED_NUMBER_TO_QUERY} and its range: {SELECTED_RANGE_TO_QUERY}")
                    send_telegram_message(TELEGRAM_CHAT_ID, f"<i>Parameters found! Target Number: </i><code>{SELECTED_NUMBER_TO_QUERY}</code> <i>(Range: {SELECTED_RANGE_TO_QUERY})</i>")
                else:
                    # Fallback to the first available if the target isn't found
                    first_number = list(AVAILABLE_NUMBERS.keys())[0]
                    first_range = AVAILABLE_NUMBERS[first_number]
                    SELECTED_NUMBER_TO_QUERY = first_number
                    SELECTED_RANGE_TO_QUERY = first_range
                    print(f"⚠️ Configured target number {TARGET_NUMBER} not found. Using first available: {SELECTED_NUMBER_TO_QUERY} ({SELECTED_RANGE_TO_QUERY})")
                    send_telegram_message(TELEGRAM_CHAT_ID, f"<b>⚠️ Warning:</b> Configured target number <code>{TARGET_NUMBER}</code> not found. Using first available: <code>{SELECTED_NUMBER_TO_QUERY}</code> <i>(Range: {SELECTED_RANGE_TO_QUERY})</i>")
            else:
                print("⛔ No numbers found in the dropdown. Using hardcoded defaults for query.")
                SELECTED_RANGE_TO_QUERY = TARGET_RANGE # Fallback to hardcoded if no numbers parsed
                send_telegram_message(TELEGRAM_CHAT_ID, "<b>⛔ Error:</b> No phone numbers found on the SMS received page. Using default hardcoded values.")
        else:
            print("⛔ Could not find the 'Number' select element on the page. Using hardcoded defaults.")
            SELECTED_RANGE_TO_QUERY = TARGET_RANGE # Fallback to hardcoded if select element not found
            send_telegram_message(TELEGRAM_CHAT_ID, "<b>⛔ Error:</b> The 'Number' selection element was not found on the page. Using default hardcoded values.")

        return True
    except requests.exceptions.RequestException as e:
        print(f"❌ Error fetching SMS received page for dynamic params: {e}")
        send_telegram_message(TELEGRAM_CHAT_ID, f"<b>❌ Error:</b> Network error fetching SMS received page: {e}")
        return False
    except Exception as e:
        print(f"🐞 Error parsing dynamic SMS parameters: {e}")
        send_telegram_message(TELEGRAM_CHAT_ID, f"<b>🐞 Error:</b> Failed to parse dynamic SMS parameters: {e}")
        return False

def get_ivasms_data():
    """
    Makes a POST request to ivasms.com using the authenticated session and dynamic parameters.
    Returns the response text, or None if the request fails.
    """
    print(f"📦 Attempting to fetch SMS data for Number: {SELECTED_NUMBER_TO_QUERY} ({SELECTED_RANGE_TO_QUERY})...")
    
    sms_headers = {
        "accept": "text/html, */*; q=0.01",
        "accept-language": "en-US,en;q=0.9",
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "origin": "https://www.ivasms.com",
        "referer": SMS_RECEIVED_PAGE_URL, # Referer should be the page where the request originates
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
        "x-requested-with": "XMLHttpRequest", # Crucial for AJAX requests
        # You can add the 'priority' and 'sec-ch-ua' headers if necessary, but often not for direct API calls.
    }
    
    # Optional: Set date range for query. Current time is July 17, 2025.
    today = datetime.now().strftime("%Y-%m-%d")
    
    sms_data_payload = {
        "_token": DYNAMIC_CSRF_TOKEN,
        "start": "", # "2025-07-01", # Example: Start of current month
        "end": "",   # today, # Example: Today's date
        "Number": SELECTED_NUMBER_TO_QUERY,
        "Range": SELECTED_RANGE_TO_QUERY,
    }

    try:
        response = session.post( # Use the global session object
            SMS_DATA_ENDPOINT,
            headers=sms_headers,
            data=sms_data_payload,
            timeout=20 # Extended timeout
        )
        response.raise_for_status()
        print("✅ Successfully fetched SMS data.")
        return response.text
    except requests.exceptions.RequestException as e:
        print(f"❌ Error fetching SMS data from {SMS_DATA_ENDPOINT}: {e}")
        send_telegram_message(TELEGRAM_CHAT_ID, f"<b>❌ Data Fetch Error:</b> Network problem getting SMS data: {e}")
        return None

def parse_sms_html(html_content):
    """
    Parses the HTML response from the SMS_DATA_ENDPOINT to extract SMS details.
    You MUST inspect the actual HTML structure of the SMS data to accurately extract information.
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    messages = []
    
    # --- IMPORTANT: CUSTOMIZE THIS PART BASED ON ACTUAL HTML STRUCTURE ---
    # Look for the table or container holding the SMS messages
    # Common selectors: <table> with specific id/class, or <div> containers for each message.
    
    # Example: Searching for a table. Adjust 'class_' or 'id' based on what you find.
    # From your curl, the data response looks like HTML table content.
    sms_table_rows = soup.find_all('tr') # Find all table rows, assuming response is a table snippet
    
    if sms_table_rows:
        for row in sms_table_rows:
            cols = row.find_all('td')
            # Assuming a structure like: <td>Sender</td> <td>Message</td> <td>Date/Time</td> <td>Status</td>
            if len(cols) >= 4: # Adjust number of columns based on actual data
                sender = cols[0].get_text(strip=True)
                message_content = cols[1].get_text(strip=True)
                date_time = cols[2].get_text(strip=True)
                status = cols[3].get_text(strip=True)
                messages.append(f"📞 <b>From:</b> <code>{sender}</code>\n💬 <b>Message:</b> <i>{message_content}</i>\n⏰ <b>Time:</b> {date_time}\n📊 <b>Status:</b> {status}")
            else:
                # Fallback if a row has an unexpected number of columns
                row_text = row.get_text(separator=' | ', strip=True)
                if row_text:
                    messages.append(f"ℹ️ <i>Unstructured SMS row detected:</i> {row_text}")
    
    if not messages:
        # Fallback if no structured messages were parsed
        print("⚠️ No structured SMS data found or could not parse. Providing raw snippet.")
        all_text = soup.get_text(separator='\n', strip=True)
        if len(all_text) > 1500:
            return f"<b>⚠️ No detailed SMS found for </b><code>{SELECTED_NUMBER_TO_QUERY}</code>.\n\n<i>Here's a raw snippet of the response (first 1500 chars):</i>\n<code>{all_text[:1500]}...</code>"
        else:
            return f"<b>⚠️ No detailed SMS found for </b><code>{SELECTED_NUMBER_TO_QUERY}</code>.\n\n<i>Here's the full raw response:</i>\n<code>{all_text}</code>"
    else:
        # Join messages with a separator for readability in Telegram
        return "\n\n➖➖➖➖➖➖➖➖➖➖\n\n".join(messages)

def send_telegram_message(chat_id, message):
    """
    Sends a message to the specified Telegram chat ID, splitting long messages.
    """
    telegram_api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    MAX_MESSAGE_LENGTH = 4096
    
    # Attempt to split message into chunks at logical points (e.g., between full SMS entries)
    chunks = []
    current_chunk = ""
    
    # Prefer splitting by the SMS entry separator
    if "➖➖➖➖➖➖➖➖➖➖" in message:
        raw_entries = message.split("➖➖➖➖➖➖➖➖➖➖")
        for entry in raw_entries:
            if entry.strip(): # Ensure it's not an empty string from multiple separators
                if len(current_chunk) + len(entry) + len("\n\n➖➖➖➖➖➖➖➖➖➖\n\n") > MAX_MESSAGE_LENGTH:
                    if current_chunk: # Add current chunk if not empty
                        chunks.append(current_chunk.strip())
                    current_chunk = entry.strip()
                else:
                    if current_chunk:
                        current_chunk += "\n\n➖➖➖➖➖➖➖➖➖➖\n\n" + entry.strip()
                    else:
                        current_chunk = entry.strip()
        if current_chunk:
            chunks.append(current_chunk.strip())
    else: # Fallback to splitting by lines if no specific separator or short message
        lines = message.split('\n')
        for line in lines:
            if len(current_chunk) + len(line) + 1 > MAX_MESSAGE_LENGTH:
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


    print(f"📤 Message size: {len(message)} chars. Sending in {len(chunks)} parts to Telegram.")
    for i, chunk in enumerate(chunks):
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True # Prevent Telegram from trying to unfurl URLs
        }
        try:
            response = requests.post(telegram_api_url, json=payload, timeout=10)
            response.raise_for_status()
            # print(f"Sent part {i+1} successfully.") # Uncomment for verbose success
            time.sleep(1) # Small delay to avoid Telegram API limits (1 sec per message is safe)
        except requests.exceptions.RequestException as e:
            print(f"❌ Error sending message part {i+1} to Telegram: {e}")
            # Optionally, break if sending fails to avoid spamming if API token is bad etc.
            break 


def main():
    print(f"Starting IVA SMS Telegram Bot at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}...")
    send_telegram_message(TELEGRAM_CHAT_ID, "🤖 *IVA SMS Bot Starting...*") # Use Markdown here as it's the first message

    # Attempt to log in
    if not perform_login():
        print("🛑 Login failed. Exiting.")
        send_telegram_message(TELEGRAM_CHAT_ID, "<b>🛑 Bot Stopped:</b> Login failed. Please check credentials or reCAPTCHA handling.")
        return

    # After successful login, get dynamic parameters for SMS fetching
    if not get_dynamic_sms_params():
        print("🛑 Failed to get dynamic SMS parameters. Exiting.")
        send_telegram_message(TELEGRAM_CHAT_ID, "<b>🛑 Bot Stopped:</b> Failed to retrieve dynamic SMS parameters. Site structure may have changed.")
        return

    # Now attempt to get SMS data using the authenticated session and dynamic parameters
    sms_html_data = get_ivasms_data()

    if sms_html_data:
        formatted_messages = parse_sms_html(sms_html_data)
        final_telegram_message = f"🌟 <b>Latest SMS Data for</b> <code>{SELECTED_NUMBER_TO_QUERY}</code> <i>({SELECTED_RANGE_TO_QUERY})</i> 🌟\n\n{formatted_messages}"
        send_telegram_message(TELEGRAM_CHAT_ID, final_telegram_message)
        print("🎉 SMS data successfully fetched and sent to Telegram.")
    else:
        error_message = "Failed to retrieve SMS data from ivasms.com after login. Check logs for more details."
        send_telegram_message(TELEGRAM_CHAT_ID, f"<b>⚠️ Data Fetch Failed:</b> {error_message}")
        print("❌ Failed to retrieve SMS data. See Telegram for brief error, logs for full.")

    print(f"Bot run completed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    send_telegram_message(TELEGRAM_CHAT_ID, "😴 *IVA SMS Bot Finished Current Run.*")

if __name__ == "__main__":
    main()