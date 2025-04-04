import os, threading
from azure.communication.sms import SmsClient, SmsSendResult
from app.unified_logger import get_logger

logger = get_logger()
ACS_SMS_CONNECTION_STRING = os.getenv("ACS_CONNECTION_STRING")

_sms_client = None
_lock = threading.Lock()

# singleton instance to ensure only one instance of SmsClient is created
def get_sms_client():
    global _sms_client
    if _sms_client is None:
        with _lock:
            if _sms_client is None:
                _sms_client = SmsClient.from_connection_string(ACS_SMS_CONNECTION_STRING)
    return _sms_client

async def send_sms(messages: list, customer_number: str) -> None:
    try:
        # check if customer_number is a valid united states phone number
        if not customer_number.startswith("+1"):
            logger.error("Invalid phone number. Must start with +1.")
        sms_client = get_sms_client()

        sms_response_list: list[SmsSendResult] = []
        for message in messages:
            sms_response: SmsSendResult = sms_client.send(
                from_=os.getenv("ACS_SMS_FROM_PHONE_NUMBER"),
                to=customer_number,
                message=message,
                enable_delivery_report=True,
            )
            sms_response_list.extend(sms_response)

        for sms_response in sms_response_list:
            if sms_response.successful:
                logger.info(f"SMS sent: {sms_response.message_id}")
            else:
                logger.error(f"Failed to send SMS: {sms_response.error_message}")

    except Exception as e:
        logger.error(f"Failed to send SMS: {e}")