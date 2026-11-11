from contextlib import asynccontextmanager
from datetime import date as Date
import os
from pathlib import Path
from time import perf_counter
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import ROOT, data_dir
from .search import DEFAULT_MODEL, SearchEngine, SentenceEncoder
from .store import Store

ResourceType = Literal["Patient", "DocumentReference", "DiagnosticReport"]


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=2, max_length=500)

    @field_validator("query")
    @classmethod
    def trim(cls, value):
        if len(value.strip()) < 2:
            raise ValueError("Enter at least two non-whitespace characters")
        return value.strip()


def create_app(store: Store | None = None, engine: SearchEngine | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        app.state.store = store or Store(data_dir() / "ehr.sqlite")
        app.state.patients = {p["id"]: p for p in app.state.store.patients()}
        app.state.engine = engine
        app.state.startup_error = None
        if engine is None and app.state.patients:
            try:
                encoder = SentenceEncoder(os.getenv("EMBEDDING_MODEL", DEFAULT_MODEL), data_dir() / "models")
                app.state.engine = SearchEngine.load(data_dir() / "index", list(app.state.patients.values()), encoder)
                encoder.encode(["warmup"])
            except (OSError, ValueError, RuntimeError) as error:
                app.state.startup_error = str(error)
        yield

    app = FastAPI(title="EHR Media Intelligence", version="1.0.0", lifespan=lifespan)

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if not request.url.path.startswith("/assets/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health")
    def health():
        ready = app.state.engine is not None
        return JSONResponse({"status": "ready" if ready else "not_ready",
                             "detail": app.state.startup_error if not ready else None}, status_code=200 if ready else 503)

    @app.post("/search")
    def search(payload: SearchRequest, resource_type: ResourceType | None = None,
               date_from: Date | None = None, date_to: Date | None = None):
        if date_from and date_to and date_from > date_to:
            raise HTTPException(422, "date_from must be on or before date_to")
        if app.state.engine is None:
            raise HTTPException(503, "Search index is not ready. Import records and restart the server.")
        start = perf_counter()
        matches = app.state.engine.search(payload.query, resource_type, date_from, date_to)
        return {"query": payload.query, "results": matches, "count": len(matches),
                "elapsed_ms": round((perf_counter() - start) * 1000, 2), "score_kind": "cosine_similarity"}

    @app.get("/stats")
    def stats():
        patients = list(app.state.patients.values())
        report = app.state.store.report()
        return {"patients": len(patients), "records": report.get("accepted_records", 0),
                "input_records": report.get("input_records", 0), "rejected_records": report.get("rejected_records", 0),
                "duplicates": report.get("duplicates", 0), "validation_errors": report.get("fhir_validation_errors", 0),
                "fhir_resources": sum(len(p["bundle"]["entry"]) for p in patients),
                "summary_providers": sorted({p["summary"]["provider"] for p in patients}),
                "ready": app.state.engine is not None,
                "data_label": "Synthetic assessment data"}

    @app.get("/records")
    def records(limit: int = Query(10, ge=1, le=50), resource_type: ResourceType | None = None,
                date_from: Date | None = None, date_to: Date | None = None):
        if date_from and date_to and date_from > date_to:
            raise HTTPException(422, "date_from must be on or before date_to")
        if app.state.engine is None:
            return {"results": []}
        rows = [r for r in app.state.engine.records if r["resource_type"] == (resource_type or "DocumentReference")]
        rows = [r for r in rows if not (date_from or date_to) or
                (r["date"] and (not date_from or r["date"] >= str(date_from)) and (not date_to or r["date"] <= str(date_to)))]
        rows.sort(key=lambda r: (r["date"] or "", r["id"]), reverse=True)
        return {"results": [{k: v for k, v in r.items() if k != "text"} | {"score": None} for r in rows[:limit]]}

    def get_patient(patient_id):
        patient = app.state.patients.get(patient_id)
        if patient is None:
            raise HTTPException(404, "Patient not found")
        return patient

    @app.get("/patients/{patient_id}")
    def patient_detail(patient_id: str):
        person = get_patient(patient_id)
        bundle = person["bundle"]
        patient = next(e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == "Patient")
        return {"patient": patient, "summary": person["summary"],
                "resources": [{"id": e["resource"]["id"], "resource_type": e["resource"]["resourceType"],
                               "title": e["resource"].get("description") or e["resource"].get("code", {}).get("text") or e["resource"]["resourceType"],
                               "url": f"/patients/{patient_id}/resources/{e['resource']['resourceType']}/{e['resource']['id']}"}
                              for e in bundle["entry"]]}

    @app.get("/patients/{patient_id}/bundle")
    def patient_bundle(patient_id: str):
        return get_patient(patient_id)["bundle"]

    @app.get("/patients/{patient_id}/resources/{resource_type}/{resource_id}")
    def resource(patient_id: str, resource_type: str, resource_id: str):
        for entry in get_patient(patient_id)["bundle"]["entry"]:
            value = entry["resource"]
            if value["resourceType"] == resource_type and value["id"] == resource_id:
                return value
        raise HTTPException(404, "Resource not found for this patient")

    @app.get("/", include_in_schema=False)
    def home():
        return FileResponse(ROOT / "web/index.html")

    app.mount("/assets", StaticFiles(directory=ROOT / "web"), name="assets")
    return app


app = create_app()
