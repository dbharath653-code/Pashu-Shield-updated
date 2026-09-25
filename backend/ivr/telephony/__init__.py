"""
Factory for telephony provider.
"""
import os

def get_telephony_provider():
    from ..config import TELEPHONY_PROVIDER, TELEPHONY_ACCOUNT_ID, TELEPHONY_AUTH_TOKEN, TELEPHONY_PHONE_NUMBER, TELEPHONY_WEBHOOK_SECRET
    provider_name = (TELEPHONY_PROVIDER or "mock").lower()
    if provider_name == "twilio":
        from .twilio_provider import TwilioProvider
        return TwilioProvider(TELEPHONY_ACCOUNT_ID, TELEPHONY_AUTH_TOKEN, TELEPHONY_PHONE_NUMBER)
    elif provider_name == "exotel":
        from .exotel_provider import ExotelProvider
        return ExotelProvider(TELEPHONY_ACCOUNT_ID, TELEPHONY_AUTH_TOKEN, TELEPHONY_PHONE_NUMBER)
    elif provider_name == "plivo":
        # Plivo uses similar pattern to Twilio, reuse Twilio for now with note
        from .twilio_provider import TwilioProvider
        return TwilioProvider(TELEPHONY_ACCOUNT_ID, TELEPHONY_AUTH_TOKEN, TELEPHONY_PHONE_NUMBER)
    elif provider_name == "sip":
        # Self-hosted Asterisk PBX voice gateway (pbx/). No SaaS credentials.
        from .sip_provider import SIPProvider
        return SIPProvider()
    else:
        from .mock_provider import MockTelephonyProvider
        return MockTelephonyProvider(TELEPHONY_WEBHOOK_SECRET)
