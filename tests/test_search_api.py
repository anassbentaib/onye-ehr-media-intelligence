from datetime import date
import json

from fastapi.testclient import TestClient
import pytest

from ehr_media.api import create_app
from ehr_media.search import SearchEngine


def test_full_synthetic_pipeline(imported):
    store, engine = imported
    assert store.report()["accepted_records"] == 50
    assert store.report()["rejected_records"] == 4
    assert store.report()["duplicates"] == 1
    assert len(store.patients()) == 10
    assert engine.index.ntotal >= 90  # all documents, diagnostic reports, summaries


def test_top_five_filtering_and_dedup(imported):
    _, engine = imported
    matches = engine.search("low hemoglobin")
    assert len(matches) == 5
    assert len({(r["patient_id"], r["source_id"]) for r in matches}) == 5
    assert [r["score"] for r in matches] == sorted([r["score"] for r in matches], reverse=True)
    filtered = engine.search("hemoglobin", "DiagnosticReport", date(2026, 10, 1), date(2026, 10, 31))
    assert len(filtered) == 5
    assert all(r["resource_type"] == "DiagnosticReport" and "2026-10-01" <= r["date"] <= "2026-10-31" for r in filtered)
    assert engine.search("hemoglobin", date_from=date(2030, 1, 1)) == []


def test_index_persistence_and_staleness(imported, tmp_path, encoder):
    store, engine = imported
    engine.save(tmp_path / "copy")
    loaded = SearchEngine.load(tmp_path / "copy", store.patients(), encoder)
    assert loaded.search("glucose") == engine.search("glucose")
    with pytest.raises(ValueError, match="Stale"):
        SearchEngine.load(tmp_path / "copy", [], encoder)


def test_end_to_end_search_detail_bundle(imported):
    store, engine = imported
    with TestClient(create_app(store, engine)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/stats").json()["records"] == 50
        response = client.post("/search?resource_type=DiagnosticReport&date_from=2026-10-01", json={"query": "low hemoglobin"})
        assert response.status_code == 200
        result = response.json()["results"][0]
        detail = client.get(f"/patients/{result['patient_id']}").json()
        assert detail["summary"]["provider"] == "demo"
        for resource in detail["resources"]:
            assert client.get(resource["url"]).status_code == 200
        bundle = client.get(f"/patients/{result['patient_id']}/bundle").json()
        assert bundle["resourceType"] == "Bundle"
        assert client.get("/patients/missing").status_code == 404
        assert client.get(f"/patients/{result['patient_id']}/resources/Patient/wrong-id").status_code == 404


@pytest.mark.parametrize("url,payload", [
    ("/search", {"query": "  "}),
    ("/search", {"query": "x" * 501}),
    ("/search", {"query": "glucose", "unknown": True}),
    ("/search?resource_type=BadType", {"query": "glucose"}),
    ("/search?date_from=2026-11-01&date_to=2026-10-01", {"query": "glucose"}),
    ("/search?date_from=not-a-date", {"query": "glucose"}),
])
def test_invalid_requests(imported, url, payload):
    store, engine = imported
    with TestClient(create_app(store, engine)) as client:
        assert client.post(url, json=payload).status_code == 422


def test_unready_is_explicit(tmp_path):
    from ehr_media.store import Store
    with TestClient(create_app(Store(tmp_path / "empty.sqlite"))) as client:
        assert client.get("/health").status_code == 503
        assert client.post("/search", json={"query": "glucose"}).status_code == 503


def test_replace_snapshot_removes_old_patients(imported):
    store, _ = imported
    remaining = store.patients()[:1]
    store.replace_patients(remaining, {})
    assert len(store.patients()) == 1
