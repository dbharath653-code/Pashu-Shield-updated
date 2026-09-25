"""
Localisation layer for the IVR channel.

Nothing language specific is hardcoded anywhere else in the IVR code: every
prompt, instruction and error message is resolved through `t(lang, key)`.

Adding a language = drop a new module in this package exporting CODE, META
and PROMPTS, then add its code to IVR_SUPPORTED_LANGUAGES.
"""
import importlib
import os

from ..config import settings

_REGISTRY = {}
_LOADED = False


def register(module_name):
    mod = importlib.import_module(f".{module_name}", __name__)
    _REGISTRY[mod.CODE] = {"meta": mod.META, "prompts": mod.PROMPTS}
    return mod


def load():
    global _LOADED
    if _LOADED:
        return
    for name in ("en", "hi", "te", "mr"):
        try:
            register(name)
        except Exception:  # pragma: no cover - a broken locale must not kill the IVR
            continue
    _LOADED = True


load()


def supported_language_codes():
    """Configured languages (env), limited to locales that are implemented."""
    load()
    wanted = [c for c in (settings.SUPPORTED_LANGUAGES or []) if c in _REGISTRY]
    return wanted or [settings.DEFAULT_LANGUAGE if settings.DEFAULT_LANGUAGE in _REGISTRY else "en"]


def available_locales():
    load()
    return sorted(_REGISTRY.keys())


def get_locale(lang):
    load()
    return _REGISTRY.get(lang) or _REGISTRY.get(settings.DEFAULT_LANGUAGE) or _REGISTRY["en"]


def t(lang, key, **fmt):
    """Translate `key` for `lang`, falling back to English."""
    load()
    entry = _REGISTRY.get(lang) or _REGISTRY.get("en")
    text = (entry["prompts"].get(key) if entry else None)
    if text is None:
        text = _REGISTRY["en"]["prompts"].get(key, key)
    if fmt:
        try:
            text = text.format(**fmt)
        except Exception:
            pass
    return text


def meta(lang):
    load()
    entry = _REGISTRY.get(lang) or _REGISTRY["en"]
    return entry["meta"]


def tts_voice(lang, provider):
    """Resolve the TTS voice for a language/provider, honouring env overrides."""
    load()
    m = meta(lang)
    env_key = f"IVR_TTS_VOICE_{lang.upper()}"
    override = os.environ.get(env_key, "").strip()
    if override:
        return override, m["tts"].get("language", "en-IN")
    return m["tts"].get(provider) or m["tts"].get("twilio"), m["tts"].get("language", "en-IN")


def stt_language(lang):
    return meta(lang).get("stt_language", "en-IN")


def speech_hints(lang, extra=None):
    hints = list(meta(lang).get("speech_hints", []))
    if extra:
        hints.extend([h for h in extra if h not in hints])
    return hints[:100]


def language_menu_prompts(languages=None):
    """One prompt per language, each spoken in its own language."""
    load()
    codes = languages or supported_language_codes()
    return [(code, t(code, "language_option")) for code in codes if code in _REGISTRY]


def dtmf_to_language(codes=None):
    load()
    codes = codes or supported_language_codes()
    return {meta(c).get("dtmf"): c for c in codes if c in _REGISTRY}
