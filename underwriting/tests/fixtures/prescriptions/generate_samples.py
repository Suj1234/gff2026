"""generate_samples.py — one-off script that renders synthetic Indian prescription
images for the health-agent OCR demo/tests (HEALTH_AGENT_PLAN.md §2, §10).

These are NOT real prescriptions. Every doctor, clinic, patient name, registration
number, and address below is fabricated for testing. Styled loosely on common Indian
clinic/hospital prescription layouts (letterhead, Reg. No., Rx symbol, drug table,
follow-up advice) so OCR is exercised against realistic formatting: mixed print sizes,
a clinic letterhead, drug names + dosage + frequency + duration in the typical Indian
"1-0-1" (morning-afternoon-night) shorthand, and doctor signature block.

Run once: `python underwriting/tests/fixtures/prescriptions/generate_samples.py`
Regenerate if the layout needs to change; the PNGs themselves are committed (small,
deterministic, no need to regenerate on every checkout).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path(__file__).parent
FONT_DIR = Path("C:/Windows/Fonts")

W, H = 1000, 1300
MARGIN = 60


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / name), size)


F_TITLE = _font("arialbd.ttf", 34)
F_SUB = _font("arial.ttf", 18)
F_LABEL = _font("arialbd.ttf", 20)
F_BODY = _font("arial.ttf", 20)
F_RX = _font("times.ttf", 46)
F_SMALL = _font("arial.ttf", 15)


def _wrap(draw, text, font, max_width):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def render_prescription(
    *,
    filename: str,
    clinic_name: str,
    clinic_addr: str,
    doctor_name: str,
    doctor_qual: str,
    reg_no: str,
    patient_name: str,
    patient_age_sex: str,
    date: str,
    diagnosis_note: str,
    drugs: list[tuple[str, str, str]],  # (name+strength, dosage shorthand, duration)
    advice: str,
) -> None:
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)

    # --- letterhead ---
    d.rectangle([(0, 0), (W, 130)], fill=(235, 245, 250))
    d.text((MARGIN, 20), clinic_name, font=F_TITLE, fill=(20, 60, 90))
    d.text((MARGIN, 65), clinic_addr, font=F_SUB, fill=(60, 60, 60))
    d.text((MARGIN, 90), f"{doctor_name}, {doctor_qual}", font=F_SUB, fill=(60, 60, 60))
    d.text((W - 260, 90), f"Reg. No: {reg_no}", font=F_SMALL, fill=(90, 90, 90))
    d.line([(MARGIN, 130), (W - MARGIN, 130)], fill=(180, 200, 210), width=2)

    y = 155
    d.text((MARGIN, y), f"Patient: {patient_name}", font=F_LABEL, fill=(0, 0, 0))
    d.text((W - 300, y), f"Date: {date}", font=F_BODY, fill=(0, 0, 0))
    y += 32
    d.text((MARGIN, y), f"Age/Sex: {patient_age_sex}", font=F_BODY, fill=(0, 0, 0))
    y += 45

    if diagnosis_note:
        d.text((MARGIN, y), "Clinical notes:", font=F_LABEL, fill=(0, 0, 0))
        y += 28
        for line in _wrap(d, diagnosis_note, F_BODY, W - 2 * MARGIN):
            d.text((MARGIN + 10, y), line, font=F_BODY, fill=(30, 30, 30))
            y += 26
        y += 15

    d.text((MARGIN, y), "Rx", font=F_RX, fill=(20, 60, 90))
    y += 65
    d.line([(MARGIN, y), (W - MARGIN, y)], fill=(200, 200, 200), width=1)
    y += 20

    for i, (drug, dosage, duration) in enumerate(drugs, start=1):
        d.text((MARGIN + 10, y), f"{i}. {drug}", font=F_LABEL, fill=(0, 0, 0))
        y += 28
        d.text((MARGIN + 40, y), f"   {dosage}   —   {duration}", font=F_BODY, fill=(50, 50, 50))
        y += 40

    y += 20
    d.line([(MARGIN, y), (W - MARGIN, y)], fill=(200, 200, 200), width=1)
    y += 25
    d.text((MARGIN, y), "Advice:", font=F_LABEL, fill=(0, 0, 0))
    y += 28
    for line in _wrap(d, advice, F_BODY, W - 2 * MARGIN):
        d.text((MARGIN + 10, y), line, font=F_BODY, fill=(30, 30, 30))
        y += 26

    # signature block
    d.text((W - 300, H - 140), "_________________________", font=F_BODY, fill=(0, 0, 0))
    d.text((W - 300, H - 110), doctor_name, font=F_SUB, fill=(0, 0, 0))
    d.text((W - 300, H - 88), "(Signature)", font=F_SMALL, fill=(90, 90, 90))

    d.text((MARGIN, H - 40), "-- fabricated sample for underwriting-agent testing only, not a real prescription --",
           font=F_SMALL, fill=(160, 160, 160))

    img.save(OUT_DIR / filename)
    print("wrote", filename)


def main() -> None:
    render_prescription(
        filename="sample_diabetes_metformin.png",
        clinic_name="Sunrise Diabetes & Wellness Clinic",
        clinic_addr="No. 14, MG Road, Bengaluru - 560001",
        doctor_name="Dr. Anitha Rao",
        doctor_qual="MBBS, MD (Endocrinology)",
        reg_no="KMC-88213",
        patient_name="Ramesh Iyer (fictional, test data)",
        patient_age_sex="52 / M",
        date="12-May-2024",
        diagnosis_note="Type 2 Diabetes Mellitus, diagnosed 2019. HbA1c 7.8% at last visit, "
                        "poorly controlled on prior monotherapy.",
        drugs=[
            ("Tab. Metformin 500mg", "1-0-1 (after food)", "30 days"),
            ("Tab. Glimepiride 1mg", "1-0-0 (before breakfast)", "30 days"),
        ],
        advice="Review HbA1c in 3 months. Continue diet control and daily walk 30 min. "
               "Report any hypoglycemia symptoms immediately.",
    )

    render_prescription(
        filename="sample_cardiac_statin.png",
        clinic_name="Apex Heart & Vascular Centre",
        clinic_addr="Plot 22, Anna Salai, Chennai - 600002",
        doctor_name="Dr. Suresh Menon",
        doctor_qual="MBBS, DM (Cardiology)",
        reg_no="TNMC-55021",
        patient_name="Vikram Nair (fictional, test data)",
        patient_age_sex="58 / M",
        date="03-Jan-2025",
        diagnosis_note="Known case of Ischaemic Heart Disease s/p PTCA (2021). Stable angina, "
                        "on secondary prevention therapy.",
        drugs=[
            ("Tab. Atorvastatin 20mg", "0-0-1 (at night)", "90 days"),
            ("Tab. Metoprolol 25mg", "1-0-1", "90 days"),
            ("Tab. Aspirin 75mg", "0-1-0 (after lunch)", "90 days"),
        ],
        advice="Continue cardiac rehab exercises. Avoid heavy exertion. Follow-up ECG "
               "and lipid profile after 3 months.",
    )

    render_prescription(
        filename="sample_thyroid_levothyroxine.png",
        clinic_name="Care & Cure Multispeciality Clinic",
        clinic_addr="Sector 18, Noida - 201301",
        doctor_name="Dr. Priya Sharma",
        doctor_qual="MBBS, MD (Internal Medicine)",
        reg_no="DMC-31940",
        patient_name="Neha Gupta (fictional, test data)",
        patient_age_sex="34 / F",
        date="20-Feb-2025",
        diagnosis_note="Hypothyroidism, on replacement therapy since 2022. TSH 4.1 mIU/L "
                        "at last review, within target range.",
        drugs=[
            ("Tab. Levothyroxine 50mcg", "1-0-0 (empty stomach, 30 min before breakfast)", "90 days"),
        ],
        advice="Repeat TSH after 3 months. No dose change unless advised.",
    )

    render_prescription(
        filename="sample_hypertension_amlodipine.png",
        clinic_name="Green Valley Family Clinic",
        clinic_addr="Baner Road, Pune - 411045",
        doctor_name="Dr. Kavita Deshmukh",
        doctor_qual="MBBS, MD (General Medicine)",
        reg_no="MMC-72611",
        patient_name="Sunita Patil (fictional, test data)",
        patient_age_sex="47 / F",
        date="09-Jun-2024",
        diagnosis_note="Essential Hypertension, diagnosed 2023. BP 138/88 today, "
                        "improved from 152/96 at diagnosis.",
        drugs=[
            ("Tab. Amlodipine 5mg", "1-0-0 (morning)", "60 days"),
        ],
        advice="Low salt diet, monitor home BP weekly, review in 2 months.",
    )

    render_prescription(
        filename="sample_asthma_inhaler.png",
        clinic_name="Breathe Easy Chest Clinic",
        clinic_addr="Park Street, Kolkata - 700016",
        doctor_name="Dr. Arindam Chatterjee",
        doctor_qual="MBBS, MD (Pulmonology)",
        reg_no="WBMC-40982",
        patient_name="Debasish Roy (fictional, test data)",
        patient_age_sex="29 / M",
        date="15-Aug-2024",
        diagnosis_note="Mild persistent bronchial asthma, seasonal exacerbation. "
                        "One ER visit in 2022 for acute attack, none since.",
        drugs=[
            ("Budesonide + Formoterol Inhaler 200mcg", "2 puffs twice daily", "as needed / ongoing"),
            ("Tab. Montelukast 10mg", "0-0-1 (at night)", "30 days"),
        ],
        advice="Carry rescue inhaler at all times. Avoid known triggers (dust, cold air). "
               "Review in 1 month or sooner if symptomatic.",
    )


if __name__ == "__main__":
    main()
