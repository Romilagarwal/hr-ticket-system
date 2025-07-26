import os
import requests
from hr_ticket import send_whatsapp_template

# Set environment variables (replace with your actual values or set in shell)
os.environ['META_ACCESS_TOKEN'] = "EAARnp44Shx4BPKuiHPgLAR4KwJsu0HW87JcqZC6hhnsdixldmVX0pgOpocK8bibP5ZCL7dzEFfnbo2AJoAvnImsbOlcxEZCxbHshp0zHxRZCKSEc38ldPr0APrYk3g25apdZCWt3IYX0StufP4TjePj0QLlvmGCfV4op6hEq3d3RhZB3IAW4fgpta3AXEtytjJMGH47z7xYh1fOscVQ2ZBDZAKYo0OXG1v6sq5ZAZB1EkQ"
os.environ['WHATSAPP_PHONE_NUMBER_ID'] = "639456475927290"

def test_real_api_call():
    try:
        # Send a simple template message
        result = send_whatsapp_template(
            to_phone="+918318436133",
            template_name="otp_login_verification",
            lang_code="en",
            parameters=["123456"]
        )
        # Make a direct API call to capture detailed response
        url = f"https://graph.facebook.com/v22.0/{os.environ['WHATSAPP_PHONE_NUMBER_ID']}/messages"
        headers = {
            "Authorization": f"Bearer {os.environ['META_ACCESS_TOKEN']}",
            "Content-Type": "application/json"
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": "+918318436133",
            "type": "template",
            "template": {
                "name": "otp_login_verification",
                "language": {"code": "en"},
                "components": [
                    {"type": "body", "parameters": [{"type": "text", "text": "123456"}]}
                ]
            }
        }
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        
        # Print detailed response
        print(f"API Response Status: {response.status_code}")
        print(f"API Response Text: {response.text}")
        print(f"send_whatsapp_template Result: {result}")
        
        # Check if message was sent successfully
        if response.status_code == 200 and result:
            print("Message sent successfully. Check WhatsApp on +918318436133.")
        else:
            print("Message failed to send. See response details above.")
            
    except Exception as e:
        print(f"Error during API call: {str(e)}")

if __name__ == "__main__":
    test_real_api_call()