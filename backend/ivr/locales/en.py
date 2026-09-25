"""English (en) prompt catalogue for the IVR channel."""

CODE = "en"

META = {
    "code": "en",
    "name": "English",
    "native_name": "English",
    "dtmf": "1",
    # Voice names are provider specific defaults; override with
    # IVR_TTS_VOICE_EN / TWILIO_VOICE_EN etc. to match your account.
    "tts": {
        "twilio": "Polly.Aditi",
        "plivo": "Polly.Aditi",
        "exotel": "en-IN-Wavenet-A",
        "language": "en-IN",
    },
    "stt_language": "en-IN",
    "speech_hints": [
        "fever", "not eating", "not drinking", "cough", "swelling",
        "cattle", "buffalo", "goat", "sheep", "poultry", "pig",
        "yes", "no", "veterinarian", "report",
    ],
}

PROMPTS = {
    "welcome": "Welcome to PashuMitra, the livestock health helpline.",
    "language_option": "For English, press 1.",
    "language_selected": "You have selected English.",
    "invalid_language": "Sorry, that language is not available.",

    "main_menu": (
        "Press 1 to speak with a veterinarian. "
        "Press 2 to report an animal health problem. "
        "Press 9 to hear these options again."
    ),
    "invalid_option": "Sorry, that option is not valid. Please press 1 or 2.",

    "vet_connecting": "Please wait. We are connecting you to a veterinarian.",
    "vet_unavailable": (
        "No veterinarian is available right now. "
        "You can report the animal problem through our automated survey. "
        "Your report will be sent to the veterinary and authorized government teams."
    ),
    "vet_no_answer": "The veterinarian could not answer your call. We are starting the automated survey now.",
    "vet_call_ended": "Thank you. Your conversation has been recorded as a veterinary report.",

    "recording_consent": (
        "For quality and record keeping, this call may be recorded. "
        "Press 1 to allow recording. Press 2 to continue without recording. "
        "You will receive the same service either way."
    ),
    "recording_declined": "Okay. This call will not be recorded.",
    "recording_enabled_note": "Recording started.",

    "survey_intro": (
        "We will now ask a few short questions about your animal. "
        "You can answer by pressing keys on your phone, or by speaking after the beep."
    ),
    "survey_complete": (
        "Thank you. Your report has been created. "
        "A veterinarian will contact you soon. Goodbye."
    ),
    "survey_partial": "Thank you. We saved the information you gave. A veterinarian will contact you.",

    "no_input": "We did not receive your answer.",
    "invalid_input": "That answer was not understood.",
    "timeout": "We did not hear anything. Please try again.",
    "error": "We are facing a technical problem. Please call again after some time.",
    "goodbye": "Thank you for calling PashuMitra. Goodbye.",

    "confirm_heard": "You said {value}.",
    "confirm_instruction": "Press 1 to confirm. Press 2 to answer again.",
    "nav_help": "Press 9 to repeat this question. Press 0 for the previous question.",
    "nav_help_star": "Press star to repeat this question. Press 0 for the previous question.",

    "phone_request": (
        "We could not read your phone number. "
        "Please enter your ten digit mobile number and then press the hash key."
    ),
    "phone_invalid": "That number is not valid. Please enter your ten digit mobile number and press hash.",
    "phone_captured": "Thank you. Your number has been saved.",

    "location_sms_sent": (
        "We sent a link to your mobile. "
        "Please open the link and allow location to share your exact farm position."
    ),
    "location_not_available": (
        "Exact location is not available on this call, "
        "so we will record your village and district from your answers."
    ),
    "callback_offered": "Press 1 if you would like a veterinarian to call you back.",

    "not_provided": "Not provided",
    "press_unknown": "Press 3 if you do not know.",
    "yes": "Yes",
    "no": "No",
    "press_yes_no": "Press 1 for yes. Press 2 for no.",
}
