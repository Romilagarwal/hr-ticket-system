import os
import requests
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

def send_whatsapp_template(to_phone, template_name, lang_code, parameters):
    """
    Send a WhatsApp template message using Meta's Cloud API v22.0 for testing.
    :param to_phone: Recipient phone number in international format, e.g. '919999999999'
    :param template_name: Name of the approved template, e.g. 'otp_login_verification'
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
    if template_name in ["grievance_submission_confirmation", "new_grievance_notification_hr",
                        "grievance_resolution_confirmation", "grievance_reopened_hr", "grievance_reopened_employee",
                        "grievance_pending_reminder", "grievance_reassigned_hr", "grievance_updated_employee",
                        "grievance_updated_hr", "grievance_deleted_notification"]:
        image_url = "https://i.ibb.co/xKYMdfg96/ask-hr-logo.png"
        components.append({"type": "header", "parameters": [{"type": "image", "image": {"link": image_url}}]})
    
    if parameters:
        if template_name == "otp_login_verification" and len(parameters) >= 1:
            components.append({
                "type": "body",
                "parameters": [
                    {"type": "text", "text": str(parameters[0])},  # OTP variable
                    {"type": "text", "text": "For your security, do not share this code."},
                    {"type": "text", "text": "This code expires in 5 minutes."}
                ]
            })
            components.append({"type": "button", "sub_type": "copy_code", "index": 0, "parameters": [{"type": "text", "text": str(parameters[0])}]})
        elif template_name != "hello_world_private":
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

def test_all_templates():
    """Test all WhatsApp templates with appropriate parameters."""
    to_phone = "+918899822551"  # Use a verified test number
    current_time = datetime.now().strftime("%d-%m-%Y, %H:%M")
    templates = {
        "hello_world_private": [],  # No parameters
        "grievance_submission_confirmation": ["John Doe", "12345", "Leave Issue", f"17-07-2025, {current_time}"],
        "new_grievance_notification_hr": ["John Doe", "HR Manager", "12345", "Leave Issue", f"17-07-2025, {current_time}"],
        "grievance_resolution_confirmation": ["John Doe", "12345", "Leave Issue", f"17-07-2025, {current_time}"],
        "grievance_reopened_hr": ["HR Manager", "12345", "John Doe", "Leave Issue"],
        "grievance_reopened_employee": ["John Doe", "12345", "Leave Issue"],
        "grievance_pending_reminder": [2, "12345", "John Doe", "Leave Issue", f"17-07-2025, {current_time}"],
        "grievance_reassigned_hr": ["HR Manager", "12345", "Leave Issue", f"17-07-2025, {current_time}"],
        "grievance_updated_employee": ["John Doe", "12345", "Leave Issue", "Grievance Types"],
        "grievance_updated_hr": ["John Doe", "HR Manager", "12345", "Leave Issue", "John Doe"],
        "otp_login_verification": ["123456"],  # OTP with static security and expiry text
        "grievance_deleted_notification": ["John Doe", "12345", "Leave Issue", "Admin Deleted"]
    }
    
    for template_name, parameters in templates.items():
        print(f"Testing template: {template_name}")
        success = send_whatsapp_template(to_phone, template_name, "en", parameters)
        if success:
            print(f"Successfully tested {template_name}")
        else:
            print(f"Failed to test {template_name}")

if __name__ == "__main__":
    test_all_templates()