from copy import deepcopy
from datetime import date

import pytest

from ehr_media.fhir import document_sources, make_bundle, validate_bundle
from ehr_media.models import CleanRecord, Patient


@pytest.fixture
def bundle():
    return make_bundle([CleanRecord(id="r-1", patient=Patient(id="p-1", mrn="001", name="Alex Example"),
        kind="lab", title="CBC", date=date(2026, 10, 15), text="Hemoglobin 12 g/dL.", encounter_id="visit-1")])


def test_valid_r4_bundle_and_text_roundtrip(bundle):
    assert validate_bundle(bundle) == []
    assert {e["resource"]["resourceType"] for e in bundle["entry"]} == {"Patient", "DocumentReference", "DiagnosticReport", "Encounter"}
    assert document_sources(bundle)[0]["text"] == "Hemoglobin 12 g/dL."


def test_invalid_status_and_unknown_fields_report_paths(bundle):
    bundle["entry"][1]["resource"]["status"] = "banana"
    bundle["entry"][1]["resource"]["madeUp"] = True
    errors = validate_bundle(bundle)
    assert errors and any(e["validator"] == "HL7 R4 4.0.1" for e in errors)


def test_unresolved_or_wrong_patient_reference(bundle):
    bundle["entry"][1]["resource"]["subject"]["reference"] = "Patient/absent"
    assert any("Unresolved" in e["message"] for e in validate_bundle(bundle))


def test_duplicate_fullurl_rejected(bundle):
    bundle["entry"].append(deepcopy(bundle["entry"][1]))
    assert any("unique" in e["message"] for e in validate_bundle(bundle))


def test_date_only_does_not_fabricate_instant(bundle):
    doc = bundle["entry"][1]["resource"]
    assert "date" not in doc
    assert doc["content"][0]["attachment"]["creation"] == "2026-10-15"


def test_wrong_encounter_resource_type(bundle):
    bundle["entry"][1]["resource"]["context"]["encounter"][0]["reference"] = bundle["entry"][0]["fullUrl"]
    assert any("wrong resource type" in e["message"] for e in validate_bundle(bundle))
