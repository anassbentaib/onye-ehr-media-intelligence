"""Reproduce 50 entirely fictional records and five intentionally dirty input rows."""
import csv
import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data/synthetic"

# These are invented educational fixtures, not treatment guidance or patient data.
CASES = [
    ("Maya Ellis", "female", "1985-02-14", "Persistent cough and breathlessness.", "Asthma documented in prior notes.",
     "Eosinophils 0.7 x10^9/L, flagged high by the source laboratory.", "Chest radiograph: no focal consolidation or pleural effusion.",
     "Peak flow 310 L/min, reduced from documented baseline 420 L/min.", "Eosinophils flagged high in the original report."),
    ("Daniel Rowan", "male", "1968-09-20", "Follow-up for elevated blood glucose.", "Type 2 diabetes mellitus.",
     "HbA1c 8.4%, flagged high in the source report.", "Retinal imaging: no diabetic retinopathy reported.",
     "Fasting glucose 168 mg/dL, flagged high in the source report.", "HbA1c flagged high in the original report."),
    ("Leila Mercer", "female", "1992-04-08", "Fatigue and exertional dizziness.", "Iron deficiency anemia documented by the referring clinician.",
     "Hemoglobin 9.8 g/dL and ferritin 8 ng/mL, both flagged low.", "Pelvic ultrasound: no focal uterine lesion reported.",
     "Hemoglobin 10.6 g/dL, still flagged low in the repeat report.", "Hemoglobin and ferritin flagged low in the original report."),
    ("Owen Vale", "male", "1977-12-03", "Left knee pain after a fall.", "Left knee contusion documented in the visit note.",
     "C-reactive protein 3 mg/L, within the source laboratory range.", "Left knee radiograph: small joint effusion; no acute fracture.",
     "White blood cell count 6.4 x10^9/L, within the source laboratory range.", "Small left knee effusion noted in the radiology report."),
    ("Nora Bell", "female", "1959-07-17", "Intermittent right upper quadrant abdominal pain.", "Cholelithiasis documented by the referring clinician.",
     "Bilirubin 0.8 mg/dL, within the source laboratory range.", "Abdominal ultrasound: gallstones; no gallbladder wall thickening.",
     "Alkaline phosphatase 142 U/L, flagged high in the source report.", "Alkaline phosphatase flagged high in the original report."),
    ("Elias Hart", "male", "1971-11-22", "Review of reduced kidney function.", "Chronic kidney disease stage 3 documented in prior notes.",
     "Creatinine 1.7 mg/dL and eGFR 44 mL/min/1.73m2, flagged abnormal.", "Renal ultrasound: no hydronephrosis.",
     "Potassium 4.6 mmol/L, within the source laboratory range.", "Creatinine and eGFR flagged abnormal in the original report."),
    ("Sofia Lane", "female", "1988-03-12", "Cold intolerance and fatigue.", "Hypothyroidism documented in the assessment.",
     "TSH 9.2 mIU/L, flagged high; free T4 0.7 ng/dL, flagged low.", "Thyroid ultrasound: heterogeneous gland without a discrete nodule.",
     "TSH 6.1 mIU/L, still flagged high on repeat testing.", "TSH flagged high and free T4 flagged low in the original report."),
    ("Julian Reed", "male", "1990-06-06", "Recurrent headaches with visual aura.", "Migraine with aura documented by the neurologist.",
     "Sodium 139 mmol/L, within the source laboratory range.", "Brain MRI: no acute intracranial abnormality.",
     "ESR 8 mm/hr, within the source laboratory range.", None),
    ("Amara West", "female", "1964-10-29", "Intermittent palpitations without syncope.", "Premature atrial contractions documented on monitoring.",
     "TSH 2.1 mIU/L, within the source laboratory range.", "Echocardiogram: left ventricular ejection fraction 60%; no significant valve disease.",
     "Magnesium 1.9 mg/dL, within the source laboratory range.", "Ambulatory monitor documented frequent premature atrial contractions."),
    ("Theo Brooks", "male", "1982-01-09", "Dysuria and urinary frequency.", "Urinary tract infection documented in the visit note.",
     "Urinalysis: positive nitrites and leukocyte esterase.", "Renal ultrasound: no renal stone or hydronephrosis reported.",
     "Urine culture: Escherichia coli greater than 100,000 CFU/mL.", "Positive urine culture documented in the original report."),
]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, (name, gender, dob, concern, diagnosis, lab, imaging, followup, flag) in enumerate(CASES, 1):
        for j, (kind, title, day, finding) in enumerate([
            ("note", "Initial consultation", "09/05/2026", None),
            ("lab", "Laboratory results", "2026/09/12", lab),
            ("imaging", "Imaging report", "15-Oct-2026", imaging),
            ("lab", "Follow-up laboratory results", "2026-10-27", followup),
            ("discharge", "Follow-up visit summary", "11/03/2026", None),
        ], 1):
            lines = [f"Concern: {concern}", f"Diagnosis: {diagnosis}"]
            if finding:
                lines.append(f"Finding: {finding}")
            if flag and j in (2, 5):
                lines.append(f"Flag: {flag}")
            lines.append("Synthetic educational record. No real patient information.")
            rows.append({"record_id": f"synthetic-{i:02}-{j}", "patient_id": f"example-{i:02}",
                         "mrn": f" syn-{i:04} " if j % 2 else f"SYN{i:04}", "name": name,
                         "dob": dob if j % 2 else "/".join([dob[5:7], dob[8:], dob[:4]]),
                         "gender": gender[0].upper(), "date": day, "kind": kind, "title": title,
                         "encounter_id": f"visit-{i:02}", "text": "\n".join(lines)})
    (OUT / "ehr_export.json").write_text(json.dumps({"synthetic": True, "records": rows[:25]}, indent=2) + "\n")
    with (OUT / "scanned_notes.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows[25:])
    dirty = [dict(rows[0]), {**rows[0], "record_id": "missing-mrn", "mrn": ""},
             {**rows[0], "record_id": "bad-date", "date": "2026-02-30"},
             {**rows[0], "record_id": "conflict-a", "patient_id": "conflict", "mrn": "CONFLICT001", "dob": "1980-01-01"},
             {**rows[0], "record_id": "conflict-b", "patient_id": "conflict", "mrn": "CONFLICT001", "dob": "1981-01-01"}]
    (OUT / "edge_cases.json").write_text(json.dumps(dirty, indent=2) + "\n")
    print("Wrote 50 synthetic documents + 1 duplicate + 4 rejected edge cases.")


if __name__ == "__main__":
    main()
