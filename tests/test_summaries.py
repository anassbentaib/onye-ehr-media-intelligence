from copy import deepcopy

import pytest

from ehr_media.fhir import make_bundle
from ehr_media.models import CleanRecord, Patient
from ehr_media.store import Store
from ehr_media.summaries import Evidence, SummaryDraft, summarize, verify_summary


def draft(text="No evidence of pneumonia.", source_id="r-1"):
    return SummaryDraft(chief_concern=None, key_diagnoses=[],
                        recent_media=[Evidence(text=text, source_id=source_id)], flagged_anomalies=[])


def test_exact_evidence_and_word_limit():
    sources = [{"id": "r-1", "text": "No evidence of pneumonia."}]
    result = verify_summary(draft(), sources, "openai", "test-model")
    assert result.word_count < 200 and "AI-generated" in result.disclaimer


@pytest.mark.parametrize("text,source_id", [("Pneumonia confirmed.", "r-1"), ("No evidence of pneumonia.", "another-patient"), ("Hemoglobin 15 g/dL", "r-1")])
def test_unsupported_claims_rejected(text, source_id):
    with pytest.raises(ValueError, match="exact excerpt"):
        verify_summary(draft(text, source_id), [{"id": "r-1", "text": "No evidence of pneumonia."}], "openai", "test")


def test_overlength_rejected():
    value = " ".join(["word"] * 190)
    with pytest.raises(ValueError, match="under 200"):
        verify_summary(draft(value), [{"id": "r-1", "text": value}], "openai", "test")


def test_cache_and_record_hash_and_model_invalidation(tmp_path):
    bundle = make_bundle([CleanRecord(id="r-1", patient=Patient(id="p-1", mrn="001", name="Test"),
                          kind="imaging", title="X-ray", text="No evidence of pneumonia.")])
    store = Store(tmp_path / "data.sqlite")
    calls = []
    def generate(sources):
        calls.append(True)
        return draft()
    a = summarize(bundle, store, "openai", "model-a", generate)
    assert summarize(bundle, store, "openai", "model-a", generate) == a
    assert len(calls) == 1
    summarize(bundle, store, "openai", "model-b", generate)
    changed = deepcopy(bundle)
    changed["entry"][0]["resource"]["name"][0]["text"] = "Updated name"
    summarize(changed, store, "openai", "model-b", generate)
    assert len(calls) == 3
    demo = summarize(bundle, store, "demo")
    assert demo["provider"] == "demo" and "not LLM" in demo["disclaimer"]


def test_failed_generation_not_cached(tmp_path):
    bundle = make_bundle([CleanRecord(id="r-1", patient=Patient(id="p-1", mrn="001", name="Test"),
                          kind="note", title="Note", text="No evidence of pneumonia.")])
    store = Store(tmp_path / "data.sqlite")
    with pytest.raises(ValueError):
        summarize(bundle, store, "openai", generate=lambda _: draft("Invented diagnosis"))
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM summary_cache").fetchone()[0] == 0
