import os
import requests
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

def send_whatsapp_template(to_phone, template_name, lang_code, parameters):
    """
    Send a WhatsApp template message using Meta's Cloud API v22.0 for testing.
    :param to_phone: Recipient phone number in international format, e.g. '919999999999'
    :param template_name: Name of the approved template, e.g. 'grievance_resolution_confirmation'
    :param lang_code: Language code, e.g. 'en'
    :param parameters: List of text values for the template placeholders (in order)
    :return: True if sent, False otherwise
    """
    phone_number_id = os.environ.get('WHATSAPP_PHONE_NUMBER_ID')
    access_token = os.environ.get('META_ACCESS_TOKEN')
    url = f"https://graph.facebook.com/v22.0/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": lang_code}
        }
    }
    
    components = []
    if parameters:
        if template_name == "grievance_resolution_confirmation" and len(parameters) >= 4:
            components.append({
                "type": "body",
                "parameters": [
                    {"type": "text", "text": str(parameters[0])},  # Name
                    {"type": "text", "text": str(parameters[1])},  # Reference ID
                    {"type": "text", "text": str(parameters[2])},  # Subject
                    {"type": "text", "text": str(parameters[3])}   # Resolution Date
                ]
            })
        else:
            components.append({"type": "body", "parameters": [{"type": "text", "text": str(val)} for val in parameters]})
    
    if components:
        payload["template"]["components"] = components
    
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        print(f"WhatsApp API response for {template_name}: {resp.status_code} {resp.text}")
        return resp.status_code == 200
    except Exception as e:
        print(f"WhatsApp API error for {template_name}: {e}")
        return False

def test_grievance_resolution_template():
    """Test the Grievance Resolution Confirmation template with appropriate parameters."""
    to_phone = "+918318436133"  # Use a verified test number
    current_date = datetime.now().strftime("%d-%m-%Y")
    current_time = datetime.now().strftime("%H:%M")
    resolution_date = f"{current_date}, {current_time}"

    templates = {
        "grievance_resolution_confirmation": [
            "John Doe",           # Name
            "12345",              # Reference ID
            "Leave Issue",        # Subject
            resolution_date       # Resolution Date
        ]
    }
    
    print("🔐 Testing Grievance Resolution Confirmation Template")
    print("=" * 40)
    
    for template_name, parameters in templates.items():
        print(f"\n📱 Testing template: {template_name}")
        print("-" * 30)
        
        success = send_whatsapp_template(to_phone, template_name, "en", parameters)
        
        if success:
            print(f"✅ Test passed for {template_name}")
        else:
            print(f"❌ Test failed for {template_name}")
        
        # Small delay between tests
        import time
        time.sleep(2)

if __name__ == "__main__":
    test_grievance_resolution_template()