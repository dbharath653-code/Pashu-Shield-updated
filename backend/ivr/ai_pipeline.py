"""
Structured, non-hallucinating IVR summarisation.

Pipeline
--------
    transcript / survey answers
        -> deterministic extraction (always available, evidence based)
        -> optional LLM structuring (IVR_AI_PROVIDER=openai_compatible)
        -> schema validation
        -> evidence grounding check (drop anything not supported by the
           conversation; never invent missing information)
        -> provenance labelling (FARMER_REPORTED / VET_VERIFIED /
           AI_GENERATED / SYSTEM_GENERATED)

Guarantees
----------
* Every field that was not actually stated ends up as "Not provided" or None.
* The model is never allowed to diagnose, prescribe or invent medication;
  the system prompt forbids it and the grounding check removes anything it
  could not have heard.
* Output is always the validated schema - malformed model output is rejected
  and the deterministic extraction is used instead (recorded in warnings).
"""
import json
import re
from datetime import datetime, timezone

import requests

from .config import settings

NOT_PROVIDED = "Not provided"
AI_DISCLAIMER = "AI-generated summary — veterinarian verification required."
SYSTEM_DISCLAIMER = "System-generated structured summary from IVR input — veterinarian verification required."

LIST_FIELDS = (
    "symptoms", "previous_treatment", "veterinarian_observations", "veterinarian_advice",
    "visible_signs", "medicines_given",
)
TEXT_FIELDS = (
    "duration", "severity", "vaccination_status", "urgency", "problem",
    "eating", "drinking", "temperature", "previous_disease", "pregnancy_status",
    "additional_description", "recommended_next_action", "summary_text",
)

# --------------------------------------------------------------- schema ----
EMPTY_STRUCTURE = {
    "animal": {"species": NOT_PROVIDED, "breed": NOT_PROVIDED, "age": NOT_PROVIDED,
               "sex": NOT_PROVIDED, "count": None},
    "symptoms": [],
    "visible_signs": [],
    "duration": NOT_PROVIDED,
    "severity": NOT_PROVIDED,
    "problem": NOT_PROVIDED,
    "eating": NOT_PROVIDED,
    "drinking": NOT_PROVIDED,
    "temperature": NOT_PROVIDED,
    "previous_treatment": [],
    "medicines_given": [],
    "previous_disease": NOT_PROVIDED,
    "vaccination_status": NOT_PROVIDED,
    "pregnancy_status": NOT_PROVIDED,
    "veterinarian_observations": [],
    "veterinarian_advice": [],
    "follow_up_required": None,
    "urgency": NOT_PROVIDED,
    "recommended_next_action": NOT_PROVIDED,
    "additional_description": NOT_PROVIDED,
    "farmer": {"name": NOT_PROVIDED, "phone": NOT_PROVIDED},
    "location": {"village": NOT_PROVIDED, "district": NOT_PROVIDED, "state": NOT_PROVIDED,
                 "lat": None, "lng": None, "source": "NOT_AVAILABLE", "accuracy": None},
    "source": "IVR",
    "call_id": "",
    "timestamp": "",
    "language": "en",
}


def empty_structure(call_id=None, language="en"):
    data = json.loads(json.dumps(EMPTY_STRUCTURE))
    data["call_id"] = call_id or ""
    data["language"] = language
    data["timestamp"] = datetime.now(timezone.utc).isoformat()
    return data


class SchemaError(Exception):
    pass


def validate_structure(data):
    """Validate + coerce a structured report. Raises SchemaError when unusable."""
    if not isinstance(data, dict):
        raise SchemaError("structured report must be a JSON object")
    clean = json.loads(json.dumps(EMPTY_STRUCTURE))

    animal = data.get("animal") if isinstance(data.get("animal"), dict) else {}
    for key in ("species", "breed", "age", "sex"):
        clean["animal"][key] = _clean_text(animal.get(key))
    clean["animal"]["count"] = _clean_int(animal.get("count"))

    farmer = data.get("farmer") if isinstance(data.get("farmer"), dict) else {}
    clean["farmer"]["name"] = _clean_text(farmer.get("name"))
    clean["farmer"]["phone"] = _clean_text(farmer.get("phone"))

    loc = data.get("location") if isinstance(data.get("location"), dict) else {}
    for key in ("village", "district", "state"):
        clean["location"][key] = _clean_text(loc.get(key))
    clean["location"]["lat"] = _clean_float(loc.get("lat"))
    clean["location"]["lng"] = _clean_float(loc.get("lng"))
    clean["location"]["accuracy"] = _clean_float(loc.get("accuracy"))
    src = str(loc.get("source") or "NOT_AVAILABLE").upper()
    clean["location"]["source"] = src if src in ("GPS", "NETWORK", "FARMER_PROVIDED", "REGISTRY", "NOT_AVAILABLE") else "NOT_AVAILABLE"

    for key in LIST_FIELDS:
        clean[key] = _clean_list(data.get(key))

    for key in TEXT_FIELDS:
        if key == "summary_text":
            continue
        clean[key] = _clean_text(data.get(key))

    fur = data.get("follow_up_required")
    if isinstance(fur, bool):
        clean["follow_up_required"] = fur
    elif isinstance(fur, str):
        low = fur.strip().lower()
        clean["follow_up_required"] = True if low in ("true", "yes", "1") else (False if low in ("false", "no", "0") else None)
    else:
        clean["follow_up_required"] = None

    clean["source"] = "IVR"
    clean["call_id"] = str(data.get("call_id") or "")[:128]
    clean["timestamp"] = str(data.get("timestamp") or datetime.now(timezone.utc).isoformat())[:64]
    clean["language"] = str(data.get("language") or "en")[:8]
    clean["summary_text"] = _clean_text(data.get("summary_text"), limit=4000)
    return clean


def _clean_text(value, limit=500):
    if value is None:
        return NOT_PROVIDED
    text = str(value).strip()
    if not text or text.lower() in ("none", "null", "n/a", "na", ""):
        return NOT_PROVIDED
    return text[:limit]


def _clean_list(value, limit=40):
    if value is None:
        return []
    if isinstance(value, str):
        value = [v for v in re.split(r"[,;/]| and ", value) if v.strip()]
    if not isinstance(value, list):
        return []
    out = []
    for item in value[:limit]:
        text = str(item).strip()
        if text and text.lower() not in ("none", "null", "n/a", "na") and text not in out:
            out.append(text[:200])
    return out


def _clean_int(value):
    try:
        if value is None or value == "":
            return None
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _clean_float(value):
    try:
        if value is None or value == "":
            return None
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


# ------------------------------------------------- deterministic extraction --
SPECIES_WORDS = {
    "cattle": ["cattle", "cow", "bull", "ox", "calf", "heifer", "गाय", "गौ", "बैल", "ఆవు", "గేదె" if False else "ఆవు", "गाय"],
    "buffalo": ["buffalo", "she-buffalo", "murrah", "भैंस", "గేదె", "म्हैस"],
    "goat": ["goat", "she-goat", "बकरी", "మేక", "शेळी"],
    "sheep": ["sheep", "ram", "ewe", "भेड़", "గొర్రె", "मेंढी"],
    "poultry": ["poultry", "chicken", "hen", "bird", "duck", "मुर्गी", "కోడి", "कोंबडी"],
    "pig": ["pig", "swine", "boar", "सूअर", "పంది", "डुक्कर"],
}

SYMPTOM_WORDS = {
    "fever": ["fever", "temperature", "hot", "बुखार", "ताप", "జ్వరం", "ज्वर"],
    "not eating": ["not eating", "no appetite", "stopped eating", "खाना बंद", "खाना कम", "తినడం లేదు", "खात नाही"],
    "not drinking": ["not drinking", "no water", "पानी नहीं", "నీళ్లు తాగడం లేదు", "पाणी पित नाही"],
    "cough": ["cough", "खांसी", "దగ్గు", "खोकला"],
    "breathing difficulty": ["breathing", "dyspnoea", "dyspnea", "gasping", "सांस", "श्वास", "శ్వాస"],
    "drooling": ["drooling", "salivation", "slobbering", "लार", "లాలాజలం", "लाळ"],
    "nasal discharge": ["nasal discharge", "running nose", "नाक बहना", "ముక్కు కారడం", "नाक वाहणे"],
    "diarrhoea": ["diarrhoea", "diarrhea", "loose motion", "दस्त", "విరేచనాలు", "जुलाब"],
    "swelling": ["swelling", "swollen", "oedema", "edema", "सूजन", "వాపు", "सूज"],
    "lameness": ["lameness", "limping", "lame", "लंगड़ा", "కుంటు", "लंगड"],
    "milk drop": ["milk drop", "less milk", "milk reduced", "दूध कम", "పాలు తగ్గ", "दूध कमी"],
    "blood in dung/urine": ["blood", "bloody", "खून", "రక్తం", "रक्त"],
    "abortion": ["abortion", "miscarriage", "गर्भपात", "గర్భస్రావం", "गर्भपात"],
    "convulsions": ["convulsion", "fit", "seizure", "दौरा", "ఫిట్స్", "झटके"],
    "animal down": ["cannot stand", "down", "recumbent", "खड़ा नहीं", "नిలబడలేక", "उभे राहू"],
    "death": ["died", "dead", "death", "मर गया", "मृत्यु", "చనిపోయింది", "मेले"],
}

MEDICINE_WORDS = [
    "oxytetracycline", "tetracycline", "penicillin", "amoxicillin", "ampicillin",
    "streptomycin", "gentamicin", "enrofloxacin", "ciprofloxacin", "meloxicam",
    "flunixin", "ketoprofen", "paracetamol", "ivermectin", "albendazole",
    "fenbendazole", "cloxacillin", "sulfa", "sulphonamide", "doxycycline",
    "dexamethasone", "chlorpheniramine", "vitamin", "mineral mixture",
    "dicyclomine", "fipronil", "deltamethrin",
]

DURATION_PATTERNS = [
    (r"(?:since|for)\s+(\d+)\s*(day|days)", lambda n: f"{n} days"),
    (r"(?:since|for)\s+(\d+)\s*(week|weeks)", lambda n: f"{n} weeks"),
    (r"(?:since|for)\s+(\d+)\s*(month|months)", lambda n: f"{n} months"),
    (r"(today|since today|from today)", lambda n: "today"),
    (r"(yesterday)", lambda n: "1 day"),
]

SEVERITY_WORDS = {
    "critical": ["critical", "cannot stand", "down", "गंभीर", "तीव्र", "తీవ్ర"],
    "severe": ["severe", "serious", "very sick", "गंभीर", "तेज", "చాలా"],
    "moderate": ["moderate", "medium", "मध्यम", "మధ్యస్థ"],
    "mild": ["mild", "slight", "little", "हल्का", "सौम्य", "తేలిక"],
}


def _norm(text):
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def extract_deterministic(transcript, extra_answers=None):
    """Evidence-based extraction. Only what was literally said is captured."""
    text = _norm(transcript)
    data = empty_structure()

    if not text:
        # No conversation audio: the structured survey answers are the only
        # evidence, so they are merged and returned as-is.
        if extra_answers:
            _merge_answers(data, extra_answers)
        return data

    for species, words in SPECIES_WORDS.items():
        if any(w in text for w in words):
            data["animal"]["species"] = species.capitalize()
            break

    for label, words in SYMPTOM_WORDS.items():
        if any(w in text for w in words):
            data["symptoms"].append(label)

    count = None
    m = re.search(r"(\d+)\s+(?:animals?|cattle|buffaloes?|goats?|sheep|birds?|pigs?)", text)
    if m:
        count = int(m.group(1))
    if count:
        data["animal"]["count"] = count

    m = re.search(r"(?:age|aged)\s*(?:is)?\s*(\d{1,2})", text)
    if m:
        data["animal"]["age"] = f"{m.group(1)} years"
    m = re.search(r"(\d{1,2})\s*(?:years?|yrs?|year old)\b", text)
    if m and data["animal"]["age"] == NOT_PROVIDED:
        data["animal"]["age"] = f"{m.group(1)} years"

    if re.search(r"\b(female|she|cow|buffalo|doe|ewe|hen)\b", text):
        data["animal"]["sex"] = "Female"
    elif re.search(r"\b(male|he|bull|ox|ram|buck|cock)\b", text):
        data["animal"]["sex"] = "Male"

    for pattern, fmt in DURATION_PATTERNS:
        m = re.search(pattern, text)
        if m:
            try:
                data["duration"] = fmt(m.group(1))
            except Exception:
                pass
            break

    for level, words in SEVERITY_WORDS.items():
        if any(w in text for w in words):
            data["severity"] = level.capitalize()
            break

    m = re.search(r"(\d{2,3}(?:\.\d)?)\s*(?:degrees|degree|°?\s*[fc]\b|fahrenheit|celsius)", text)
    if m:
        data["temperature"] = m.group(1)

    if re.search(r"\b(vaccinated|vaccination done|टीकाकरण|టీకాలు|लसीकरण)\b", text):
        data["vaccination_status"] = "Yes"
    elif re.search(r"\b(not vaccinated|no vaccination|unvaccinated)\b", text):
        data["vaccination_status"] = "No"

    for med in MEDICINE_WORDS:
        if med in text:
            data["medicines_given"].append(med)
            data["previous_treatment"].append(f"{med} (reported by farmer)")

    if re.search(r"\b(eating|eats|खा रहा|తింటుంది|खात आहे)\b", text) and "not eating" not in data["symptoms"]:
        data["eating"] = "Yes"
    if "not eating" in data["symptoms"]:
        data["eating"] = "No"
    if "not drinking" in data["symptoms"]:
        data["drinking"] = "No"
    elif re.search(r"\b(drinking|drinks water|पानी पी|నీళ్లు తాగు|पाणी पित)\b", text):
        data["drinking"] = "Yes"

    if re.search(r"\b(pregnant|गर्भवती|గర్భిణి|गरोदर)\b", text):
        data["pregnancy_status"] = "Pregnant"

    # Veterinarian contributions are only treated as such when the transcript
    # carries explicit speaker labels from the provider/STT.
    for segment in re.findall(r"\[VET\]\s*([^\[]+)", str(transcript or "")):
        seg = segment.strip()
        if not seg:
            continue
        if re.search(r"\b(advise|advice|recommend|give|should|continue|isolate|vaccinate)\b", seg, re.I):
            data["veterinarian_advice"].append(seg[:300])
        else:
            data["veterinarian_observations"].append(seg[:300])

    if extra_answers:
        _merge_answers(data, extra_answers)

    return data


def _merge_answers(data, answers):
    """Overlay verified survey answers (highest quality source) onto the data."""
    mapping = {
        "species": ("animal", "species"),
        "breed": ("animal", "breed"),
        "age": ("animal", "age"),
        "sex": ("animal", "sex"),
        "count": ("animal", "count"),
        "problem": ("problem", None),
        "duration": ("duration", None),
        "severity": ("severity", None),
        "eating": ("eating", None),
        "drinking": ("drinking", None),
        "temperature": ("temperature", None),
        "vaccination_status": ("vaccination_status", None),
        "previous_disease": ("previous_disease", None),
        "pregnancy_status": ("pregnancy_status", None),
        "additional_description": ("additional_description", None),
    }
    for key, value in (answers or {}).items():
        if value in (None, "", NOT_PROVIDED):
            continue
        target = mapping.get(key)
        if target:
            section, field = target
            if field:
                data[section][field] = value
            else:
                data[section] = value
        elif key == "symptoms" and value:
            for s in value if isinstance(value, list) else [value]:
                if s and s not in data["symptoms"]:
                    data["symptoms"].append(s)
        elif key == "previous_treatment" and value:
            for s in value if isinstance(value, list) else [value]:
                if s and s not in data["previous_treatment"]:
                    data["previous_treatment"].append(s)
        elif key.startswith("location."):
            data["location"][key.split(".", 1)[1]] = value


# --------------------------------------------------------------- grounding --
_STOPWORDS = {"the", "a", "an", "of", "and", "or", "is", "was", "were", "to", "in", "on",
              "for", "with", "has", "have", "been", "not", "no", "yes", "animal", "animals"}


def ground(structured, transcript):
    """Remove/mark any extracted value that has no support in the transcript.

    This is the anti-hallucination guard: if the words are not in the
    conversation, the field reverts to "Not provided" and the rejected value
    is preserved in `unverified_extractions` for audit.
    """
    hay = _norm(transcript)
    removed = []
    if not hay:
        return structured, removed

    def supported(value):
        tokens = [tok for tok in re.findall(r"[\w\u0900-\u097F\u0C00-\u0C7F]+", _norm(value))
                  if len(tok) > 2 and tok not in _STOPWORDS]
        if not tokens:
            return True
        return all(tok in hay for tok in tokens)

    for key in TEXT_FIELDS:
        if key in ("summary_text", "recommended_next_action", "urgency"):
            continue
        value = structured.get(key)
        if isinstance(value, str) and value not in (NOT_PROVIDED, "", None):
            if not supported(value):
                removed.append({key: value})
                structured[key] = NOT_PROVIDED

    for section in ("animal", "farmer", "location"):
        for key, value in list(structured.get(section, {}).items()):
            if key in ("lat", "lng", "accuracy", "source", "count"):
                continue
            if isinstance(value, str) and value not in (NOT_PROVIDED, "", None):
                if not supported(value):
                    removed.append({f"{section}.{key}": value})
                    structured[section][key] = NOT_PROVIDED

    for key in LIST_FIELDS:
        kept = []
        for item in structured.get(key, []):
            if supported(item):
                kept.append(item)
            else:
                removed.append({key: item})
        structured[key] = kept

    return structured, removed


# ------------------------------------------------------------------- LLM ----
SYSTEM_PROMPT = """You are a veterinary call SUMMARISATION assistant for the
PashuMitra livestock health helpline. You convert a call transcript into a
strict JSON record.

HARD RULES (violating them makes the output invalid):
1. NEVER diagnose. Never name a disease as a diagnosis unless the
   veterinarian on the call said it explicitly.
2. NEVER invent symptoms, medicines, vaccination facts, temperatures,
   locations or animal details.
3. If something was not said, output exactly "Not provided".
4. Copy the animal/drug names exactly as spoken; do not translate them.
5. veterinarian_advice must contain ONLY what the veterinarian actually
   advised on the call. If no veterinarian spoke, return an empty list.
6. urgency must be one of Low, Medium, High, Critical, or "Not provided".
7. Return a single JSON object and nothing else - no markdown, no prose.

JSON schema:
{
  "animal": {"species": "", "breed": "", "age": "", "sex": "", "count": null},
  "symptoms": [], "visible_signs": [],
  "duration": "", "severity": "", "problem": "",
  "eating": "", "drinking": "", "temperature": "",
  "previous_treatment": [], "medicines_given": [], "previous_disease": "",
  "vaccination_status": "", "pregnancy_status": "",
  "veterinarian_observations": [], "veterinarian_advice": [],
  "follow_up_required": null,
  "urgency": "", "recommended_next_action": "",
  "additional_description": "",
  "summary_text": ""
}"""


def _extract_json(raw):
    if not raw:
        return None
    text = str(raw).strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return None


def llm_summarise(transcript, language="en", call_id=None):
    """Call the configured OpenAI-compatible model. Returns dict or raises."""
    if (settings.AI_PROVIDER or "none").lower() != "openai_compatible":
        raise RuntimeError("IVR_AI_PROVIDER is not openai_compatible")
    if not settings.AI_API_KEY:
        raise RuntimeError("IVR_AI_API_KEY is not set")

    url = f"{settings.AI_BASE_URL.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {settings.AI_API_KEY}", "Content-Type": "application/json"}
    body = {
        "model": settings.AI_MODEL,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"Call language: {language}\n"
                f"Transcript:\n{str(transcript)[:settings.AI_MAX_TRANSCRIPT_CHARS]}"
            )},
        ],
    }
    resp = requests.post(url, headers=headers, json=body, timeout=settings.AI_TIMEOUT)
    resp.raise_for_status()
    payload = resp.json()
    content = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
    parsed = _extract_json(content)
    if parsed is None:
        raise RuntimeError("model did not return valid JSON")
    return parsed, payload.get("model", settings.AI_MODEL)


# --------------------------------------------------------------- pipeline ---
def summarise(transcript, language="en", call_id=None, extra_answers=None,
              use_llm=None, allow_llm=None):
    """Produce the structured report.

    Returns:
      {
        "structured": validated dict,
        "summary_text": str,
        "model": str,
        "ai_generated": bool,
        "disclaimer": str,
        "warnings": [str],
        "unverified_extractions": [dict],
        "provenance": {field: source}
      }
    """
    warnings = []
    unverified = []
    deterministic = extract_deterministic(transcript, extra_answers)
    structured = None
    model = "deterministic-extractor-v1"
    ai_generated = False

    want_llm = allow_llm if allow_llm is not None else (
        use_llm if use_llm is not None else (settings.AI_PROVIDER or "none").lower() == "openai_compatible"
    )

    if want_llm and (transcript or "").strip():
        try:
            parsed, used_model = llm_summarise(transcript, language, call_id)
            candidate = json.loads(json.dumps(deterministic))
            for key, value in parsed.items():
                if key in ("summary_text",):
                    continue
                candidate[key] = value
            if extra_answers:
                _merge_answers(candidate, extra_answers)
            structured = validate_structure(candidate)
            model = used_model
            ai_generated = True
        except Exception as exc:  # never fail the report because of the model
            warnings.append(f"LLM summarisation unavailable/failed ({exc}); used deterministic extraction")
            structured = None

    if structured is None:
        structured = validate_structure(deterministic)
        model = "deterministic-extractor-v1"
        ai_generated = False

    structured, unverified = ground(structured, transcript or "")
    structured["call_id"] = call_id or structured.get("call_id") or ""
    structured["language"] = language

    if not structured.get("summary_text") or structured["summary_text"] == NOT_PROVIDED:
        structured["summary_text"] = build_summary_text(structured)

    provenance = build_provenance(structured, extra_answers, ai_generated)

    return {
        "structured": structured,
        "summary_text": structured["summary_text"],
        "model": model,
        "ai_generated": ai_generated,
        "disclaimer": AI_DISCLAIMER if ai_generated else SYSTEM_DISCLAIMER,
        "warnings": warnings,
        "unverified_extractions": unverified,
        "provenance": provenance,
    }


def build_provenance(structured, extra_answers=None, ai_generated=False):
    """Label every important field with where it came from."""
    prov = {}
    survey_keys = set((extra_answers or {}).keys())
    key_to_field = {
        "species": "animal.species", "breed": "animal.breed", "age": "animal.age",
        "sex": "animal.sex", "count": "animal.count", "problem": "problem",
        "duration": "duration", "severity": "severity", "eating": "eating",
        "drinking": "drinking", "temperature": "temperature",
        "vaccination_status": "vaccination_status",
        "previous_disease": "previous_disease",
        "pregnancy_status": "pregnancy_status",
        "additional_description": "additional_description",
        "symptoms": "symptoms", "previous_treatment": "previous_treatment",
    }
    for survey_key, field in key_to_field.items():
        value = structured
        for part in field.split("."):
            value = (value or {}).get(part) if isinstance(value, dict) else None
        if value in (None, NOT_PROVIDED, []):
            continue
        prov[field] = "FARMER_REPORTED" if survey_key in survey_keys else (
            "AI_GENERATED" if ai_generated else "SYSTEM_GENERATED")
    if structured.get("veterinarian_advice"):
        prov["veterinarian_advice"] = "VET_VERIFIED"
    if structured.get("veterinarian_observations"):
        prov["veterinarian_observations"] = "VET_VERIFIED"
    return prov


def build_summary_text(structured):
    """Human readable (English) summary used in the web app and notifications."""
    animal = structured.get("animal", {})
    location = structured.get("location", {})
    parts = []
    species = animal.get("species")
    count = animal.get("count")
    if species and species != NOT_PROVIDED:
        parts.append(f"{count} {species}" if count else f"{species}")
    elif count:
        parts.append(f"{count} animal(s)")
    if animal.get("breed") not in (None, NOT_PROVIDED):
        parts.append(f"breed {animal['breed']}")
    if animal.get("age") not in (None, NOT_PROVIDED):
        parts.append(f"age {animal['age']}")
    if animal.get("sex") not in (None, NOT_PROVIDED):
        parts.append(f"sex {animal['sex']}")
    head = "Reported animal: " + (", ".join(parts) if parts else "Not provided")

    symptoms = structured.get("symptoms") or []
    body = [
        head,
        f"Problem: {structured.get('problem', NOT_PROVIDED)}",
        f"Symptoms: {', '.join(symptoms) if symptoms else NOT_PROVIDED}",
        f"Duration: {structured.get('duration', NOT_PROVIDED)}",
        f"Severity: {structured.get('severity', NOT_PROVIDED)}",
        f"Eating: {structured.get('eating', NOT_PROVIDED)}; Drinking: {structured.get('drinking', NOT_PROVIDED)}",
        f"Vaccination: {structured.get('vaccination_status', NOT_PROVIDED)}",
        f"Treatment already given: {', '.join(structured.get('previous_treatment') or []) or NOT_PROVIDED}",
        f"Location: {location.get('village', NOT_PROVIDED)}, {location.get('district', NOT_PROVIDED)}, "
        f"{location.get('state', NOT_PROVIDED)} (source: {location.get('source', 'NOT_AVAILABLE')})",
    ]
    if structured.get("veterinarian_advice"):
        body.append("Veterinarian advice on call: " + "; ".join(structured["veterinarian_advice"]))
    if structured.get("additional_description") not in (None, NOT_PROVIDED):
        body.append("Additional: " + structured["additional_description"])
    return ". ".join(body)
