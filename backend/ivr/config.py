"""
Environment driven configuration for the IVR channel.

SECURITY: no secret, phone number, provider name or AI key is stored in
source. Everything is read from the process environment (Render / systemd /
docker env). `masked()` returns a safe view for the admin UI and health
endpoint - it never leaks credentials.
"""
import os

_TRUTHY = ("1", "true", "yes", "on", "y", "t")
_FALSY = ("0", "false", "no", "off", "n", "f")

# Keys that must never be rendered back to a client, even partially.
SECRET_KEYS = {
    "TELEPHONY_AUTH_TOKEN",
    "TELEPHONY_API_KEY",
    "TELEPHONY_API_SECRET",
    "TELEPHONY_WEBHOOK_SECRET",
    "IVR_AI_API_KEY",
    "IVR_STT_API_KEY",
    "IVR_PHONE_ENCRYPTION_KEY",
    "IVR_LOCATION_TOKEN_SECRET",
    "SIH_SECRET_KEY",
}


def env_bool(name, default=False):
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    v = str(raw).strip().lower()
    if v in _TRUTHY:
        return True
    if v in _FALSY:
        return False
    return default


def env_int(name, default):
    raw = os.environ.get(name)
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def env_float(name, default):
    raw = os.environ.get(name)
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return default


def env_list(name, default):
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return list(default)
    return [p.strip() for p in str(raw).split(",") if p.strip()]


class Settings:
    """Resolved IVR configuration (env first, DB `ivr_config` overrides)."""

    # --------------------------------------------------------- identity ---
    IVR_PHONE_NUMBER = os.environ.get("IVR_PHONE_NUMBER", "").strip()
    PUBLIC_BASE_URL = os.environ.get("IVR_PUBLIC_BASE_URL", "").strip().rstrip("/")
    ENABLED = env_bool("IVR_ENABLED", True)

    # -------------------------------------------------------- telephony ---
    PROVIDER = (os.environ.get("IVR_PROVIDER", "") or "").strip().lower()  # twilio|exotel|plivo|custom|none
    TELEPHONY_ACCOUNT_ID = os.environ.get("TELEPHONY_ACCOUNT_ID", "").strip()
    TELEPHONY_AUTH_TOKEN = os.environ.get("TELEPHONY_AUTH_TOKEN", "").strip()
    TELEPHONY_API_KEY = os.environ.get("TELEPHONY_API_KEY", "").strip()
    TELEPHONY_API_SECRET = os.environ.get("TELEPHONY_API_SECRET", "").strip()
    TELEPHONY_PHONE_NUMBER = os.environ.get("TELEPHONY_PHONE_NUMBER", "").strip()
    TELEPHONY_SID = os.environ.get("TELEPHONY_SID", "").strip()          # Exotel: account SID / Plivo: auth id
    TELEPHONY_SUBDOMAIN = os.environ.get("TELEPHONY_SUBDOMAIN", "").strip()  # Exotel
    TELEPHONY_APP_ID = os.environ.get("TELEPHONY_APP_ID", "").strip()    # Exotel / custom
    TELEPHONY_BASE_URL = os.environ.get("TELEPHONY_BASE_URL", "").strip()  # custom provider REST base
    TELEPHONY_WEBHOOK_SECRET = os.environ.get("TELEPHONY_WEBHOOK_SECRET", "").strip()
    TELEPHONY_MARKUP = (os.environ.get("TELEPHONY_MARKUP", "twilio_xml")).strip()

    # -------------------------------------------------------- languages ---
    SUPPORTED_LANGUAGES = env_list("IVR_SUPPORTED_LANGUAGES", ["en", "te", "hi", "mr"])
    DEFAULT_LANGUAGE = (os.environ.get("IVR_DEFAULT_LANGUAGE", "en") or "en").strip()

    # -------------------------------------------------------- recording ---
    RECORDING_ENABLED = env_bool("IVR_RECORDING_ENABLED", False)
    RECORDING_CONSENT_REQUIRED = env_bool("IVR_RECORDING_CONSENT_REQUIRED", True)
    RECORDING_STORAGE = (os.environ.get("IVR_RECORDING_STORAGE", "provider")).strip()  # provider|local
    RECORDING_DIR = os.environ.get("IVR_RECORDING_DIR", os.path.join(os.path.dirname(__file__), "..", "recordings"))
    RECORDING_RETENTION_DAYS = env_int("IVR_RECORDING_RETENTION_DAYS", 180)
    RECORDING_ROLES = env_list("IVR_RECORDING_ROLES", ["govt", "vet"])

    # -------------------------------------------------------------- AI ----
    AI_PROVIDER = (os.environ.get("IVR_AI_PROVIDER", "none")).strip().lower()  # none|openai_compatible
    AI_BASE_URL = os.environ.get("IVR_AI_BASE_URL", "https://api.openai.com/v1").strip()
    AI_API_KEY = os.environ.get("IVR_AI_API_KEY", "").strip()
    AI_MODEL = os.environ.get("IVR_AI_MODEL", "gpt-4o-mini").strip()
    AI_TIMEOUT = env_int("IVR_AI_TIMEOUT", 30)
    AI_MAX_TRANSCRIPT_CHARS = env_int("IVR_AI_MAX_TRANSCRIPT_CHARS", 12000)

    # ------------------------------------------------------------- STT ----
    STT_PROVIDER = (os.environ.get("IVR_STT_PROVIDER", "none")).strip().lower()  # none|provider_native|whisper_http|openai_compatible
    STT_WHISPER_URL = os.environ.get("IVR_STT_WHISPER_URL", "").strip()
    STT_BASE_URL = os.environ.get("IVR_STT_BASE_URL", "https://api.openai.com/v1").strip()
    STT_API_KEY = os.environ.get("IVR_STT_API_KEY", "").strip()
    STT_MODEL = os.environ.get("IVR_STT_MODEL", "whisper-1").strip()
    STT_TIMEOUT = env_int("IVR_STT_TIMEOUT", 120)
    STT_MIN_CONFIDENCE = env_float("IVR_STT_MIN_CONFIDENCE", 0.5)

    # --------------------------------------------------------- security ---
    PHONE_ENCRYPTION_KEY = os.environ.get("IVR_PHONE_ENCRYPTION_KEY", "").strip()
    PHONE_HASH_SECRET = os.environ.get("IVR_PHONE_HASH_SECRET", "").strip()
    LOCATION_TOKEN_SECRET = os.environ.get("IVR_LOCATION_TOKEN_SECRET", "").strip()
    WEBHOOK_REPLAY_WINDOW = env_int("IVR_WEBHOOK_REPLAY_WINDOW_SECONDS", 600)
    RATE_LIMIT_PER_MINUTE = env_int("IVR_RATE_LIMIT_PER_MINUTE", 120)
    REQUIRE_WEBHOOK_SIGNATURE = env_bool("IVR_REQUIRE_WEBHOOK_SIGNATURE", True)

    # ------------------------------------------------------ roles / ACL ---
    ADMIN_ROLES = env_list("IVR_ADMIN_ROLES", ["govt"])
    VIEW_ROLES = env_list("IVR_VIEW_ROLES", ["govt", "vet"])
    MERGE_ROLES = env_list("IVR_MERGE_ROLES", ["govt", "vet"])

    # ----------------------------------------------------------- timing ---
    GATHER_TIMEOUT = env_int("IVR_GATHER_TIMEOUT_SECONDS", 6)
    SPEECH_TIMEOUT = env_int("IVR_SPEECH_TIMEOUT_SECONDS", 6)
    MAX_RETRIES = env_int("IVR_MAX_RETRIES", 2)
    VET_RING_TIMEOUT = env_int("IVR_VET_RING_TIMEOUT_SECONDS", 30)
    VET_AVAILABLE_FROM = os.environ.get("IVR_VET_AVAILABLE_FROM", "08:00").strip()
    VET_AVAILABLE_TO = os.environ.get("IVR_VET_AVAILABLE_TO", "20:00").strip()

    # ------------------------------------------------------------- jobs ---
    WORKER_ENABLED = env_bool("IVR_WORKER_ENABLED", True)
    WORKER_THREADS = env_int("IVR_WORKER_THREADS", 2)
    WORKER_POLL_SECONDS = env_float("IVR_WORKER_POLL_SECONDS", 2.0)
    JOB_MAX_ATTEMPTS = env_int("IVR_JOB_MAX_ATTEMPTS", 4)
    JOB_BACKOFF_SECONDS = env_int("IVR_JOB_BACKOFF_SECONDS", 15)

    # ---------------------------------------------------------- reports ---
    DUPLICATE_WINDOW_MINUTES = env_int("IVR_DUPLICATE_WINDOW_MINUTES", 1440)
    NOTIFY_GOV_USERS = env_bool("IVR_NOTIFY_GOV_USERS", True)
    AUTO_ASSIGN_VET = env_bool("IVR_AUTO_ASSIGN_VET", True)
    CALLBACK_ENABLED = env_bool("IVR_CALLBACK_ENABLED", False)
    CALLBACK_COOLDOWN_MINUTES = env_int("IVR_CALLBACK_COOLDOWN_MINUTES", 180)

    # --------------------------------------------------------- location ---
    LOCATION_LINK_ENABLED = env_bool("IVR_LOCATION_LINK_ENABLED", True)
    LOCATION_LINK_TTL_MINUTES = env_int("IVR_LOCATION_LINK_TTL_MINUTES", 2880)
    SMS_ENABLED = env_bool("IVR_SMS_ENABLED", False)
    DEFAULT_STATE = os.environ.get("IVR_DEFAULT_STATE", "Maharashtra").strip()

    # ------------------------------------------------------------- dev ----
    DEV_MODE = env_bool("IVR_DEV_MODE", False)

    # --------------------------------------------------------- overrides --
    _overrides = {}

    @classmethod
    def apply_overrides(cls, mapping: dict) -> None:
        """Apply administrator managed overrides (from the ivr_config table).

        Only keys already declared on this class may be overridden, and secret
        keys are never accepted from the database (they stay in the
        environment / secret manager).
        """
        clean = {}
        for key, value in (mapping or {}).items():
            if key.startswith("_"):
                continue
            if key in SECRET_KEYS:
                continue
            # Overrides are stored under the environment variable name; the
            # class attribute may drop the IVR_ prefix (DUPLICATE_WINDOW_MINUTES).
            attr = key if hasattr(cls, key) else (
                key[len("IVR_"):] if key.startswith("IVR_") else None
            )
            if attr is None or not hasattr(cls, attr):
                continue
            clean[attr] = value
        cls._overrides = clean
        for key, value in clean.items():
            setattr(cls, key, value)

    @classmethod
    def reset(cls) -> None:
        """Restore the pure environment view (used by tests)."""
        cls._overrides = {}
        for key in list(vars(cls).keys()):
            if key.isupper() and hasattr(cls, key):
                delattr(cls, key)
        # Re-evaluate the class body defaults by reloading the module state.
        import importlib

        module = importlib.import_module(__name__)
        importlib.reload(module)

    @classmethod
    def as_dict(cls, include_secrets: bool = False) -> dict:
        out = {}
        for key, value in vars(cls).items():
            if not key.isupper():
                continue
            if key in SECRET_KEYS and not include_secrets:
                out[key] = "***set***" if value else ""
            else:
                out[key] = value
        return out

    @classmethod
    def masked(cls) -> dict:
        """Safe subset for the admin UI / health endpoint."""
        d = cls.as_dict(include_secrets=False)
        d["_secrets_configured"] = {
            key: bool(getattr(cls, key, "")) for key in sorted(SECRET_KEYS)
        }
        return d


settings = Settings


def reload_settings() -> None:
    """Re-read environment variables (used by tests and config reloads)."""
    import importlib

    module = importlib.import_module(__name__)
    importlib.reload(module)


def load_db_overrides(get_db) -> None:
    """Load administrator managed configuration from the database.

    Fails soft: a broken/empty config table must never stop the IVR channel.
    """
    try:
        conn = get_db()
        rows = conn.execute("SELECT key, value FROM ivr_config").fetchall()
        mapping = {}
        for r in rows:
            try:
                import json

                mapping[r["key"]] = json.loads(r["value"])
            except Exception:
                mapping[r["key"]] = r["value"]
        conn.close()
        Settings.apply_overrides(mapping)
    except Exception:
        # Table not migrated yet, or DB unavailable - env config stands.
        pass
