import argparse
import json
import os
import platform
import statistics
from pathlib import Path
from time import perf_counter

from .config import ROOT, data_dir
from .fhir import make_bundles, validate_bundle
from .ingestion import ingest
from .search import DEFAULT_MODEL, SearchEngine, SentenceEncoder
from .store import Store
from .summaries import summarize


def run_ingestion(paths: list[Path], directory: Path, provider: str, model: str, encoder=None):
    directory.mkdir(parents=True, exist_ok=True)
    result = ingest(paths)
    (directory / "cleaned.json").write_text(result.model_dump_json(indent=2))
    bundles = make_bundles(result.records)
    validation = [{"patient_id": b["id"].removeprefix("b-"), "errors": validate_bundle(b)} for b in bundles]
    (directory / "validation.json").write_text(json.dumps(validation, indent=2))
    if any(row["errors"] for row in validation):
        raise ValueError("FHIR validation failed; see runtime/validation.json. Previous data retained.")
    store = Store(directory / "ehr.sqlite")
    patients = []
    for bundle in bundles:
        pid = bundle["id"].removeprefix("b-")
        print(f"Summarizing {pid} ({provider})", flush=True)
        patients.append({"id": pid, "bundle": bundle, "summary": summarize(bundle, store, provider, model)})
    encoder = encoder or SentenceEncoder(os.getenv("EMBEDDING_MODEL", DEFAULT_MODEL), directory / "models")
    engine = SearchEngine.build(patients, encoder)
    report = {"input_records": result.input_count, "accepted_records": len(result.records),
              "rejected_records": result.rejected_count, "duplicates": result.duplicate_count,
              "patients": len(patients), "fhir_validation_errors": 0,
              "fhir_resources": sum(len(p["bundle"]["entry"]) for p in patients),
              "indexed_resources": len(engine.records), "indexed_chunks": engine.index.ntotal,
              "summary_provider": provider, "summary_model": model if provider == "openai" else "deterministic-excerpts",
              "embedding_model": encoder.name}
    # Import is an offline operation. Restart the server afterward. A crash
    # between these writes is detected by the index's corpus hash on startup.
    store.replace_patients(patients, report)
    engine.save(directory / "index")
    output = directory / "bundles"
    output.mkdir(exist_ok=True)
    for old in output.glob("*.json"):
        old.unlink()
    for person in patients:
        (output / f"{person['id']}.json").write_text(json.dumps(person["bundle"], indent=2))
    (directory / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return report


def benchmark(directory: Path, runs: int):
    from fastapi.testclient import TestClient
    from .api import create_app
    store = Store(directory / "ehr.sqlite")
    encoder = SentenceEncoder(os.getenv("EMBEDDING_MODEL", DEFAULT_MODEL), directory / "models")
    engine = SearchEngine.load(directory / "index", store.patients(), encoder)
    queries = ["low hemoglobin and fatigue", "diabetes with high blood sugar", "cough and shortness of breath", "kidney function creatinine", "headache brain imaging"]
    timings = []
    with TestClient(create_app(store, engine)) as client:
        client.post("/search", json={"query": queries[0]}).raise_for_status()
        for i in range(runs):
            start = perf_counter()
            response = client.post("/search", json={"query": queries[i % len(queries)]})
            response.raise_for_status()
            assert len(response.json()["results"]) == 5
            timings.append((perf_counter() - start) * 1000)
    result = {"runs": runs, "warm": True, "transport": "FastAPI TestClient (full POST /search)",
              "python": platform.python_version(), "platform": platform.platform(),
              "documents": store.report().get("accepted_records"), "model": encoder.name,
              "median_ms": round(statistics.median(timings), 2),
              "p95_ms": round(sorted(timings)[max(0, int(len(timings) * .95) - 1)], 2),
              "max_ms": round(max(timings), 2), "all_under_2s": max(timings) < 2000}
    (directory / "benchmark.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    if not result["all_under_2s"]:
        raise SystemExit("Search exceeded the 2-second assessment target")


def main():
    parser = argparse.ArgumentParser(description="Synthetic EHR pipeline")
    sub = parser.add_subparsers(dest="command", required=True)
    load = sub.add_parser("ingest")
    load.add_argument("paths", nargs="*", type=Path)
    load.add_argument("--summary-provider", choices=["demo", "openai"], default=os.getenv("SUMMARY_PROVIDER", "demo"))
    load.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"))
    bench = sub.add_parser("benchmark")
    bench.add_argument("--runs", type=int, default=25)
    args = parser.parse_args()
    if args.command == "ingest":
        paths = args.paths or sorted((ROOT / "data/synthetic").glob("*.json")) + sorted((ROOT / "data/synthetic").glob("*.csv"))
        if not paths:
            parser.error("No input files")
        run_ingestion(paths, data_dir(), args.summary_provider, args.model)
    else:
        if args.runs < 5:
            parser.error("Use at least 5 benchmark runs")
        benchmark(data_dir(), args.runs)


if __name__ == "__main__":
    main()
