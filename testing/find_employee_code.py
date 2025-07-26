import os
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

# Load environment variables from your .env file
load_dotenv()

# --- Configuration: Fetched from your existing code ---
SAP_USERNAME = os.environ.get('SAP_API_USERNAME')
SAP_PASSWORD = os.environ.get('SAP_API_PASSWORD')

# List of emails to search for
emails_to_find = [
    "sunil.kumar@nvtpower.com",
    "Dipanshu.Dhiman@nvtpower.com",
    "Priyanka.Mehta@nvtpower.com",
    "yashica.garg@nvtpower.com",
    "Saveen.Bhutani@nvtpower.com",
    "taru.kaushik@nvtpower.com",
    "pawan.tyagi@nvtpower.com",
    "jayesh.sinha@nvtpower.com"
]

def find_employee_code_by_email(email):
    """
    Searches for an employee's user ID (employee code) using their email address.
    This function is adapted from your get_employee_sap route.
    """
    if not SAP_USERNAME or not SAP_PASSWORD:
        return "Error: SAP credentials not found in .env file."

    # This URL is modified to filter by email address instead of userId
    # The path 'employmentNav/personNav/emailNav/emailAddress' is derived from your API call's expand/select parameters
    url = (
        f"https://api44.sapsf.com/odata/v2/EmpJob?"
        f"$select=userId"
        f"&$expand=employmentNav/personNav/emailNav"
        f"&$filter=employmentNav/personNav/emailNav/emailAddress eq '{email}'"
        f"&$format=json"
    )

    try:
        response = requests.get(
            url,
            auth=HTTPBasicAuth(SAP_USERNAME, SAP_PASSWORD),
            timeout=10  # Increased timeout for potentially slower queries
        )

        if response.status_code == 200:
            data = response.json()
            results = data.get('d', {}).get('results', [])
            if results:
                # The employee code is in the 'userId' field
                return results[0].get('userId', 'User ID not found in response')
            else:
                return "Not Found"
        else:
            return f"Error: API returned status {response.status_code}"

    except requests.exceptions.RequestException as e:
        return f"Error: Network request failed - {e}"
    except Exception as e:
        return f"An unexpected error occurred: {e}"

if __name__ == "__main__":
    print("--- Searching for Employee Codes via SAP API ---")
    for email in emails_to_find:
        print(f"Searching for: {email}")
        employee_code = find_employee_code_by_email(email)
        print(f"  -> Employee Code: {employee_code}\n")
    print("--- Search Complete ---")