import json

import pytest

from ehr_media.ingestion import ingest, parse_date


@pytest.fixture
def row():
    return {"record_id": "doc-1", "patient_id": "ehr-1", "mrn": " ab-001 ",
            "name": " Alex  Example ", "dob": "01/20/1980", "gender": "F",
            "date": "2026/10/15", "kind": "lab", "text": "  Hemoglobin 12 g/dL.  "}


def run(tmp_path, rows):
    path = tmp_path / "input.json"
    path.write_text(json.dumps(rows))
    return ingest([path])


def test_normalization_preserves_identity_and_audit(tmp_path, row):
    result = run(tmp_path, [row])
    record = result.records[0]
    assert record.patient.mrn == "AB001"
    assert record.patient.name == "Alex Example"
    assert record.patient.gender == "female"
    assert str(record.patient.dob) == "1980-01-20"
    assert {e.field for e in result.audit} >= {"mrn", "name", "gender", "dob", "date", "text"}


def test_missing_demographics_are_unknown(tmp_path, row):
    for key in ("name", "dob", "gender", "date"):
        row.pop(key)
    record = run(tmp_path, [row]).records[0]
    assert record.patient.dob is None and record.date is None
    assert record.patient.gender == "unknown"


def test_missing_mrn_and_impossible_date_are_rejected(tmp_path, row):
    result = run(tmp_path, [{**row, "mrn": ""}, {**row, "date": "2026-02-30"}])
    assert result.rejected_count == 2 and not result.records


def test_duplicate_after_normalization(tmp_path, row):
    result = run(tmp_path, [row, {**row, "mrn": "AB001", "date": "2026-10-15"}])
    assert len(result.records) == 1 and result.duplicate_count == 1


@pytest.mark.parametrize("change", [{"dob": "1981-01-20"}, {"mrn": "AB002"}, {"name": "Different Person"}])
def test_identity_conflicts_quarantine_both_orders(tmp_path, row, change):
    other = {**row, "record_id": "doc-2", "text": "New note", **change}
    for rows in ([row, other], [other, row]):
        result = run(tmp_path, rows)
        assert result.rejected_count == 2 and not result.records


def test_csv_json_deduplication(tmp_path, row):
    import csv
    path = tmp_path / "input.json"
    path.write_text(json.dumps([row]))
    csv_path = tmp_path / "input.csv"
    with csv_path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=row.keys())
        writer.writeheader()
        writer.writerow(row)
    assert ingest([path, csv_path]).duplicate_count == 1


def test_record_id_collision_rejected(tmp_path, row):
    assert run(tmp_path, [row, {**row, "text": "Changed content"}]).rejected_count == 2


def test_date_policy_is_explicit():
    assert str(parse_date("01/02/2026")) == "2026-01-02"
    with pytest.raises(ValueError):
        parse_date("tomorrow")
