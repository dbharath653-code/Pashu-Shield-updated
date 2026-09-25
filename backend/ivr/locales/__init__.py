"""
Localization system for IVR prompts.
Loads JSON files per language, with fallback to English.
"""
import os
import json

LOCALES_DIR = os.path.join(os.path.dirname(__file__))
SUPPORTED = ["en", "te", "hi", "mr"]
_cache = {}

def load_locale(lang: str) -> dict:
    lang = (lang or "en").lower()
    if lang not in SUPPORTED:
        lang = "en"
    if lang in _cache:
        return _cache[lang]
    path = os.path.join(LOCALES_DIR, f"{lang}.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            _cache[lang] = data
            return data
    except Exception:
        # fallback to en
        if lang != "en":
            return load_locale("en")
        return {}

def t(key: str, lang: str = "en", **kwargs) -> str:
    """Translate key for lang, with optional format kwargs."""
    locale = load_locale(lang)
    text = locale.get(key, "")
    if not text:
        # fallback to en
        if lang != "en":
            locale_en = load_locale("en")
            text = locale_en.get(key, key)
        else:
            text = key
    if kwargs:
        try:
            text = text.format(**kwargs)
        except Exception:
            pass
    return text

def get_all_prompts(lang: str = "en") -> dict:
    return load_locale(lang)

def list_languages():
    return SUPPORTED
