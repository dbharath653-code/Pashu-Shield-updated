"""
Survey service - configurable multilingual survey definition and DTMF/speech handling.
"""
import json
from typing import Dict, Any, List, Optional, Tuple

# Canonical question order - must match locales
SURVEY_QUESTION_KEYS = [
    "farmer_name",
    "species",
    "animal_count",
    "breed",
    "age",
    "sex",
    "pregnancy",
    "main_problem",
    "symptoms",
    "duration",
    "severity",
    "eating",
    "drinking",
    "temperature",
    "vaccination",
    "previous_disease",
    "medicines",
    "location_village",
    "location_district",
    "location_state",
    "additional",
]

# Mapping for DTMF choices to normalized values
CHOICE_MAPS = {
    "species": {"1": "Cattle", "2": "Buffalo", "3": "Goat", "4": "Sheep", "5": "Poultry", "6": "Other"},
    "sex": {"1": "Female", "2": "Male", "3": "Unknown"},
    "pregnancy": {"1": "Yes", "2": "No", "3": "Not Applicable"},
    "main_problem": {"1": "Fever", "2": "Not eating", "3": "Diarrhea", "4": "Breathing difficulty", "5": "Skin lesions", "6": "Lameness", "7": "Other"},
    "severity": {"1": "Mild", "2": "Medium", "3": "High", "4": "Critical"},
    "eating": {"1": "Yes", "2": "No"},
    "drinking": {"1": "Yes", "2": "No"},
    "vaccination": {"1": "Yes", "2": "No", "3": "Unknown"},
    "previous_disease": {"1": "Yes", "2": "No"},
    "location_state": {"1": "Maharashtra", "2": "Other"},
}

# Questions requiring confirmation
CONFIRM_QUESTIONS = {"species", "severity", "location_district"}

# Conditional: pregnancy only relevant for certain species
def should_ask_pregnancy(responses: Dict[str, Any]) -> bool:
    species = (responses.get("species") or "").lower()
    return species in ("cattle", "buffalo", "goat", "sheep", "cow", "cattle/buffalo")

def should_skip_question(key: str, responses: Dict[str, Any]) -> bool:
    if key == "pregnancy" and not should_ask_pregnancy(responses):
        return True
    # If no vaccination info, don't repeatedly ask (already asked once, but conditional handled elsewhere)
    return False

def get_next_question_index(current_index: int, responses: Dict[str, Any]) -> int:
    """Get next question index skipping conditionals."""
    next_idx = current_index + 1
    while next_idx < len(SURVEY_QUESTION_KEYS):
        key = SURVEY_QUESTION_KEYS[next_idx]
        if should_skip_question(key, responses):
            next_idx += 1
            continue
        break
    return next_idx

def normalize_answer(question_key: str, raw: str, source: str = "dtmf", language: str = "en") -> Tuple[str, str, Optional[str]]:
    """
    Normalize raw answer to canonical value.
    Returns: (normalized_value, answer_source, dtmf_digit)
    """
    raw = (raw or "").strip()
    if not raw:
        return ("Not provided", source, None)

    # Handle DTMF digit for choice questions
    if question_key in CHOICE_MAPS:
        mapping = CHOICE_MAPS[question_key]
        # DTMF is single digit
        if raw in mapping:
            return (mapping[raw], "dtmf", raw)
        # Speech input that matches values
        lower_raw = raw.lower()
        for digit, val in mapping.items():
            if lower_raw == val.lower() or lower_raw in val.lower() or val.lower() in lower_raw:
                return (val, "speech", digit)
        # Fallback: speech contains keyword
        if question_key == "species":
            if "cow" in lower_raw or "cattle" in lower_raw or "gaay" in lower_raw:
                return ("Cattle", "speech", "1")
            if "buff" in lower_raw or "bhains" in lower_raw:
                return ("Buffalo", "speech", "2")
            if "goat" in lower_raw or "bakri" in lower_raw:
                return ("Goat", "speech", "3")
        # If not matched, keep raw
        return (raw.title() if len(raw) < 50 else raw, "speech", None)

    # Number fields
    if question_key in ("animal_count", "age", "duration", "temperature"):
        # Extract digits
        import re
        digits = re.sub(r"[^\d.]", "", raw)
        if digits:
            try:
                # Keep as string but validate
                if "." in digits:
                    val = str(float(digits))
                else:
                    val = str(int(digits))
                return (val, source, None)
            except Exception:
                pass
        # If DTMF hash skipped
        if raw in ("#", "", "skip"):
            return ("Not provided", source, None)
        return ("Not provided", source, None)

    # Free speech / village / district
    if len(raw) > 200:
        raw = raw[:200]
    # Capitalize
    if raw and len(raw) < 100:
        return (raw.strip().title() if question_key.startswith("location_") else raw.strip(), source, None)
    return (raw.strip(), source, None)

def is_valid_answer(question_key: str, normalized: str) -> bool:
    if question_key in ("species", "main_problem", "severity", "eating", "drinking"):
        return normalized != "Not provided" and normalized != ""
    # Others optional-ish, but animal_count and duration ideally required
    if question_key in ("animal_count", "duration"):
        return normalized != "Not provided"
    return True

def is_critical_missing(responses: Dict[str, Any]) -> List[str]:
    """Return list of critical missing fields after survey."""
    missing = []
    for key in ("species", "main_problem", "severity", "location_district", "location_village"):
        val = responses.get(key)
        if not val or val == "Not provided":
            missing.append(key)
    return missing

# Speech-to-text confidence threshold
STT_CONFIDENCE_THRESHOLD = 0.6

def should_retry_stt(confidence: float, text: str) -> bool:
    if not text or not text.strip():
        return True
    if confidence is not None and confidence < STT_CONFIDENCE_THRESHOLD:
        return True
    return False

# Configurable survey loader from DB
def load_survey_config_from_db(conn) -> Dict[str, Any]:
    try:
        row = conn.execute("SELECT config_value FROM ivr_survey_config WHERE config_key='survey_definition'").fetchone()
        if row and row["config_value"]:
            return json.loads(row["config_value"])
    except Exception:
        pass
    # Fallback to defaults
    from ..schema import DEFAULT_SURVEY_CONFIG
    return DEFAULT_SURVEY_CONFIG

def save_survey_config_to_db(conn, config: Dict[str, Any], updated_by: int = None):
    val = json.dumps(config)
    conn.execute(
        "INSERT INTO ivr_survey_config (config_key, config_value, description, updated_by) VALUES ('survey_definition', ?, 'Survey question definition', ?) "
        "ON CONFLICT(config_key) DO UPDATE SET config_value=?, updated_at=datetime('now'), updated_by=?",
        (val, updated_by, val, updated_by)
    )
    conn.commit()
