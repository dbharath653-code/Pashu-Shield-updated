"""Telephony provider registry / factory."""
from ..config import settings
from .base import MarkupBuilder, ProviderNotConfigured, TelephonyProvider
from .custom_provider import CustomProvider
from .exotel_provider import ExotelProvider
from .plivo_provider import PlivoProvider
from .twilio_provider import TwilioProvider

PROVIDERS = {
    "twilio": TwilioProvider,
    "exotel": ExotelProvider,
    "plivo": PlivoProvider,
    "custom": CustomProvider,
}

_cache = {}


class UnconfiguredProvider(TelephonyProvider):
    """Returned when IVR_PROVIDER is unset.

    Refuses to render any call control markup and reports exactly what is
    missing - no simulated telephony.
    """

    name = "none"

    def configuration_errors(self):
        return [
            "IVR_PROVIDER is not set (choose twilio | exotel | plivo | custom). "
            "Until a licensed telephony provider is configured the IVR webhook "
            "cannot answer real PSTN calls."
        ]

    def render(self, builder):
        raise ProviderNotConfigured("; ".join(self.configuration_errors()))


def get_provider(name=None, refresh=False):
    key = (name or settings.PROVIDER or "none").lower()
    if not refresh and key in _cache:
        return _cache[key]
    cls = PROVIDERS.get(key)
    inst = cls() if cls else UnconfiguredProvider()
    _cache[key] = inst
    return inst


def provider_status(name=None):
    """Health detail used by /api/ivr/health and the admin UI."""
    provider = get_provider(name)
    errs = provider.configuration_errors()
    return {
        "provider": provider.name,
        "configured": not errs,
        "markup_flavor": getattr(provider.serializer, "flavor", None),
        "errors": errs,
        "ivr_phone_number": settings.IVR_PHONE_NUMBER or settings.TELEPHONY_PHONE_NUMBER or "",
        "webhook_url": provider.webhook_url("/ivr/voice"),
        "status_webhook_url": provider.webhook_url("/ivr/status"),
        "recording_webhook_url": provider.webhook_url("/ivr/recording"),
    }


__all__ = [
    "TelephonyProvider", "MarkupBuilder", "ProviderNotConfigured", "UnconfiguredProvider",
    "TwilioProvider", "ExotelProvider", "PlivoProvider", "CustomProvider",
    "get_provider", "provider_status", "PROVIDERS",
]
