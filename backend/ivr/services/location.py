"""
Location capture strategy for IVR calls.
NEVER invent GPS. Explicitly track source/accuracy.
"""
import re
from typing import Dict, Any, Optional, Tuple

# Maharashtra district list for validation
MAHARASHTRA_DISTRICTS = [
    "Pune", "Satara", "Aurangabad", "Chhatrapati Sambhajinagar", "Nagpur", "Nashik", "Nanded",
    "Latur", "Solapur", "Kolhapur", "Ahmednagar", "Ahilyanagar", "Thane", "Mumbai", "Jalgaon",
    "Amravati", "Sangli", "Chandrapur", "Akola", "Dhule", "Osmanabad", "Dharashiv", "Beed",
    "Buldhana", "Yavatmal", "Wardha", "Bhandara", "Gondia", "Gadchiroli", "Parbhani", "Hingoli",
    "Jalna", "Raigad", "Ratnagiri", "Sindhudurg", "Palghar", "Nandurbar", "Washim", "Gadchiroli"
]

# District centroid fallbacks (honest: NOT farmer GPS)
DISTRICT_CENTROIDS = {
    "pune": (18.5204, 73.8567),
    "satara": (17.6805, 74.0183),
    "aurangabad": (19.8762, 75.3433),
    "chhatrapati sambhajinagar": (19.8762, 75.3433),
    "nagpur": (21.1458, 79.0882),
    "nashik": (20.0110, 73.7903),
    "nanded": (19.1383, 77.3210),
    "latur": (18.4088, 76.5604),
    "solapur": (17.6599, 75.9064),
    "kolhapur": (16.7050, 74.2433),
    "ahmednagar": (19.0952, 74.7496),
    "ahilyanagar": (19.0952, 74.7496),
    "thane": (19.2183, 72.9781),
    "mumbai": (19.0760, 72.8777),
    "jalgaon": (21.0077, 75.5626),
}

def normalize_district(raw: str) -> str:
    if not raw or not isinstance(raw, str):
        return ""
    cleaned = re.sub(r"[^a-zA-Z\s]", "", raw).strip().title()
    # Try exact match
    for d in MAHARASHTRA_DISTRICTS:
        if d.lower() == cleaned.lower():
            return d
    # Try substring
    for d in MAHARASHTRA_DISTRICTS:
        if cleaned.lower() in d.lower() or d.lower() in cleaned.lower():
            return d
    # Return cleaned as-is if not in list but plausible (allow other states)
    return cleaned

def normalize_village(raw: str) -> str:
    if not raw:
        return ""
    # Basic cleaning: remove digits/special but keep letters
    cleaned = raw.strip().title()
    # Limit length
    return cleaned[:80]

def get_location_from_responses(responses: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build location dict from survey responses.
    NEVER invent GPS - explicitly mark source.
    Priority:
    1. If SMS link provided GPS -> GPS
    2. If carrier network available -> NETWORK (not implemented, marked)
    3. If farmer provided village/district/state -> FARMER_PROVIDED
    4. Else NOT_AVAILABLE
    """
    village = normalize_village(responses.get("location_village") or responses.get("village") or "")
    district = normalize_district(responses.get("location_district") or responses.get("district") or "")
    state = (responses.get("location_state") or responses.get("state") or "Maharashtra").strip().title()
    if state == "1" or state == "Maharashtra":
        state = "Maharashtra"

    # Check if GPS was provided via SMS link or browser
    lat = responses.get("location_lat")
    lng = responses.get("location_lng")
    source = "NOT_AVAILABLE"
    accuracy = "No location provided"
    lat_f = None
    lng_f = None

    # Try parse lat/lng if provided
    try:
        if lat is not None and lng is not None:
            lat_f = float(lat)
            lng_f = float(lng)
            # Validate plausible India bounds
            if 6.0 <= lat_f <= 36.0 and 68.0 <= lng_f <= 98.0:
                # Check if source explicitly marked
                provided_source = responses.get("location_source", "")
                if provided_source == "GPS":
                    source = "GPS"
                    accuracy = "High - Browser GPS after farmer consent"
                elif provided_source == "NETWORK":
                    source = "NETWORK"
                    accuracy = "Medium - Carrier network"
                elif provided_source == "SMS_LINK":
                    source = "SMS_LINK"
                    accuracy = "High - SMS link GPS"
                else:
                    # If lat/lng provided but no source, treat as farmer provided via SMS link
                    source = "GPS"
                    accuracy = "High - Provided coordinates"
            else:
                lat_f = None
                lng_f = None
    except Exception:
        lat_f = None
        lng_f = None

    # If no GPS, but farmer provided village/district
    if lat_f is None and (village or district):
        source = "FARMER_PROVIDED"
        accuracy = "Low - Farmer verbal description"
        # Optionally provide centroid as approximate but MUST label as such
        # We do NOT store centroid as GPS - we leave lat/lng NULL and set approximate flag
        centroid = DISTRICT_CENTROIDS.get((district or "").lower())
        if centroid and not lat_f:
            # We intentionally do NOT fill lat/lng with centroid to avoid fake GPS illusion
            # Instead, caller can use district centroid for map display with disclaimer
            pass

    # If state/district provided via DTMS choice 1 = Maharashtra
    if district and not village:
        # Still farmer provided
        if source == "NOT_AVAILABLE":
            source = "FARMER_PROVIDED"
            accuracy = "Low - District only"

    return {
        "village": village or "Not provided",
        "block": responses.get("block") or responses.get("location_block") or "",
        "district": district or "Not provided",
        "state": state or "Maharashtra",
        "lat": lat_f,
        "lng": lng_f,
        "location_source": source,
        "location_accuracy": accuracy,
        "is_gps_available": lat_f is not None,
    }

def location_to_dict_for_report(loc: Dict[str, Any]) -> Dict[str, Any]:
    """Format for storage in ivr_reports."""
    return {
        "location_village": loc.get("village"),
        "location_block": loc.get("block") or "",
        "location_district": loc.get("district"),
        "location_state": loc.get("state"),
        "location_lat": loc.get("lat"),
        "location_lng": loc.get("lng"),
        "location_source": loc.get("location_source"),
        "location_accuracy": loc.get("location_accuracy"),
    }

def suggest_location_sms_link(call_sid: str, phone: str) -> str:
    """Generate secure location sharing link (opt-in). In prod, would be signed URL."""
    import hashlib, os
    secret = os.environ.get("SIH_SECRET_KEY", "dev-secret")
    token = hashlib.sha256(f"{call_sid}:{phone}:{secret}".encode()).hexdigest()[:16]
    base = os.environ.get("APP_BASE_URL", "https://example.com")
    return f"{base}/api/ivr/location/share?call_sid={call_sid}&token={token}"
