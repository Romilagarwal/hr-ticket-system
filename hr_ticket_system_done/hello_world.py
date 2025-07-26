import os
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from hr_ticket import send_whatsapp_template
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Set environment variables (replace with your actual values or set in shell)
os.environ['META_ACCESS_TOKEN'] = "EAARnp44Shx4BPKuiHPgLAR4KwJsu0HW87JcqZC6hhnsdixldmVX0pgOpocK8bibP5ZCL7dzEFfnbo2AJoAvnImsbOlcxEZCxbHshp0zHxRZCKSEc38ldPr0APrYk3g25apdZCWt3IYX0StufP4TjePj0QLlvmGCfV4op6hEq3d3RhZB3IAW4fgpta3AXEtytjJMGH47z7xYh1fOscVQ2ZBDZAKYo0OXG1v6sq5ZAZB1EkQ"
os.environ['WHATSAPP_PHONE_NUMBER_ID'] = "639456475927290"

def create_session_with_retries():
    """Create a requests session with retry logic for connection errors."""
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["POST"])
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session

def test_real_api_call():
    """Test sending a real WhatsApp message with the hello_world_private template."""
    to_phone = "+918899822551"
    template_name = "hello_world_private"
    lang_code = "en"
    
    try:
        # Create a session with retry logic
        session = create_session_with_retries()
        
        # Log attempt
        logger.info(f"Attempting to send message to {to_phone} with template {template_name}")
        
        # Call send_whatsapp_template
        result = send_whatsapp_template(
            to_phone=to_phone,
            template_name=template_name,
            lang_code=lang_code,
            parameters=[]  # No parameters for this template
        )
        
        # Make a direct API call with minimal payload
        url = f"https://graph.facebook.com/v22.0/{os.environ['WHATSAPP_PHONE_NUMBER_ID']}/messages"
        headers = {
            "Authorization": f"Bearer {os.environ['META_ACCESS_TOKEN']}",
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
        
        logger.info(f"Sending API request to {url} with payload: {payload}")
        response = session.post(url, headers=headers, json=payload, timeout=15)
        
        # Log response
        logger.info(f"API Response Status: {response.status_code}")
        logger.info(f"API Response Text: {response.text}")
        logger.info(f"send_whatsapp_template Result: {result}")
        
        # Check success
        if response.status_code == 200 and result:
            logger.info(f"Message sent successfully to {to_phone}. Check WhatsApp.")
        else:
            logger.error(f"Message failed to send. Status: {response.status_code}, Response: {response.text}")
            
        return response.status_code == 200
    
    except requests.exceptions.ConnectionError as e:
        logger.error(f"Connection error: {str(e)}. Retrying may help. Check network or firewall settings.")
        return False
    except requests.exceptions.Timeout:
        logger.error("Request timed out. Check network or increase timeout.")
        return False
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return False

if __name__ == "__main__":
    test_real_api_call()