"""
Speech-to-text abstraction for the IVR channel.

Supported back-ends (chosen with IVR_STT_PROVIDER):
  none              - no STT configured: transcription is skipped and the
                      report is generated from the DTMF/survey answers only.
                      (Never faked.)
  provider_native   - the telephony provider posts the transcription to
                      /ivr/transcription (Twilio <Record transcribe=...>).
  whisper_http      - POST the recording to a Whisper / faster-whisper /
                      whisper.cpp HTTP endpoint (IVR_STT_WHISPER_URL). This
                      is the reuse path for existing Whisper infrastructure.
  openai_compatible - POST to an OpenAI compatible /audio/transcriptions API.

If recognition fails or confidence is below IVR_STT_MIN_CONFIDENCE the
caller is asked to repeat - the pipeline never guesses.
"""
import json

import requests

from .config import settings
from .locales import stt_language


class STTError(Exception):
    pass


class STTUnavailable(STTError):
    pass


def is_configured():
    mode = (settings.STT_PROVIDER or "none").lower()
    if mode in ("none", ""):
        return False
    if mode == "provider_native":
        return True
    if mode == "whisper_http":
        return bool(settings.STT_WHISPER_URL)
    if mode == "openai_compatible":
        return bool(settings.STT_BASE_URL and settings.STT_API_KEY)
    return False


def unavailability_reason():
    mode = (settings.STT_PROVIDER or "none").lower()
    if mode in ("none", ""):
        return "IVR_STT_PROVIDER is not set - no speech-to-text backend configured"
    if mode == "whisper_http" and not settings.STT_WHISPER_URL:
        return "IVR_STT_PROVIDER=whisper_http but IVR_STT_WHISPER_URL is empty"
    if mode == "openai_compatible" and not (settings.STT_BASE_URL and settings.STT_API_KEY):
        return "IVR_STT_PROVIDER=openai_compatible but IVR_STT_BASE_URL/IVR_STT_API_KEY are missing"
    return f"unsupported IVR_STT_PROVIDER '{mode}'"


def transcribe(audio_bytes=None, audio_url=None, language="en", filename="recording.mp3",
               content_type="audio/mpeg", provider=None):
    """Transcribe an audio clip.

    Returns {"text": str, "confidence": float|None, "provider": str, "language": str}
    Raises STTUnavailable / STTError - never returns invented text.
    """
    mode = (settings.STT_PROVIDER or "none").lower()
    if not is_configured():
        raise STTUnavailable(unavailability_reason())

    if mode == "provider_native":
        raise STTUnavailable(
            "provider_native transcription is delivered by the provider webhook "
            "(/ivr/transcription); no audio to transcribe locally"
        )

    lang = stt_language(language)

    if mode == "whisper_http":
        return _transcribe_whisper_http(audio_bytes, audio_url, lang, filename, content_type)

    if mode == "openai_compatible":
        return _transcribe_openai(audio_bytes, lang, filename, content_type)

    raise STTUnavailable(unavailability_reason())


def _audio_bytes(audio_bytes, audio_url, timeout):
    if audio_bytes is not None:
        return audio_bytes
    if audio_url:
        resp = requests.get(audio_url, timeout=timeout)
        resp.raise_for_status()
        return resp.content
    raise STTError("no audio provided for transcription")


def _transcribe_whisper_http(audio_bytes, audio_url, lang, filename, content_type, timeout=None):
    timeout = timeout or settings.STT_TIMEOUT
    payload = _audio_bytes(audio_bytes, audio_url, timeout)
    files = {"file": (filename, payload, content_type), "audio": (filename, payload, content_type)}
    data = {"language": lang, "task": "transcribe", "model": settings.STT_MODEL}
    resp = requests.post(settings.STT_WHISPER_URL, files=files, data=data, timeout=timeout)
    resp.raise_for_status()
    try:
        body = resp.json()
    except ValueError:
        return {"text": resp.text.strip(), "confidence": None,
                "provider": "whisper_http", "language": lang}
    text = ""
    if isinstance(body, dict):
        text = body.get("text") or body.get("transcription") or body.get("transcript") or ""
    else:
        text = str(body)
    conf = body.get("confidence") if isinstance(body, dict) else None
    return {"text": str(text).strip(), "confidence": conf, "provider": "whisper_http", "language": lang}


def _transcribe_openai(audio_bytes, lang, filename, content_type, timeout=None):
    timeout = timeout or settings.STT_TIMEOUT
    if audio_bytes is None:
        raise STTError("openai_compatible transcription requires the audio bytes")
    url = f"{settings.STT_BASE_URL.rstrip('/')}/audio/transcriptions"
    headers = {"Authorization": f"Bearer {settings.STT_API_KEY}"}
    files = {"file": (filename, audio_bytes, content_type)}
    data = {"model": settings.STT_MODEL, "language": lang}
    resp = requests.post(url, headers=headers, files=files, data=data, timeout=timeout)
    resp.raise_for_status()
    body = resp.json()
    return {
        "text": (body.get("text") or "").strip(),
        "confidence": body.get("confidence"),
        "provider": "openai_compatible",
        "language": lang,
    }


def store_provider_transcript(conn, call_id, text, language="en", confidence=None,
                              participant_role="FARMER", provenance="FARMER_REPORTED",
                              provider="provider_native"):
    """Persist a transcript delivered by a provider callback."""
    cur = conn.execute(
        "INSERT INTO ivr_transcripts (call_id, participant_role, language, text, stt_provider, "
        "stt_confidence, source, provenance) VALUES (?,?,?,?,?,?,'PROVIDER',?)",
        (call_id, participant_role, language, text, provider, confidence, provenance),
    )
    conn.commit()
    return cur.lastrowid


def get_call_transcripts(conn, call_id):
    rows = conn.execute(
        "SELECT * FROM ivr_transcripts WHERE call_id=? ORDER BY id", (call_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def combined_transcript(conn, call_id):
    rows = get_call_transcripts(conn, call_id)
    parts = []
    for r in rows:
        if not r.get("text"):
            continue
        parts.append(f'[{r.get("participant_role", "FARMER")}] {r["text"]}')
    return "\n".join(parts)
