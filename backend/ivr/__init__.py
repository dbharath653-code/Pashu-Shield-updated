"""
PashuMitra IVR Reporting Channel
================================

An ADDITIVE reporting channel for the existing PashuMitra / Pashu Shield
platform. It turns an ordinary inbound phone call into a normal application
record (Animal -> Case -> Veterinary -> Laboratory -> Government -> GIS/ML).

Design rules honoured here:
  * Nothing in the existing application is re-implemented. Existing
    users / animals / cases / notifications / audit tables are reused.
  * No credential, phone number or provider is hardcoded - everything is
    environment driven (see ivr/config.py).
  * No fake GPS, no fake telephony, no fake AI. Anything that cannot be
    done with the configured infrastructure is recorded as
    NOT_AVAILABLE / BLOCKED rather than invented.

Package layout
--------------
    config.py        environment driven settings
    schema.py        additive SQL DDL + idempotent migrations
    locales/         prompt catalogue (en, hi, te, mr)
    security.py      phone normalisation/encryption, webhook signatures,
                     rate limiting, signed location tokens
    providers/       telephony provider abstraction (Twilio, Exotel,
                     Plivo, custom) - markup + REST clients
    survey.py        configurable multilingual survey engine
    stt.py           speech-to-text abstraction
    ai_pipeline.py   structured, non-hallucinating summarisation
    rules.py         urgency escalation, duplicate detection, vet assignment
    location.py      legitimate location capture strategies
    jobs.py          durable async job queue + worker
    report_service.py survey/call -> structured report -> existing backend
    service.py       IVR call state machine
    routes.py        webhooks + authenticated APIs
    worker.py        `python -m ivr.worker` entry point
"""

__version__ = "1.0.0"
