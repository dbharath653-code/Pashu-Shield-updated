"""
Default multilingual livestock-health survey.

The question set is DATA, not code: it is stored in `ivr_surveys.definition`
on first boot and can be edited afterwards by an authorised administrator
through PUT /api/ivr/survey. Nothing here is hardcoded in the call flow.

Question schema
---------------
key            unique id, also used for duplicate detection
type           menu | numeric | boolean | speech | phone
text           {lang: prompt}
help           {lang: extra DTMF help} (optional)
map_to         dotted path in the structured report
required       bool
confirm        bool - read the answer back and ask for confirmation
allow_unknown  bool - adds a "don't know" DTMF option
options        [{digit, value, labels:{lang: label}}]
speech_map     {lang: {canonical_value: [keywords]}} for speech normalisation
conditional    {depends_on: key, in: [values]} - asked only when it matches
"""

YES_NO = [
    {"digit": "1", "value": "yes", "labels": {"en": "Yes", "hi": "हाँ", "te": "అవును", "mr": "हो"}},
    {"digit": "2", "value": "no", "labels": {"en": "No", "hi": "नहीं", "te": "కాదు", "mr": "नाही"}},
]

YES_NO_UNKNOWN = YES_NO + [
    {"digit": "3", "value": "unknown", "labels": {"en": "Don't know", "hi": "पता नहीं", "te": "తెలియదు", "mr": "माहित नाही"}},
]

DEFAULT_SURVEY = {
    "code": "default_livestock_health_survey",
    "version": 1,
    "languages": ["en", "hi", "te", "mr"],
    "questions": [
        {
            "key": "farmer_name",
            "type": "speech",
            "required": False,
            "confirm": False,
            "allow_unknown": True,
            "map_to": "farmer.name",
            "text": {
                "en": "Please say your name after the beep. Press 3 if you do not want to give your name.",
                "hi": "कृपया बीप के बाद अपना नाम बोलें। नाम नहीं देना चाहते तो 3 दबाएँ।",
                "te": "దయచేసి బీప్ తర్వాత మీ పేరు చెప్పండి. పేరు ఇవ్వకూడదనుకుంటే 3 నొక్కండి.",
                "mr": "कृपया बीप नंतर तुमचे नाव सांगा. नाव द्यायचे नसल्यास 3 दाबा.",
            },
        },
        {
            "key": "species",
            "type": "menu",
            "required": True,
            "confirm": True,
            "map_to": "animal.species",
            "text": {
                "en": "Which animal is sick?",
                "hi": "कौन सा जानवर बीमार है?",
                "te": "ఏ జంతువు అనారోగ్యంగా ఉంది?",
                "mr": "कोणते जनावर आजारी आहे?",
            },
            "options": [
                {"digit": "1", "value": "cattle", "labels": {"en": "Cattle", "hi": "गाय", "te": "ఆవు", "mr": "गाय"}},
                {"digit": "2", "value": "buffalo", "labels": {"en": "Buffalo", "hi": "भैंस", "te": "గేదె", "mr": "म्हैस"}},
                {"digit": "3", "value": "goat", "labels": {"en": "Goat", "hi": "बकरी", "te": "మేక", "mr": "शेळी"}},
                {"digit": "4", "value": "sheep", "labels": {"en": "Sheep", "hi": "भेड़", "te": "గొర్రె", "mr": "मेंढी"}},
                {"digit": "5", "value": "poultry", "labels": {"en": "Poultry", "hi": "मुर्गी", "te": "కోడి", "mr": "कोंबडी"}},
                {"digit": "6", "value": "pig", "labels": {"en": "Pig", "hi": "सूअर", "te": "పంది", "mr": "डुक्कर"}},
                {"digit": "7", "value": "other", "labels": {"en": "Other", "hi": "अन्य", "te": "ఇతర", "mr": "इतर"}},
            ],
            "speech_map": {
                "en": {"cattle": ["cattle", "cow", "bull", "ox", "calf", "heifer"],
                       "buffalo": ["buffalo", "she buffalo", "murrah"],
                       "goat": ["goat", "she goat", "buck"],
                       "sheep": ["sheep", "ram", "ewe"],
                       "poultry": ["poultry", "chicken", "hen", "bird", "duck"],
                       "pig": ["pig", "swine", "boar"]},
                "hi": {"cattle": ["गाय", "गौ", "बैल", "बछड़ा"], "buffalo": ["भैंस"],
                       "goat": ["बकरी"], "sheep": ["भेड़"], "poultry": ["मुर्गी", "चूज़ा"], "pig": ["सूअर"]},
                "te": {"cattle": ["ఆవు", "ఎద్దు"], "buffalo": ["గేదె"], "goat": ["మేక"],
                       "sheep": ["గొర్రె"], "poultry": ["కోడి"], "pig": ["పంది"]},
                "mr": {"cattle": ["गाय", "बैल"], "buffalo": ["म्हैस"], "goat": ["शेळी"],
                       "sheep": ["मेंढी"], "poultry": ["कोंबडी"], "pig": ["डुक्कर"]},
            },
        },
        {
            "key": "animal_count",
            "type": "numeric",
            "required": True,
            "confirm": True,
            "max_digits": 3,
            "map_to": "animal.count",
            "text": {
                "en": "How many animals are sick? Press the number and then hash.",
                "hi": "कितने जानवर बीमार हैं? संख्या दबाएँ और फिर हैश दबाएँ।",
                "te": "ఎన్ని జంతువులు అనారోగ్యంగా ఉన్నాయి? సంఖ్య నొక్కి హాష్ నొక్కండి.",
                "mr": "किती जनावरे आजारी आहेत? संख्या दाबा आणि नंतर हॅश दाबा.",
            },
        },
        {
            "key": "breed",
            "type": "speech",
            "required": False,
            "confirm": False,
            "allow_unknown": True,
            "map_to": "animal.breed",
            "text": {
                "en": "Say the breed of the animal, for example Gir or Murrah. Press 3 if you do not know.",
                "hi": "जानवर की नस्ल बताएँ, जैसे गिर या मुर्रा। पता नहीं तो 3 दबाएँ।",
                "te": "జంతువు జాతిని చెప్పండి, ఉదాహరణకు గిర్ లేదా ముర్రా. తెలియకపోతే 3 నొక్కండి.",
                "mr": "जनावराची जात सांगा, उदा. गीर किंवा मुर्रा. माहित नसल्यास 3 दाबा.",
            },
        },
        {
            "key": "age",
            "type": "numeric",
            "required": False,
            "confirm": False,
            "max_digits": 2,
            "allow_unknown": True,
            "map_to": "animal.age",
            "text": {
                "en": "How old is the animal, in years? Press the number and hash, or press 3 if you do not know.",
                "hi": "जानवर की उम्र कितने वर्ष है? संख्या दबाकर हैश दबाएँ, पता नहीं तो 3 दबाएँ।",
                "te": "జంతువు వయస్సు ఎన్ని సంవత్సరాలు? సంఖ్య నొక్కి హాష్ నొక్కండి, తెలియకపోతే 3 నొక్కండి.",
                "mr": "जनावराचे वय किती वर्षे? संख्या दाबून हॅश दाबा, माहित नसल्यास 3 दाबा.",
            },
        },
        {
            "key": "sex",
            "type": "menu",
            "required": False,
            "confirm": False,
            "map_to": "animal.sex",
            "options": [
                {"digit": "1", "value": "female", "labels": {"en": "Female", "hi": "मादा", "te": "ఆడ", "mr": "मादी"}},
                {"digit": "2", "value": "male", "labels": {"en": "Male", "hi": "नर", "te": "మగ", "mr": "नर"}},
                {"digit": "3", "value": "unknown", "labels": {"en": "Don't know", "hi": "पता नहीं", "te": "తెలియదు", "mr": "माहित नाही"}},
            ],
            "text": {
                "en": "Is the animal female or male?",
                "hi": "जानवर मादा है या नर?",
                "te": "జంతువు ఆడదా లేదా మగదా?",
                "mr": "जनावर मादी आहे की नर?",
            },
        },
        {
            "key": "main_problem",
            "type": "menu",
            "required": True,
            "confirm": True,
            "map_to": "problem",
            "text": {
                "en": "What is the main problem with the animal?",
                "hi": "जानवर की मुख्य समस्या क्या है?",
                "te": "జంతువుకు ప్రధాన సమస్య ఏమిటి?",
                "mr": "जनावराची मुख्य समस्या काय आहे?",
            },
            "options": [
                {"digit": "1", "value": "fever", "labels": {"en": "Fever", "hi": "बुखार", "te": "జ్వరం", "mr": "ताप"}},
                {"digit": "2", "value": "not_eating", "labels": {"en": "Not eating", "hi": "खाना कम या बंद", "te": "తినడం తగ్గింది", "mr": "खाणे कमी"}},
                {"digit": "3", "value": "swelling", "labels": {"en": "Swelling", "hi": "सूजन", "te": "వాపు", "mr": "सूज"}},
                {"digit": "4", "value": "diarrhoea", "labels": {"en": "Diarrhoea", "hi": "दस्त", "te": "విరేచనాలు", "mr": "जुलाब"}},
                {"digit": "5", "value": "breathing", "labels": {"en": "Cough or breathing trouble", "hi": "खांसी या सांस की तकलीफ", "te": "దగ్గు లేదా శ్వాస ఇబ్బంది", "mr": "खोकला किंवा श्वासाचा त्रास"}},
                {"digit": "6", "value": "lameness", "labels": {"en": "Lameness", "hi": "लंगड़ाना", "te": "కుంటుతనం", "mr": "लंगडणे"}},
                {"digit": "7", "value": "milk_drop", "labels": {"en": "Drop in milk", "hi": "दूध कम होना", "te": "పాలు తగ్గడం", "mr": "दूध कमी"}},
                {"digit": "8", "value": "wound", "labels": {"en": "Wound or injury", "hi": "घाव या चोट", "te": "గాయం", "mr": "जखम"}},
                {"digit": "9", "value": "other", "labels": {"en": "Other", "hi": "अन्य", "te": "ఇతర", "mr": "इतर"}},
            ],
        },
        {
            "key": "symptoms",
            "type": "speech",
            "required": True,
            "confirm": False,
            "map_to": "symptoms",
            "text": {
                "en": "Please describe what you see. For example, drooling, running nose, blood in dung, or shaking.",
                "hi": "कृपया बताएँ आप क्या देख रहे हैं। जैसे लार टपकना, नाक बहना, मल में खून, या काँपना।",
                "te": "మీరు ఏమి గమనిస్తున్నారో చెప్పండి. ఉదాహరణకు లాలాజలం కారడం, ముక్కు కారడం, పేడలో రక్తం, వణుకు.",
                "mr": "तुम्हाला काय दिसते ते सांगा. उदा. लाळ गळणे, नाक वाहणे, शेणात रक्त, थरथर.",
            },
        },
        {
            "key": "duration",
            "type": "menu",
            "required": True,
            "confirm": True,
            "map_to": "duration",
            "text": {
                "en": "Since when is the animal sick?",
                "hi": "जानवर कब से बीमार है?",
                "te": "జంతువు ఎప్పటి నుండి అనారోగ్యంగా ఉంది?",
                "mr": "जनावर कधीपासून आजारी आहे?",
            },
            "options": [
                {"digit": "1", "value": "today", "labels": {"en": "Since today", "hi": "आज से", "te": "ఈ రోజు నుండి", "mr": "आजपासून"}},
                {"digit": "2", "value": "1-3 days", "labels": {"en": "One to three days", "hi": "एक से तीन दिन", "te": "ఒకటి నుండి మూడు రోజులు", "mr": "एक ते तीन दिवस"}},
                {"digit": "3", "value": "4-7 days", "labels": {"en": "Four to seven days", "hi": "चार से सात दिन", "te": "నాలుగు నుండి ఏడు రోజులు", "mr": "चार ते सात दिवस"}},
                {"digit": "4", "value": "more than a week", "labels": {"en": "More than a week", "hi": "एक सप्ताह से अधिक", "te": "ఒక వారం కంటే ఎక్కువ", "mr": "एक आठवड्यापेक्षा जास्त"}},
                {"digit": "5", "value": "unknown", "labels": {"en": "Don't know", "hi": "पता नहीं", "te": "తెలియదు", "mr": "माहित नाही"}},
            ],
        },
        {
            "key": "severity",
            "type": "menu",
            "required": True,
            "confirm": True,
            "map_to": "severity",
            "text": {
                "en": "How serious is the problem?",
                "hi": "समस्या कितनी गंभीर है?",
                "te": "సమస్య ఎంత తీవ్రంగా ఉంది?",
                "mr": "समस्या किती गंभीर आहे?",
            },
            "options": [
                {"digit": "1", "value": "mild", "labels": {"en": "Mild", "hi": "हल्का", "te": "తేలికపాటి", "mr": "सौम्य"}},
                {"digit": "2", "value": "moderate", "labels": {"en": "Moderate", "hi": "मध्यम", "te": "మధ్యస్థ", "mr": "मध्यम"}},
                {"digit": "3", "value": "severe", "labels": {"en": "Severe", "hi": "गंभीर", "te": "తీవ్రమైన", "mr": "तीव्र"}},
                {"digit": "4", "value": "animal cannot stand", "labels": {"en": "Animal cannot stand", "hi": "जानवर खड़ा नहीं हो पा रहा", "te": "జంతువు నిలబడలేకపోతోంది", "mr": "जनावर उभे राहू शकत नाही"}},
            ],
        },
        {
            "key": "eating",
            "type": "boolean",
            "required": True,
            "confirm": False,
            "map_to": "eating",
            "options": YES_NO,
            "text": {
                "en": "Is the animal eating?",
                "hi": "क्या जानवर खा रहा है?",
                "te": "జంతువు తింటుందా?",
                "mr": "जनावर खात आहे का?",
            },
        },
        {
            "key": "drinking",
            "type": "boolean",
            "required": True,
            "confirm": False,
            "map_to": "drinking",
            "options": YES_NO,
            "text": {
                "en": "Is the animal drinking water?",
                "hi": "क्या जानवर पानी पी रहा है?",
                "te": "జంతువు నీళ్లు తాగుతుందా?",
                "mr": "जनावर पाणी पित आहे का?",
            },
        },
        {
            "key": "temperature",
            "type": "numeric",
            "required": False,
            "confirm": False,
            "max_digits": 3,
            "allow_unknown": True,
            "map_to": "temperature",
            "text": {
                "en": "If you measured the temperature, press the number in Fahrenheit and hash. Press 3 if not measured.",
                "hi": "यदि आपने तापमान मापा है, तो फ़ारेनहाइट में संख्या दबाएँ और हैश दबाएँ। नहीं मापा तो 3 दबाएँ।",
                "te": "ఉష్ణోగ్రత కొలిచి ఉంటే ఫారెన్‌హీట్‌లో సంఖ్య నొక్కి హాష్ నొక్కండి. కొలవకపోతే 3 నొక్కండి.",
                "mr": "तापमान मोजले असल्यास फॅरनहाइटमध्ये संख्या दाबा व हॅश दाबा. मोजले नसल्यास 3 दाबा.",
            },
        },
        {
            "key": "vaccination_status",
            "type": "menu",
            "required": False,
            "confirm": False,
            "map_to": "vaccination_status",
            "options": YES_NO_UNKNOWN,
            "text": {
                "en": "Has the animal been vaccinated?",
                "hi": "क्या जानवर का टीकाकरण हुआ है?",
                "te": "జంతువుకు టీకాలు వేశారా?",
                "mr": "जनावराचे लसीकरण झाले आहे का?",
            },
        },
        {
            "key": "previous_disease",
            "type": "boolean",
            "required": False,
            "confirm": False,
            "map_to": "previous_disease",
            "options": YES_NO_UNKNOWN,
            "text": {
                "en": "Has the animal had a similar illness before?",
                "hi": "क्या जानवर को पहले भी ऐसी बीमारी हुई है?",
                "te": "జంతువుకు ఇంతకు ముందు ఇలాంటి వ్యాధి వచ్చిందా?",
                "mr": "जनावराला यापूर्वी असा आजार झाला होता का?",
            },
        },
        {
            "key": "treatment_given",
            "type": "boolean",
            "required": False,
            "confirm": False,
            "map_to": "previous_treatment",
            "options": YES_NO_UNKNOWN,
            "text": {
                "en": "Have you already given any medicine or treatment?",
                "hi": "क्या आपने पहले से कोई दवा या उपचार दिया है?",
                "te": "మీరు ఇప్పటికే ఏదైనా మందు లేదా చికిత్స ఇచ్చారా?",
                "mr": "तुम्ही आधीच काही औषध किंवा उपचार दिले आहे का?",
            },
        },
        {
            "key": "treatment_detail",
            "type": "speech",
            "required": False,
            "confirm": False,
            "allow_unknown": True,
            "map_to": "previous_treatment_detail",
            "conditional": {"depends_on": "treatment_given", "in": ["yes"]},
            "text": {
                "en": "Please say which medicine you gave.",
                "hi": "कृपया बताएँ आपने कौन सी दवा दी।",
                "te": "మీరు ఇచ్చిన మందు పేరు చెప్పండి.",
                "mr": "तुम्ही कोणते औषध दिले ते सांगा.",
            },
        },
        {
            "key": "pregnancy_status",
            "type": "menu",
            "required": False,
            "confirm": False,
            "map_to": "pregnancy_status",
            "conditional": {"depends_on": "sex", "in": ["female", "unknown"]},
            "text": {
                "en": "Is the animal pregnant?",
                "hi": "क्या जानवर गर्भवती है?",
                "te": "జంతువు గర్భిణా?",
                "mr": "जनावर गरोदर आहे का?",
            },
            "options": [
                {"digit": "1", "value": "pregnant", "labels": {"en": "Pregnant", "hi": "गर्भवती", "te": "గర్భిణి", "mr": "गरोदर"}},
                {"digit": "2", "value": "not pregnant", "labels": {"en": "Not pregnant", "hi": "गर्भवती नहीं", "te": "గర్భిణి కాదు", "mr": "गरोदर नाही"}},
                {"digit": "3", "value": "unknown", "labels": {"en": "Don't know", "hi": "पता नहीं", "te": "తెలియదు", "mr": "माहित नाही"}},
                {"digit": "4", "value": "recently delivered", "labels": {"en": "Recently delivered", "hi": "हाल में ब्याही", "te": "ఇటీవల ఈనింది", "mr": "नुकतेच व्यायले"}},
            ],
        },
        {
            "key": "other_animals_affected",
            "type": "numeric",
            "required": False,
            "confirm": False,
            "max_digits": 3,
            "map_to": "other_animals_affected",
            "text": {
                "en": "How many other animals in your farm have the same problem? Press zero if none.",
                "hi": "आपके खेत में कितने अन्य जानवरों में यही समस्या है? कोई नहीं तो शून्य दबाएँ।",
                "te": "మీ పొలంలో ఎన్ని ఇతర జంతువులకు ఇదే సమస్య ఉంది? ఎవరికీ లేకపోతే సున్నా నొక్కండి.",
                "mr": "तुमच्या शेतात इतर किती जनावरांना हीच समस्या आहे? कोणालाच नसल्यास शून्य दाबा.",
            },
        },
        {
            "key": "village",
            "type": "speech",
            "required": True,
            "confirm": True,
            "map_to": "location.village",
            "text": {
                "en": "Which village or locality is your farm in? Please say the name after the beep.",
                "hi": "आपका खेत किस गाँव या मोहल्ले में है? कृपया बीप के बाद नाम बोलें।",
                "te": "మీ పొలం ఏ గ్రామంలో ఉంది? దయచేసి బీప్ తర్వాత పేరు చెప్పండి.",
                "mr": "तुमचे शेत कोणत्या गावात आहे? कृपया बीप नंतर नाव सांगा.",
            },
        },
        {
            "key": "district",
            "type": "speech",
            "required": True,
            "confirm": True,
            "map_to": "location.district",
            "text": {
                "en": "Which district is your village in? Please say the district name after the beep.",
                "hi": "आपका गाँव किस ज़िले में है? कृपया बीप के बाद ज़िले का नाम बोलें।",
                "te": "మీ గ్రామం ఏ జిల్లాలో ఉంది? దయచేసి బీప్ తర్వాత జిల్లా పేరు చెప్పండి.",
                "mr": "तुमचे गाव कोणत्या जिल्ह्यात आहे? कृपया बीप नंतर जिल्ह्याचे नाव सांगा.",
            },
        },
        {
            "key": "state",
            "type": "menu",
            "required": True,
            "confirm": False,
            "map_to": "location.state",
            "text": {
                "en": "Which state is your farm in?",
                "hi": "आपका खेत किस राज्य में है?",
                "te": "మీ పొలం ఏ రాష్ట్రంలో ఉంది?",
                "mr": "तुमचे शेत कोणत्या राज्यात आहे?",
            },
            "options": [
                {"digit": "1", "value": "Maharashtra", "labels": {"en": "Maharashtra", "hi": "महाराष्ट्र", "te": "మహారాష్ట్ర", "mr": "महाराष्ट्र"}},
                {"digit": "2", "value": "Telangana", "labels": {"en": "Telangana", "hi": "तेलंगाना", "te": "తెలంగాణ", "mr": "तेलंगणा"}},
                {"digit": "3", "value": "Andhra Pradesh", "labels": {"en": "Andhra Pradesh", "hi": "आंध्र प्रदेश", "te": "ఆంధ్ర ప్రదేశ", "mr": "आंध्र प्रदेश"}},
                {"digit": "4", "value": "Karnataka", "labels": {"en": "Karnataka", "hi": "कर्नाटक", "te": "కర్ణాటక", "mr": "कर्नाटक"}},
                {"digit": "5", "value": "other", "labels": {"en": "Other state, say the name", "hi": "अन्य राज्य, नाम बोलें", "te": "ఇతర రాష్ట్రం, పేరు చెప్పండి", "mr": "इतर राज्य, नाव सांगा"}},
            ],
        },
        {
            "key": "additional_notes",
            "type": "speech",
            "required": False,
            "confirm": False,
            "allow_unknown": True,
            "map_to": "additional_description",
            "text": {
                "en": "If you want to add anything else, please say it now. Press 3 to finish.",
                "hi": "कुछ और बताना चाहते हैं तो अब बोलें। समाप्त करने के लिए 3 दबाएँ।",
                "te": "ఇంకేమైనా చెప్పాలనుకుంటే ఇప్పుడు చెప్పండి. ముగించడానికి 3 నొక్కండి.",
                "mr": "काही आणखी सांगायचे असल्यास आता सांगा. संपविण्यासाठी 3 दाबा.",
            },
        },
    ],
}
