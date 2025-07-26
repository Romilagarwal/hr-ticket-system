import requests
import json
from requests.auth import HTTPBasicAuth

# --- Configuration ---
# Your API URL and credentials
url = "https://api44.sapsf.com/odata/v2/EmpJob?$select=employmentNav/personNav/emailNav/emailAddress&$expand=employmentNav/personNav/emailNav&$filter=userId eq '9025676'&$format=json"

# IMPORTANT: Replace with your actual username and password
username = "api_user@navitasysi"
password = "api@1234"

# --- Main Execution ---
print("🚀 Searching for the work email column...")
try:
    response = requests.get(url, auth=HTTPBasicAuth(username, password))

    if response.status_code == 200:
        data = response.json()
        results = data.get('d', {}).get('results', [])

        if results:
            # We only need to check the first record for this employee
            record = results[0]

            # Navigate to the list of email objects
            email_list = record.get('employmentNav', {}).get('personNav', {}).get('emailNav', {}).get('results', [])

            if not email_list:
                print("❌ No email information was found for this employee.")
            else:
                found_match = False
                # Iterate through the list of emails with their index
                for index, email_item in enumerate(email_list):
                    email_address = email_item.get('emailAddress')

                    # Check if the email address contains the work domain
                    if email_address and "@nvtpower.com" in email_address.lower():

                        # Dynamically construct the column name using the list index
                        dynamic_column_name = f"employmentNav_personNav_emailNav_{index}_emailAddress"

                        print("\n" + "="*50)
                        print("✅ Found it! The work email column name is:")
                        print(f"\n➡️   {dynamic_column_name}")
                        print("\n" + "="*50)

                        print(f"\nThis is because the work email '{email_address}' was found at position {index} in the employee's email list from the API.")
                        found_match = True
                        break # Exit the loop once we find it

                if not found_match:
                    print("Could not find any email address ending in '@nvtpower.com'.")

        else:
            print("⚠️ No records found for User ID 9025676.")

    else:
        print(f"❌ API Error: Received status code {response.status_code}")
        print(response.text)

except Exception as e:
    print(f"❌ An unexpected error occurred: {e}")
