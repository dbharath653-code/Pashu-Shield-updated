"""
Individual Animal Clinical Decision Support (CDS) Engine.
Provides deterministic, explainable clinical veterinary decision support
incorporating species-specific reference ranges, structured laboratory results,
vaccination gap detection, reproductive status, and symptom pathology signatures.

Strictly adheres to veterinary ethics:
- Never presents output as an autonomous diagnosis
- Mandates veterinary confirmation
- Provides clear explanation factors, confidence, and diagnostic next steps
"""
from datetime import datetime, date, timedelta
import re

VET_REFERENCE_RANGES = {
    "cattle": {
        "temperature_f": {"min": 100.4, "max": 102.8, "unit": "°F"},
        "wbc": {"min": 4.0, "max": 12.0, "unit": "x10^3/uL"},
        "rbc": {"min": 5.0, "max": 10.0, "unit": "x10^6/uL"},
        "hemoglobin": {"min": 8.0, "max": 15.0, "unit": "g/dL"},
        "pcv": {"min": 24.0, "max": 46.0, "unit": "%"},
        "gestation_days": 283,
    },
    "buffalo": {
        "temperature_f": {"min": 100.0, "max": 102.4, "unit": "°F"},
        "wbc": {"min": 5.0, "max": 13.0, "unit": "x10^3/uL"},
        "rbc": {"min": 5.5, "max": 9.5, "unit": "x10^6/uL"},
        "hemoglobin": {"min": 9.0, "max": 14.5, "unit": "g/dL"},
        "pcv": {"min": 26.0, "max": 44.0, "unit": "%"},
        "gestation_days": 310,
    },
    "goat": {
        "temperature_f": {"min": 101.5, "max": 103.5, "unit": "°F"},
        "wbc": {"min": 4.0, "max": 13.0, "unit": "x10^3/uL"},
        "rbc": {"min": 8.0, "max": 18.0, "unit": "x10^6/uL"},
        "hemoglobin": {"min": 8.0, "max": 14.0, "unit": "g/dL"},
        "pcv": {"min": 22.0, "max": 38.0, "unit": "%"},
        "gestation_days": 150,
    },
    "sheep": {
        "temperature_f": {"min": 101.5, "max": 103.5, "unit": "°F"},
        "wbc": {"min": 4.0, "max": 12.0, "unit": "x10^3/uL"},
        "rbc": {"min": 9.0, "max": 15.0, "unit": "x10^6/uL"},
        "hemoglobin": {"min": 9.0, "max": 15.0, "unit": "g/dL"},
        "pcv": {"min": 27.0, "max": 45.0, "unit": "%"},
        "gestation_days": 147,
    },
}

DISEASE_SIGNATURES = [
    {
        "disease": "Haemorrhagic Septicaemia (HS)",
        "keywords": ["fever", "eating", "lethargic", "salivation", "throat", "swelling", "submandibular", "dyspnea", "respiratory", "grunting"],
        "weight": 25,
        "category": "Acute Bacterial Septicemia (Suspected HS)",
        "priority": "HIGH",
        "vaccine_key": "HS",
        "diagnostic_steps": [
            "Collect jugular blood sample in EDTA & sterile clot activator prior to antimicrobial therapy",
            "Perform peripheral blood smear for bipolar-staining Pasteurella multocida (Leishman/Giemsa)",
            "Measure rectal temperature twice daily across all in-contact herd members",
            "Isolate affected animal in a dry, sheltered isolation pen"
        ],
        "follow_up": [
            "Re-assess respiratory effort and swelling every 6 hours",
            "Screen surrounding herd animals for early febrile spike (>103°F)",
            "Notify local veterinary dispensary if additional cases present within 48 hours"
        ]
    },
    {
        "disease": "Foot-and-Mouth Disease (FMD)",
        "keywords": ["blister", "vesicle", "mouth", "ulcer", "drooling", "salivat", "lame", "hoof", "smack", "tongue"],
        "weight": 25,
        "category": "Infectious Vesicular Disease (Suspected FMD)",
        "priority": "CRITICAL",
        "vaccine_key": "FMD",
        "diagnostic_steps": [
            "Collect vesicular fluid or unruptured vesicle epithelial tissue into transport medium (pH 7.4)",
            "Submit sample for FMD Antigen-ELISA or RT-PCR testing",
            "Immediate biosecurity barrier: quarantine premise and stop animal movement",
            "Disinfect footwear and equipment with 4% sodium carbonate or 2% citric acid"
        ],
        "follow_up": [
            "Daily inspection of interdigital spaces and oral mucosa",
            "Check herd FMD vaccination records for non-immunized calves/heifers",
            "Soft feeding with gruel/electrolytes to maintain metabolic intake"
        ]
    },
    {
        "disease": "Black Quarter (BQ)",
        "keywords": ["crepit", "crackl", "swelling", "shoulder", "thigh", "lame", "gluteal", "gangren", "dark muscle"],
        "weight": 25,
        "category": "Clostridial Myonecrosis (Suspected BQ)",
        "priority": "CRITICAL",
        "vaccine_key": "BQ",
        "diagnostic_steps": [
            "Aspiration of crepitant swelling exudate for Gram stain (Gram-positive spore-forming rods)",
            "Avoid incision of lesion to prevent environmental sporulation of Clostridium chauvoei",
            "Immediate emergency parenteral penicillin/oxytetracycline therapy as directed by vet"
        ],
        "follow_up": [
            "Urgent ring-vaccination of young stock (6 months to 2 years) in herd",
            "Deep burial of any carcass with quicklime without opening body"
        ]
    },
    {
        "disease": "Brucellosis",
        "keywords": ["abort", "miscarriage", "retained placenta", "hygroma", "orchitis", "infertility", "stillborn"],
        "weight": 20,
        "category": "Reproductive / Zoonotic Infection (Suspected Brucellosis)",
        "priority": "HIGH",
        "vaccine_key": "Brucellosis",
        "diagnostic_steps": [
            "Submit maternal serum for Rose Bengal Plate Test (RBPT) and Standard Tube Agglutination Test (STAT)",
            "Submit milk sample for Brucella Milk Ring Test (MRT)",
            "Wear PPE (gloves, mask, protective eyewear) when handling aborted material — HIGH ZOONOTIC RISK"
        ],
        "follow_up": [
            "Segregate animal until vaginal discharge ceases completely",
            "Screen all adult female livestock in herd through serology",
            "Ensure farm workers and family members avoid consuming unpasteurized milk"
        ]
    },
    {
        "disease": "Lumpy Skin Disease (LSD)",
        "keywords": ["nodule", "lump", "skin", "edema", "lymph node", "pox", "scab"],
        "weight": 20,
        "category": "Capripoxvirus Dermatopathy (Suspected LSD)",
        "priority": "HIGH",
        "vaccine_key": "LSD",
        "diagnostic_steps": [
            "Collect skin lesion biopsy / scab or EDTA blood for Capripoxvirus PCR",
            "Apply vector control (deltamethrin/cypermethrin pour-on) to reduce biting fly/tick transmission",
            "Isolate cattle with skin lesions under fine-mesh fly-proof netting"
        ],
        "follow_up": [
            "Apply antiseptic ointment / fly repellent to open burst nodules",
            "Monitor secondary bacterial infection and administer supportive vitamins",
            "Verify goat pox / live attenuated heterologous vaccine status in herd"
        ]
    },
    {
        "disease": "Bovine Mastitis",
        "keywords": ["udder", "quarter", "clot", "milk", "teat", "mastitis", "watery milk", "bloody milk"],
        "weight": 20,
        "category": "Intramammary Infection (Suspected Mastitis)",
        "priority": "MEDIUM",
        "vaccine_key": None,
        "diagnostic_steps": [
            "Perform California Mastitis Test (CMT) strip cup examination on all four quarters",
            "Aseptically collect quarter milk sample for bacterial culture and antimicrobial sensitivity test (ABST)",
            "Check for systemic fever and udder hardness/heat"
        ],
        "follow_up": [
            "Post-milking teat dipping with 0.5% povidone-iodine",
            "Milk affected quarters last and discard abnormal secretions safely",
            "Review milking hygiene, teat liner condition, and bedding cleanliness"
        ]
    }
]


def evaluate_animal_cds(animal: dict, cases: list, vaccinations: list,
                        lab_reports: list, reproductive_records: list,
                        allergies: list, medications: list) -> dict:
    """Run deterministic clinical veterinary decision support assessment.
    Returns structured recommendations, risk indicators, abnormal findings,
    and explanation factors with mandatory disclaimer."""

    species_key = (animal.get("species") or animal.get("animal_type") or "cattle").strip().lower()
    if "buff" in species_key:
        ref_spec = "buffalo"
    elif "goat" in species_key:
        ref_spec = "goat"
    elif "sheep" in species_key:
        ref_spec = "sheep"
    else:
        ref_spec = "cattle"
    spec_ref = VET_REFERENCE_RANGES.get(ref_spec, VET_REFERENCE_RANGES["cattle"])

    abnormal_findings = []
    explanation_factors = []
    concern_categories = set()
    suggested_steps = []
    follow_up_recs = []
    base_risk = 10.0

    # 1. Evaluate Current & Active Cases
    active_cases = [c for c in cases if c.get("status") not in ("CLOSED", "RECOVERED")]
    all_symptoms_text = " ".join([
        (c.get("symptoms") or "") + " " + (c.get("description") or "") + " " + (c.get("disease_suspected") or "")
        for c in active_cases
    ]).lower()

    matched_diseases = []
    if active_cases:
        base_risk += 25.0
        abnormal_findings.append(f"Active clinical episode recorded ({len(active_cases)} ongoing case(s))")

        for sig in DISEASE_SIGNATURES:
            hits = [kw for kw in sig["keywords"] if kw in all_symptoms_text]
            if len(hits) >= 2:
                matched_diseases.append(sig)
                concern_categories.add(sig["category"])
                base_risk += sig["weight"]
                explanation_factors.append({
                    "factor": f"Symptom Match: {sig['disease']}",
                    "detail": f"Matched clinical signs: {', '.join(hits)}",
                    "impact": round(sig["weight"] / 100.0, 2)
                })
                suggested_steps.extend(sig["diagnostic_steps"])
                follow_up_recs.extend(sig["follow_up"])
            elif len(hits) == 1:
                explanation_factors.append({
                    "factor": f"Possible Marker: {sig['disease']}",
                    "detail": f"Symptom keyword: '{hits[0]}'",
                    "impact": 0.08
                })

    # 2. Evaluate Structured Laboratory Results (Feature Group 12)
    lab_flagged = False
    for rep in lab_reports:
        res_str = (rep.get("result") or "").upper()
        test_n = rep.get("test_name") or "Lab Test"
        flag = rep.get("abnormal_flag") or "Normal"
        q_val = rep.get("quantitative_result")

        # Check qualitative positives
        if res_str in ("POSITIVE", "ABNORMAL", "REACTIVE", "HIGH", "LOW") or flag in ("High", "Low", "Abnormal", "Positive"):
            lab_flagged = True
            base_risk += 30.0
            finding_txt = f"Abnormal Lab Report: {rep.get('report_no', 'Lab')} — {test_n} result is {res_str}"
            if rep.get("notes"):
                finding_txt += f" ({rep['notes']})"
            abnormal_findings.append(finding_txt)
            explanation_factors.append({
                "factor": f"Lab Abnormality: {test_n}",
                "detail": f"Result: {res_str} | Abnormal Flag: {flag} | Sample: {rep.get('sample') or 'Biological'}",
                "impact": 0.35
            })
            suggested_steps.append(f"Follow up abnormal {test_n} result: re-test or correlate with clinical progression")

        # Check quantitative results if test name matches CBC / hematology
        tn_lower = test_n.lower()
        if q_val is not None:
            if "wbc" in tn_lower or "leukocyte" in tn_lower:
                wbc_ref = spec_ref["wbc"]
                if q_val < wbc_ref["min"] or q_val > wbc_ref["max"]:
                    abnormal_findings.append(f"WBC count ({q_val} {wbc_ref['unit']}) outside normal reference range ({wbc_ref['min']}-{wbc_ref['max']})")
                    base_risk += 15.0
            elif "hemoglobin" in tn_lower or "hb" in tn_lower:
                hb_ref = spec_ref["hemoglobin"]
                if q_val < hb_ref["min"]:
                    abnormal_findings.append(f"Hemoglobin ({q_val} {hb_ref['unit']}) indicates anemia (reference: {hb_ref['min']}-{hb_ref['max']})")
                    base_risk += 12.0

    # 3. Evaluate Vaccination Protection Gaps
    today = date.today()
    core_vaccines = ["FMD", "HS", "BQ"]
    given_vaxes = {v.get("vaccine", "").upper(): v for v in vaccinations}

    for cv in core_vaccines:
        if cv not in given_vaxes:
            abnormal_findings.append(f"No vaccination record found for core endemic pathogen: {cv}")
            base_risk += 10.0
            explanation_factors.append({
                "factor": f"Immunity Gap: Missing {cv} Vaccine",
                "detail": f"Animal has no documented {cv} vaccination history",
                "impact": 0.12
            })
            suggested_steps.append(f"Schedule preventive {cv} vaccination drive when animal is clinically stable")
        else:
            rec = given_vaxes[cv]
            due_str = rec.get("next_due_date")
            if due_str:
                try:
                    due_date = datetime.strptime(due_str[:10], "%Y-%m-%d").date()
                    if due_date < today:
                        days_overdue = (today - due_date).days
                        abnormal_findings.append(f"{cv} vaccination overdue by {days_overdue} days (due {due_str[:10]})")
                        base_risk += 12.0
                        explanation_factors.append({
                            "factor": f"Immunity Gap: {cv} Overdue",
                            "detail": f"{days_overdue} days past booster schedule",
                            "impact": 0.14
                        })
                        suggested_steps.append(f"Administer overdue {cv} booster dose")
                except Exception:
                    pass

    # 4. Evaluate Reproductive Health
    for rr in reproductive_records:
        p_status = rr.get("pregnancy_status")
        if p_status == "Confirmed Pregnant":
            explanation_factors.append({
                "factor": "Physiological State: Pregnant",
                "detail": f"Expected Delivery: {rr.get('expected_delivery_date') or 'Pending'}",
                "impact": 0.05
            })
            suggested_steps.append("Exercise extreme caution with teratogenic/abortifacient medications (avoid dexamethasone, prostaglandins)")
        elif p_status == "Miscarried/Aborted":
            abnormal_findings.append("Recent reproductive failure / miscarriage recorded")
            base_risk += 18.0
            concern_categories.add("Reproductive Failure / Brucellosis Risk")
            suggested_steps.append("Screen maternal serum for Brucella abortus antibodies via RBPT/ELISA")

    # 5. Evaluate Allergies & Contraindications
    active_allergies = [a for a in allergies if a.get("status") == "Active"]
    if active_allergies:
        for al in active_allergies:
            abnormal_findings.append(f"Known {al.get('allergy_severity', 'Moderate')} Allergy: {al.get('allergen')} (Reaction: {al.get('reaction')})")
            explanation_factors.append({
                "factor": f"Allergy Alert: {al.get('allergen')}",
                "detail": f"Severity: {al.get('allergy_severity')} | Reaction: {al.get('reaction')}",
                "impact": 0.20
            })
            suggested_steps.append(f"Strict contraindication: DO NOT administer {al.get('allergen')} or related classes")

    # 6. Fallback / Default suggestions if clean
    if not abnormal_findings:
        abnormal_findings.append("No acute physiological abnormalities or active disease markers detected")
        suggested_steps.append("Continue routine preventive healthcare, deworming, and nutritional maintenance")
        follow_up_recs.append("Perform routine semi-annual clinical physical examination")
        confidence = 94.0
    else:
        confidence = min(96.0, 70.0 + len(abnormal_findings) * 4.5)

    final_score = min(100.0, max(5.0, round(base_risk, 1)))

    if final_score >= 70:
        risk_level = "High Risk"
    elif final_score >= 40:
        risk_level = "Moderate Risk"
    else:
        risk_level = "Low Risk"

    # Deduplicate recommendations preserving order
    def dedupe(lst):
        seen = set()
        res = []
        for x in lst:
            if x and x not in seen:
                seen.add(x)
                res.append(x)
        return res

    return {
        "model_version": "v1.2-veterinary-cds",
        "timestamp": datetime.now().isoformat(),
        "species": ref_spec.title(),
        "risk_score": final_score,
        "risk_level": risk_level,
        "confidence": round(confidence, 1),
        "abnormal_findings": dedupe(abnormal_findings),
        "concern_categories": sorted(list(concern_categories)) if concern_categories else ["Routine Maintenance"],
        "suggested_next_steps": dedupe(suggested_steps),
        "follow_up_recommendations": dedupe(follow_up_recs) if follow_up_recs else ["Review health card at next scheduled visit"],
        "explanation_factors": explanation_factors,
        "disclaimer": "AI-assisted decision support — veterinary confirmation required. Not an autonomous veterinary diagnosis.",
        "input_summary": {
            "species": ref_spec.title(),
            "breed": animal.get("breed") or "Standard",
            "age_years": animal.get("age") or animal.get("age_years") or 0,
            "active_cases_count": len(active_cases),
            "vaccines_recorded": len(vaccinations),
            "lab_reports_analyzed": len(lab_reports),
            "allergies_recorded": len(active_allergies),
            "pregnancy_status": (reproductive_records[0].get("pregnancy_status") if reproductive_records else "Not Pregnant")
        }
    }
