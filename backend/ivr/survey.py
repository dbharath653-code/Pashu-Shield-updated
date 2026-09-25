"""
Configurable multilingual survey engine.

The question set lives in the database (`ivr_surveys.definition`) and is
seeded from `default_survey.DEFAULT_SURVEY` on first boot, so an authorised
administrator can change wording, options, ordering and conditions without
a code deployment.

Every question supports:
  * DTMF answers (always)
  * Speech answers (when the provider/STT supports the chosen language)
  * 0 = go back, 9 (or *) = repeat the question
  * an explicit "don't know" escape so the farmer is never forced to answer
"""
import json
import re
from datetime import datetime

from .default_survey import DEFAULT_SURVEY
from .locales import t

NAV_BACK = "0"
NAV_REPEAT = "9"
NAV_REPEAT_ALT = "*"
NOT_PROVIDED = "Not provided"

_NUMBER_WORDS = {
    "en": {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
           "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twenty": 20, "fifty": 50,
           "hundred": 100},
    "hi": {"शून्य": 0, "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पाँच": 5, "पांच": 5,
           "छह": 6, "सात": 7, "आठ": 8, "नौ": 9, "दस": 10},
    "te": {"సున్నా": 0, "ఒకటి": 1, "రెండు": 2, "మూడు": 3, "నాలుగు": 4, "ఐదు": 5,
           "ఆరు": 6, "ఏడు": 7, "ఎనిమిది": 8, "తొమ్మిది": 9, "పది": 10},
    "mr": {"शून्य": 0, "एक": 1, "दोन": 2, "तीन": 3, "चार": 4, "पाच": 5, "सहा": 6,
           "सात": 7, "आठ": 8, "नऊ": 9, "दहा": 10},
}


class Answer:
    """Result of normalising one farmer input."""

    def __init__(self, value=None, label=None, confidence=None, mode="DTMF",
                 raw=None, error=None, transcript=None):
        self.value = value
        self.label = label
        self.confidence = confidence
        self.mode = mode
        self.raw = raw
        self.error = error            # None | 'no_input' | 'invalid' | 'repeat' | 'back' | 'low_confidence'
        self.transcript = transcript

    @property
    def ok(self):
        return self.error is None


# ------------------------------------------------------------------ storage --
def ensure_default_survey(conn):
    """Insert the default survey definition if none is present."""
    row = conn.execute("SELECT * FROM ivr_surveys WHERE is_active=1 ORDER BY id LIMIT 1").fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO ivr_surveys (code, version, is_active, definition) VALUES (?,?,1,?)",
        (DEFAULT_SURVEY["code"], DEFAULT_SURVEY["version"], json.dumps(DEFAULT_SURVEY, ensure_ascii=False)),
    )
    conn.commit()
    return cur.lastrowid


def get_active_survey(conn):
    """Return (survey_row, definition_dict). Seeds the default if needed."""
    ensure_default_survey(conn)
    row = conn.execute("SELECT * FROM ivr_surveys WHERE is_active=1 ORDER BY id LIMIT 1").fetchone()
    definition = json.loads(row["definition"]) if row else DEFAULT_SURVEY
    return row, definition


def save_survey(conn, definition, updated_by="admin"):
    """Validate and store a new survey definition (admin action)."""
    errors = validate_definition(definition)
    if errors:
        raise ValueError("; ".join(errors))
    definition = dict(definition)
    definition["version"] = int(definition.get("version", 1)) + 1
    base_code = definition.get("code") or DEFAULT_SURVEY["code"]
    code = f"{base_code}-v{definition['version']}"
    definition["code"] = code
    conn.execute("UPDATE ivr_surveys SET is_active=0")
    cur = conn.execute(
        "INSERT INTO ivr_surveys (code, version, is_active, definition, updated_at) VALUES (?,?,1,?,?)",
        (code, definition["version"], json.dumps(definition, ensure_ascii=False), datetime.utcnow().isoformat()),
    )
    conn.commit()
    return cur.lastrowid


def validate_definition(definition):
    """Structural validation for an admin supplied survey definition."""
    errors = []
    if not isinstance(definition, dict):
        return ["definition must be an object"]
    questions = definition.get("questions")
    if not isinstance(questions, list) or not questions:
        return ["definition.questions must be a non-empty list"]
    seen = set()
    for i, q in enumerate(questions):
        key = q.get("key")
        if not key:
            errors.append(f"question[{i}] missing key")
        elif key in seen:
            errors.append(f"duplicate question key '{key}'")
        seen.add(key)
        if q.get("type") not in ("menu", "boolean", "numeric", "speech", "phone"):
            errors.append(f"question '{key}' has unsupported type '{q.get('type')}'")
        if not isinstance(q.get("text"), dict) or not q["text"].get("en"):
            errors.append(f"question '{key}' needs at least an English text")
        for opt in q.get("options", []) or []:
            digit = str(opt.get("digit", ""))
            if digit and not re.fullmatch(r"[0-9*#]", digit):
                errors.append(f"question '{key}' option digit '{digit}' is not a valid DTMF key")
    return errors


# ------------------------------------------------------------------ engine --
class SurveyEngine:
    def __init__(self, definition, language="en"):
        self.definition = definition or DEFAULT_SURVEY
        self.language = language if language else "en"
        self.questions = self.definition.get("questions", [])

    # -- conditional visibility -------------------------------------------
    def _matches(self, question, answers):
        cond = question.get("conditional")
        if not cond:
            return True
        key = cond.get("depends_on")
        if not key:
            return True
        current = answers.get(key)
        if isinstance(current, Answer):
            current = current.value
        if current is None:
            return False
        allowed = cond.get("in")
        if allowed is not None:
            return str(current).lower() in [str(a).lower() for a in allowed]
        not_allowed = cond.get("not_in")
        if not_allowed is not None:
            return str(current).lower() not in [str(a).lower() for a in not_allowed]
        return True

    def visible_questions(self, answers):
        return [q for q in self.questions if self._matches(q, answers)]

    # -- prompting ---------------------------------------------------------
    def prompt(self, question):
        text = (question.get("text") or {}).get(self.language) or (question.get("text") or {}).get("en") or ""
        return text

    def option_menu_text(self, question):
        """Rendered after the prompt: 'Press 1 for Cattle. Press 2 for Buffalo...'"""
        opts = question.get("options") or []
        parts = []
        for o in opts:
            labels = o.get("labels") or {}
            label = labels.get(self.language) or labels.get("en") or o.get("value")
            parts.append(f"{o['digit']}: {label}")
        return ". ".join(parts)

    def repeat_digit(self, question):
        """9 is 'repeat' unless a question legitimately uses 9 as an option."""
        used = {str(o.get("digit")) for o in (question.get("options") or [])}
        return NAV_REPEAT_ALT if NAV_REPEAT in used else NAV_REPEAT

    def nav_help(self, question):
        key = "nav_help_star" if self.repeat_digit(question) == NAV_REPEAT_ALT else "nav_help"
        return t(self.language, key)

    def unknown_digit(self, question):
        """Digit that means 'I don't know' for free-form questions."""
        return "3" if question.get("allow_unknown") else None

    # -- normalisation -----------------------------------------------------
    def normalize(self, question, gather, min_confidence=0.5):
        """Turn a raw provider gather result into an Answer."""
        digits = (gather.digits or "").strip()
        speech = (gather.speech or "").strip()
        conf = gather.confidence

        # ---- navigation keys -------------------------------------------
        if digits == NAV_BACK:
            return Answer(error="back", mode="DTMF", raw=digits)
        if digits and digits == self.repeat_digit(question):
            return Answer(error="repeat", mode="DTMF", raw=digits)

        unknown_digit = self.unknown_digit(question)
        qtype = question.get("type")

        # ---- DTMF paths ---------------------------------------------------
        if digits:
            if qtype in ("menu", "boolean"):
                for o in question.get("options") or []:
                    if str(o.get("digit")) == digits:
                        labels = o.get("labels") or {}
                        return Answer(value=o.get("value"),
                                      label=labels.get(self.language) or labels.get("en") or o.get("value"),
                                      confidence=1.0, mode="DTMF", raw=digits)
                return Answer(error="invalid", mode="DTMF", raw=digits)
            if qtype in ("numeric", "phone"):
                if unknown_digit and digits == unknown_digit:
                    return Answer(value=NOT_PROVIDED, label=NOT_PROVIDED, confidence=1.0,
                                  mode="DTMF", raw=digits)
                if qtype == "phone":
                    from .security import normalize_phone
                    normalized, status = normalize_phone(digits)
                    if normalized:
                        return Answer(value=normalized, label=normalized, confidence=1.0,
                                      mode="DTMF", raw=digits)
                    return Answer(error="invalid", mode="DTMF", raw=digits)
                if digits.isdigit():
                    return Answer(value=int(digits), label=digits, confidence=1.0,
                                  mode="DTMF", raw=digits)
                return Answer(error="invalid", mode="DTMF", raw=digits)
            if qtype == "speech":
                if unknown_digit and digits == unknown_digit:
                    return Answer(value=NOT_PROVIDED, label=NOT_PROVIDED, confidence=1.0,
                                  mode="DTMF", raw=digits)
                return Answer(error="invalid", mode="DTMF", raw=digits)

        # ---- speech paths --------------------------------------------------
        if speech:
            if conf is not None and conf < min_confidence:
                return Answer(error="low_confidence", mode="SPEECH", raw=speech, transcript=speech,
                              confidence=conf)
            if qtype in ("menu", "boolean"):
                mapped = self._match_speech(question, speech)
                if mapped:
                    return Answer(value=mapped[0], label=mapped[1], confidence=conf or 0.7,
                                  mode="SPEECH", raw=speech, transcript=speech)
                return Answer(error="invalid", mode="SPEECH", raw=speech, transcript=speech,
                              confidence=conf)
            if qtype == "numeric":
                number = self._speech_to_int(speech)
                if number is None:
                    return Answer(error="invalid", mode="SPEECH", raw=speech, transcript=speech,
                                  confidence=conf)
                return Answer(value=number, label=str(number), confidence=conf or 0.7,
                              mode="SPEECH", raw=speech, transcript=speech)
            if qtype == "phone":
                from .security import normalize_phone
                normalized, _ = normalize_phone(speech)
                if normalized:
                    return Answer(value=normalized, label=normalized, confidence=conf or 0.7,
                                  mode="SPEECH", raw=speech, transcript=speech)
                return Answer(error="invalid", mode="SPEECH", raw=speech, transcript=speech,
                              confidence=conf)
            # free-form speech question
            return Answer(value=speech, label=speech, confidence=conf or 0.7,
                          mode="SPEECH", raw=speech, transcript=speech)

        return Answer(error="no_input", mode="UNKNOWN", raw=digits or speech or "")

    def _match_speech(self, question, speech):
        smap = (question.get("speech_map") or {}).get(self.language) \
            or (question.get("speech_map") or {}).get("en") or {}
        text = speech.lower()
        best = None
        for canonical, keywords in smap.items():
            for kw in keywords:
                if kw.lower() in text:
                    label = canonical
                    for o in question.get("options") or []:
                        if str(o.get("value")) == str(canonical):
                            labels = o.get("labels") or {}
                            label = labels.get(self.language) or labels.get("en") or canonical
                    return canonical, label
        # yes/no fallback for booleans
        if question.get("type") == "boolean":
            yes_words = {"yes", "ha", "haan", "हाँ", "हो", "अवुनు", "అవును", "ఆ", "ok", "okay"}
            no_words = {"no", "nahi", "नहीं", "नाही", "కాదు", "కాదు", "నా", "illa"}
            tokens = set(re.findall(r"[\w\u0900-\u097F\u0C00-\u0C7F]+", text))
            if tokens & yes_words:
                return "yes", t(self.language, "yes")
            if tokens & no_words:
                return "no", t(self.language, "no")
        return best

    def _speech_to_int(self, speech):
        digits = re.sub(r"\D", "", speech)
        if digits:
            try:
                return int(digits)
            except ValueError:
                return None
        words = _NUMBER_WORDS.get(self.language) or _NUMBER_WORDS["en"]
        tokens = re.findall(r"[\w\u0900-\u097F\u0C00-\u0C7F]+", speech.lower())
        total = None
        for tok in tokens:
            if tok in words:
                total = (total or 0) + words[tok]
        return total
