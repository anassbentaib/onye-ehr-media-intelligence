"""Deterministic cleaning. Identity conflicts quarantine all affected rows."""
import csv
import hashlib
import json
import re
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from pydantic import ValidationError

from .models import AuditEvent, CleanRecord, IngestionResult, Patient


def stable_id(*values: str) -> str:
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()[:24]


def parse_date(value: object) -> date | None:
    """US exports use month/day/year. No locale-dependent or fuzzy parsing."""
    if value is None or str(value).strip() == "":
        return None
    value = str(value).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d", "%d-%b-%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Invalid date {value!r}; expected ISO, US MM/DD/YYYY, YYYY/MM/DD or DD-Mon-YYYY")


def read_rows(path: Path) -> list[dict]:
    if path.suffix.lower() == ".json":
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        rows = value.get("records") if isinstance(value, dict) else value
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise ValueError(f"{path.name}: expected a JSON array of record objects")
        return rows
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as stream:
            return list(csv.DictReader(stream))
    raise ValueError(f"Unsupported input: {path.name}; use .json or .csv")


def ingest(paths: list[Path]) -> IngestionResult:
    audit: list[AuditEvent] = []
    candidates: list[tuple[CleanRecord, str, int]] = []
    total = rejected = duplicates = 0

    def log(src, row, action, field, reason, before=None, after=None):
        audit.append(AuditEvent(source=src, row=row, action=action, field=field,
                                reason=reason, before=None if before is None else str(before),
                                after=None if after is None else str(after)))

    for path in paths:
        for row_number, raw in enumerate(read_rows(path), 1):
            total += 1
            src = path.name
            try:
                raw_patient = raw.get("patient", raw)
                if not isinstance(raw_patient, dict):
                    raise ValueError("patient must be an object")
                original_mrn = str(raw_patient.get("mrn") or "").strip()
                mrn = re.sub(r"[\s-]+", "", original_mrn).upper()
                if not re.fullmatch(r"[A-Z0-9]{1,40}", mrn):
                    raise ValueError("Missing or invalid MRN; no patient identity is invented")
                if mrn != original_mrn:
                    log(src, row_number, "normalized", "mrn", "Removed separators; preserved leading zeros", original_mrn, mrn)
                name = " ".join(str(raw_patient.get("name") or "").split())
                if not name:
                    name = "Unknown patient"
                    log(src, row_number, "warning", "name", "Missing name; display placeholder only")
                elif name != raw_patient.get("name"):
                    log(src, row_number, "normalized", "name", "Trimmed whitespace", raw_patient.get("name"), name)
                dob = parse_date(raw_patient.get("dob"))
                record_date = parse_date(raw.get("date"))
                if dob and (dob > date.today() or (record_date and dob > record_date)):
                    raise ValueError("DOB is in the future or later than record date")
                for field, before, after in (("dob", raw_patient.get("dob"), dob), ("date", raw.get("date"), record_date)):
                    if after is None:
                        log(src, row_number, "warning", field, "Missing date retained as unknown")
                    elif str(before).strip() != after.isoformat():
                        log(src, row_number, "normalized", field, "Applied explicit US/ISO date policy", before, after)
                raw_gender = str(raw_patient.get("gender") or "").strip().lower()
                gender = {"m": "male", "f": "female", "o": "other", "u": "unknown",
                          "male": "male", "female": "female", "other": "other", "unknown": "unknown"}.get(raw_gender, "unknown")
                if raw_gender != gender:
                    log(src, row_number, "normalized" if raw_gender in {"m", "f", "o", "u"} else "warning",
                        "gender", "Mapped administrative gender; unknown codes remain unknown", raw_patient.get("gender"), gender)
                text = str(raw.get("text") or "").replace("\r\n", "\n").strip()
                text = "\n".join(re.sub(r"[\t ]+", " ", line).strip() for line in text.splitlines())
                if text != raw.get("text"):
                    log(src, row_number, "normalized", "text", "Trimmed whitespace without changing clinical content")
                kind = str(raw.get("kind") or "note").strip().lower()
                patient = Patient(id="p-" + stable_id(mrn), mrn=mrn, name=name, dob=dob, gender=gender)
                source_id = str(raw.get("record_id") or "").strip() or None
                record = CleanRecord(
                    id="r-" + stable_id(mrn, str(record_date), kind, text), source_id=source_id,
                    external_patient_id=str(raw_patient.get("patient_id") or "").strip() or None,
                    patient=patient, kind=kind, date=record_date, text=text,
                    title=str(raw.get("title") or f"{kind.title()} record").strip(),
                    encounter_id=str(raw.get("encounter_id") or "").strip() or None,
                )
                candidates.append((record, src, row_number))
            except (ValueError, TypeError, ValidationError) as error:
                rejected += 1
                log(src, row_number, "rejected", "record", str(error))

    by_mrn, by_external, by_source = defaultdict(list), defaultdict(set), defaultdict(set)
    for record, _, _ in candidates:
        by_mrn[record.patient.mrn].append(record)
        if record.external_patient_id:
            by_external[record.external_patient_id].add(record.patient.mrn)
        if record.source_id:
            by_source[record.source_id].add(record.id)
    conflicts: set[str] = set()
    for mrns in by_external.values():
        if len(mrns) > 1:
            conflicts.update(mrns)
    for mrn, records in by_mrn.items():
        for getter in (
            lambda r: r.patient.dob,
            lambda r: r.patient.name.casefold() if r.patient.name != "Unknown patient" else None,
            lambda r: r.patient.gender if r.patient.gender != "unknown" else None,
        ):
            if len({getter(r) for r in records if getter(r) is not None}) > 1:
                conflicts.add(mrn)

    # Fill only missing demographics from consistent records for the same exact MRN.
    canonical = {}
    for mrn, records in by_mrn.items():
        canonical[mrn] = Patient(
            id=records[0].patient.id, mrn=mrn,
            name=next((r.patient.name for r in records if r.patient.name != "Unknown patient"), "Unknown patient"),
            dob=next((r.patient.dob for r in records if r.patient.dob), None),
            gender=next((r.patient.gender for r in records if r.patient.gender != "unknown"), "unknown"),
        )
    accepted, seen = [], set()
    for record, src, row_number in candidates:
        if record.patient.mrn in conflicts or (record.source_id and len(by_source[record.source_id]) > 1):
            rejected += 1
            log(src, row_number, "rejected", "identity", "Conflicting MRN, demographics, external patient ID or source record ID; quarantine all affected rows")
        elif record.id in seen:
            duplicates += 1
            log(src, row_number, "duplicate", "record", "Exact canonical content duplicate omitted", record.source_id, record.id)
        else:
            patient = canonical[record.patient.mrn]
            if patient != record.patient:
                log(src, row_number, "normalized", "patient", "Filled missing demographics from consistent same-MRN records")
            accepted.append(record.model_copy(update={"patient": patient}))
            seen.add(record.id)
            log(src, row_number, "accepted", "record", "Validated intermediate representation", after=record.id)
    return IngestionResult(records=accepted, audit=audit, input_count=total,
                           rejected_count=rejected, duplicate_count=duplicates)

