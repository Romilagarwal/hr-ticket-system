import requests
import json
from requests.auth import HTTPBasicAuth

# API endpoint with filter for specific user ID
url = "https://api44.sapsf.com/odata/v2/EmpJob?$select=division,divisionNav/name,location,locationNav/name,seqNumber,startDate,userId,employmentNav/personNav/personalInfoNav/firstName,employmentNav/personNav/personalInfoNav/middleName,employmentNav/personNav/personalInfoNav/lastName,employmentNav/personNav/personalInfoNav/customString5,payGradeNav/name,customString10Nav/externalName,department,departmentNav/name,employmentNav/empJobRelationshipNav/relationshipTypeNav/externalCode,employmentNav/empJobRelationshipNav/relUserId,employmentNav/empJobRelationshipNav/relUserNav/defaultFullName,employmentNav/personNav/emailNav/emailAddress,employmentNav/personNav/emailNav/isPrimary,employmentNav/personNav/emailNav/emailTypeNav/picklistLabels/label,employmentNav/personNav/countryOfBirth,employmentNav/personNav/phoneNav/phoneNumber,employmentNav/personNav/phoneNav/phoneTypeNav/picklistLabels/label,employmentNav/personNav/personalInfoNav/gender,employmentNav/personNav/personalInfoNav/maritalStatusNav/picklistLabels/label,employmentNav/personNav/dateOfBirth,employmentNav/startDate,employmentNav/customString18,emplStatusNav/picklistLabels/label,employmentNav/personNav/homeAddressNavDEFLT/addressType,employmentNav/personNav/homeAddressNavDEFLT/address1,employmentNav/personNav/homeAddressNavDEFLT/address10,employmentNav/personNav/homeAddressNavDEFLT/address12,employmentNav/personNav/homeAddressNavDEFLT/address14,employmentNav/personNav/homeAddressNavDEFLT/stateNav/picklistLabels/label,employmentNav/personNav/homeAddressNavDEFLT/countyNav/picklistLabels/label,employmentNav/personNav/homeAddressNavDEFLT/cityNav/picklistLabels/label,managerId,managerUserNav/defaultFullName,employmentNav/personNav/personalInfoNav/customString10,employmentNav/personNav/personalInfoNav/customString11,employmentNav/personNav/personalInfoNav/customString8,employmentNav/personNav/personalInfoNav/customString9,customString6,employmentType,customString6Nav/id,customString6Nav/externalCode,customString6Nav/localeLabel,employmentTypeNav/id,employmentTypeNav/externalCode,employmentTypeNav/localeLabel,employmentNav/endDate,employmentNav/customDate6,eventReasonNav/externalCode,eventReasonNav/name&$expand=employmentNav/personNav/personalInfoNav,divisionNav,locationNav,payGradeNav,customString10Nav,departmentNav,employmentNav/empJobRelationshipNav/relationshipTypeNav,employmentNav/empJobRelationshipNav/relUserNav,employmentNav/personNav/emailNav/emailTypeNav/picklistLabels,employmentNav/personNav/phoneNav/phoneTypeNav/picklistLabels,employmentNav/personNav/personalInfoNav/maritalStatusNav/picklistLabels,emplStatusNav/picklistLabels,employmentNav/personNav/homeAddressNavDEFLT/stateNav/picklistLabels,employmentNav/personNav/homeAddressNavDEFLT/countyNav/picklistLabels,employmentNav/personNav/homeAddressNavDEFLT/cityNav/picklistLabels,managerUserNav,customString6Nav,employmentTypeNav,eventReasonNav&$expand=employmentNav/personNav/personalInfoNav,divisionNav,locationNav,payGradeNav,customString10Nav,departmentNav,employmentNav/empJobRelationshipNav/relationshipTypeNav,employmentNav/empJobRelationshipNav/relUserNav,employmentNav/personNav/emailNav/emailTypeNav/picklistLabels,employmentNav/personNav/phoneNav/phoneTypeNav/picklistLabels,employmentNav/personNav/personalInfoNav/maritalStatusNav/picklistLabels,emplStatusNav/picklistLabels,employmentNav/personNav/homeAddressNavDEFLT/stateNav/picklistLabels,employmentNav/personNav/homeAddressNavDEFLT/countyNav/picklistLabels,employmentNav/personNav/homeAddressNavDEFLT/cityNav/picklistLabels,managerUserNav,customString6Nav,employmentTypeNav,eventReasonNav&$filter=userId eq '9025676'&$format=json&$orderby=employmentNav/startDate"

# Authentication credentials (replace with actual username and password)
username = "api_user@navitasysi"
password = "api@1234"

def safe_get(data, *keys):
    """Safely navigate nested dictionaries and lists"""
    for key in keys:
        if isinstance(data, dict):
            data = data.get(key)
        elif isinstance(data, list) and isinstance(key, int) and len(data) > key:
            data = data[key]
        else:
            return None
        if data is None:
            return None
    return data

def print_employee_details(result):
    """Print detailed information for an employee"""
    user_id = result.get('userId', 'N/A')
    
    # Get basic employee info
    personal_info = safe_get(result, 'employmentNav', 'personNav', 'personalInfoNav', 'results', 0)
    first_name = safe_get(personal_info, 'firstName') or 'N/A'
    middle_name = safe_get(personal_info, 'middleName') or 'N/A'
    last_name = safe_get(personal_info, 'lastName') or 'N/A'
    
    # Get employment details
    start_date = result.get('startDate', 'N/A')
    division = safe_get(result, 'divisionNav', 'name') or result.get('division', 'N/A')
    location = safe_get(result, 'locationNav', 'name') or result.get('location', 'N/A')
    department = safe_get(result, 'departmentNav', 'name') or result.get('department', 'N/A')
    
    # Get manager info
    manager_id = result.get('managerId', 'N/A')
    manager_name = safe_get(result, 'managerUserNav', 'defaultFullName') or 'N/A'
    
    # Get relUserId from job relationship
    rel_user_id = safe_get(result, 'employmentNav', 'empJobRelationshipNav', 'relUserId')
    if rel_user_id is None:
        rel_user_id = 'N/A'
    
    # Get phone number - it might be in a results array
    phone_nav = safe_get(result, 'employmentNav', 'personNav', 'phoneNav')
    phone_number = 'N/A'
    
    if phone_nav:
        if isinstance(phone_nav, dict) and 'results' in phone_nav:
            # Phone data is in results array
            phone_results = phone_nav['results']
            if phone_results and len(phone_results) > 0:
                phone_number = phone_results[0].get('phoneNumber', 'N/A')
        elif isinstance(phone_nav, dict) and 'phoneNumber' in phone_nav:
            # Phone data is directly in phoneNav
            phone_number = phone_nav['phoneNumber']
        elif isinstance(phone_nav, list) and len(phone_nav) > 0:
            # Phone data is a list
            phone_number = phone_nav[0].get('phoneNumber', 'N/A')
    
    # Get email
    email_nav = safe_get(result, 'employmentNav', 'personNav', 'emailNav')
    email = 'N/A'
    if email_nav:
        if isinstance(email_nav, dict) and 'results' in email_nav:
            email_results = email_nav['results']
            if email_results and len(email_results) > 0:
                email = email_results[0].get('emailAddress', 'N/A')
        elif isinstance(email_nav, dict) and 'emailAddress' in email_nav:
            email = email_nav['emailAddress']
        elif isinstance(email_nav, list) and len(email_nav) > 0:
            email = email_nav[0].get('emailAddress', 'N/A')
    
    print(f"Employee Details for User ID: {user_id}")
    print("="*50)
    print(f"Name: {first_name} {middle_name} {last_name}")
    print(f"Start Date: {start_date}")
    print(f"Division: {division}")
    print(f"Location: {location}")
    print(f"Department: {department}")
    print(f"Manager ID: {manager_id}")
    print(f"Manager Name: {manager_name}")
    print(f"Related User ID: {rel_user_id}")
    print(f"Phone Number: {phone_number}")
    print(f"Email: {email}")
    print("-" * 50)

# Make the API request
try:
    response = requests.get(url, auth=HTTPBasicAuth(username, password))
    
    # Check if the request was successful
    if response.status_code == 200:
        # Parse JSON response
        data = response.json()
        
        # Save the response to a file (optional)
        with open('sap_response_9025676.json', 'w') as f:
            json.dump(data, f, indent=4)
        
        print("API Response received successfully!")
        print("="*50)
        
        # Check if any results were returned
        results = data.get('d', {}).get('results', [])
        
        if results:
            print(f"Found {len(results)} record(s) for User ID 9025676")
            print()
            
            # Process each result (there might be multiple job records for the same user)
            for i, result in enumerate(results, 1):
                print(f"Record {i}:")
                print_employee_details(result)
                print()
        else:
            print("No records found for User ID 9025676")
            
    else:
        print(f"Error: Received status code {response.status_code}")
        print(response.text)
        
except requests.exceptions.RequestException as e:
    print(f"Request failed: {e}")
except KeyError as e:
    print(f"Key error - field not found: {e}")
except Exception as e:
    print(f"Unexpected error: {e}")

# Alternative approach: If the API filter doesn't work, you can filter after receiving data
def filter_by_user_id_locally():
    """Alternative method to filter results locally after API call"""
    # Use the original URL without filter
    original_url = "https://api44.sapsf.com/odata/v2/EmpJob?$select=division,divisionNav/name,location,locationNav/name,seqNumber,startDate,userId,employmentNav/personNav/personalInfoNav/firstName,employmentNav/personNav/personalInfoNav/middleName,employmentNav/personNav/personalInfoNav/lastName,employmentNav/personNav/personalInfoNav/customString5,payGradeNav/name,customString10Nav/externalName,department,departmentNav/name,employmentNav/empJobRelationshipNav/relationshipTypeNav/externalCode,employmentNav/empJobRelationshipNav/relUserId,employmentNav/empJobRelationshipNav/relUserNav/defaultFullName,employmentNav/personNav/emailNav/emailAddress,employmentNav/personNav/emailNav/isPrimary,employmentNav/personNav/emailNav/emailTypeNav/picklistLabels/label,employmentNav/personNav/countryOfBirth,employmentNav/personNav/phoneNav/phoneNumber,employmentNav/personNav/phoneNav/phoneTypeNav/picklistLabels/label,employmentNav/personNav/personalInfoNav/gender,employmentNav/personNav/personalInfoNav/maritalStatusNav/picklistLabels/label,employmentNav/personNav/dateOfBirth,employmentNav/startDate,employmentNav/customString18,emplStatusNav/picklistLabels/label,employmentNav/personNav/homeAddressNavDEFLT/addressType,employmentNav/personNav/homeAddressNavDEFLT/address1,employmentNav/personNav/homeAddressNavDEFLT/address10,employmentNav/personNav/homeAddressNavDEFLT/address12,employmentNav/personNav/homeAddressNavDEFLT/address14,employmentNav/personNav/homeAddressNavDEFLT/stateNav/picklistLabels/label,employmentNav/personNav/homeAddressNavDEFLT/countyNav/picklistLabels/label,employmentNav/personNav/homeAddressNavDEFLT/cityNav/picklistLabels/label,managerId,managerUserNav/defaultFullName,employmentNav/personNav/personalInfoNav/customString10,employmentNav/personNav/personalInfoNav/customString11,employmentNav/personNav/personalInfoNav/customString8,employmentNav/personNav/personalInfoNav/customString9,customString6,employmentType,customString6Nav/id,customString6Nav/externalCode,customString6Nav/localeLabel,employmentTypeNav/id,employmentTypeNav/externalCode,employmentTypeNav/localeLabel,employmentNav/endDate,employmentNav/customDate6,eventReasonNav/externalCode,eventReasonNav/name&$expand=employmentNav/personNav/personalInfoNav,divisionNav,locationNav,payGradeNav,customString10Nav,departmentNav,employmentNav/empJobRelationshipNav/relationshipTypeNav,employmentNav/empJobRelationshipNav/relUserNav,employmentNav/personNav/emailNav/emailTypeNav/picklistLabels,employmentNav/personNav/phoneNav/phoneTypeNav/picklistLabels,employmentNav/personNav/personalInfoNav/maritalStatusNav/picklistLabels,emplStatusNav/picklistLabels,employmentNav/personNav/homeAddressNavDEFLT/stateNav/picklistLabels,employmentNav/personNav/homeAddressNavDEFLT/countyNav/picklistLabels,employmentNav/personNav/homeAddressNavDEFLT/cityNav/picklistLabels,managerUserNav,customString6Nav,employmentTypeNav,eventReasonNav&$format=json&$orderby=employmentNav/startDate"
    
    try:
        response = requests.get(original_url, auth=HTTPBasicAuth(username, password))
        
        if response.status_code == 200:
            data = response.json()
            
            # Filter results locally
            filtered_results = []
            for result in data.get('d', {}).get('results', []):
                if result.get('userId') == '9025676':
                    filtered_results.append(result)
            
            print(f"\nLocal Filter Results: Found {len(filtered_results)} record(s) for User ID 9025676")
            
            for i, result in enumerate(filtered_results, 1):
                print(f"\nRecord {i}:")
                print_employee_details(result)
                
        else:
            print(f"Error in local filter: {response.status_code}")
            
    except Exception as e:
        print(f"Error in local filter: {e}")

# Uncomment the line below if the API filter doesn't work and you want to try local filtering
# filter_by_user_id_locally()
