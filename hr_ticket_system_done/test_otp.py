import os
import requests
from dotenv import load_dotenv

load_dotenv()

def send_otp_template(to_phone, otp_code):
    """
    Send OTP template message using Meta's Cloud API v22.0
    """
    phone_number_id = os.environ.get('WHATSAPP_PHONE_NUMBER_ID',"639456475927290")
    access_token = os.environ.get('META_ACCESS_TOKEN',"EAARnp44Shx4BPKuiHPgLAR4KwJsu0HW87JcqZC6hhnsdixldmVX0pgOpocK8bibP5ZCL7dzEFfnbo2AJoAvnImsbOlcxEZCxbHshp0zHxRZCKSEc38ldPr0APrYk3g25apdZCWt3IYX0StufP4TjePj0QLlvmGCfV4op6hEq3d3RhZB3IAW4fgpta3AXEtytjJMGH47z7xYh1fOscVQ2ZBDZAKYo0OXG1v6sq5ZAZB1EkQ")
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
            "name": "otp_login_verification",
            "language": {"code": "en"},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": str(otp_code)}
                    ]
                }
            ]
        }
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.text}")
        
        if response.status_code == 200:
            response_data = response.json()
            message_id = response_data.get('messages', [{}])[0].get('id', 'N/A')
            print(f"✅ OTP sent successfully!")
            print(f"Message ID: {message_id}")
            return True
        else:
            print(f"❌ Failed to send OTP")
            return False
            
    except Exception as e:
        print(f"Error sending OTP: {e}")
        return False

def test_otp_template():
    """Test the OTP template with different scenarios"""
    test_phone = "+918899822551"
    
    # Test cases
    test_cases = [
        "123456",      # 6-digit OTP
        "1234",        # 4-digit OTP
        "789012",      # Different 6-digit OTP
        "ABC123",      # Alphanumeric OTP
    ]
    
    print("🔐 Testing OTP Template")
    print("=" * 40)
    
    for i, otp in enumerate(test_cases, 1):
        print(f"\n📱 Test {i}: Sending OTP '{otp}'")
        print("-" * 30)
        
        success = send_otp_template(test_phone, otp)
        
        if success:
            print(f"✅ Test {i} passed")
        else:
            print(f"❌ Test {i} failed")
        
        # Small delay between tests
        import time
        time.sleep(2)

if __name__ == "__main__":
    test_otp_template()