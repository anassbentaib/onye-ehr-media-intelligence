import base64
import json
import os
import threading
import uuid
from pathlib import Path

# Initialize PyTorch's native runtime before FAISS on Intel macOS. Loading their
# OpenMP runtimes in the reverse order can crash during interpreter shutdown.
import torch
import faiss
import numpy as np

from .fhir import document_sources
from .store import content_hash
from .summaries import Summary, evidence_items

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
INDEX_VERSION = 1


class SentenceEncoder:
    def __init__(self, model_name=DEFAULT_MODEL, cache_dir: Path | None = None):
        import torch
        from sentence_transformers import SentenceTransformer
        torch.set_num_threads(2)
        self.name = model_name
        self.model = SentenceTransformer(model_name, device="cpu", cache_folder=str(cache_dir) if cache_dir else None)
        self.lock = threading.Lock()
        self.dimension = self.model.get_sentence_embedding_dimension()

    def chunks(self, text: str) -> list[str]:
        tokens = self.model.tokenizer.encode(text, add_special_tokens=False)
        size = min(192, self.model.max_seq_length - 2)
        stride = max(1, size - 32)
        chunks = []
        for start in range(0, len(tokens), stride):
            chunks.append(self.model.tokenizer.decode(tokens[start:start + size], skip_special_tokens=True))
            if start + size >= len(tokens):
                break
        return chunks or [""]

    def encode(self, texts: list[str]) -> np.ndarray:
        with self.lock:
            return self.model.encode(texts, convert_to_numpy=True, normalize_embeddings=True,
                                     show_progress_bar=False, batch_size=32).astype("float32")


def corpus_hash(patients: list[dict]) -> str:
    return content_hash([[p["id"], content_hash(p["bundle"]), content_hash(p["summary"])] for p in sorted(patients, key=lambda p: p["id"])])


def search_records(patients: list[dict]) -> list[dict]:
    records = []
    for person in patients:
        bundle, summary = person["bundle"], person["summary"]
        patient = next(e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == "Patient")
        sources = document_sources(bundle)
        snippet = " ".join(e.text for e in evidence_items(Summary.model_validate(summary)))[:320]
        common = {"patient_id": patient["id"], "patient_name": patient["name"][0]["text"],
                  "mrn": patient["identifier"][0]["value"], "summary_snippet": snippet,
                  "summary_provider": summary["provider"]}
        for source in sources:
            records.append({**common, "id": source["id"], "source_id": source["id"],
                            "resource_type": "DocumentReference", "date": source["date"],
                            "title": source["title"], "text": source["text"]})
        for entry in bundle["entry"]:
            resource = entry["resource"]
            if resource["resourceType"] == "DiagnosticReport":
                records.append({**common, "id": resource["id"], "source_id": resource["id"].removeprefix("dr-"),
                                "resource_type": "DiagnosticReport", "date": resource.get("effectiveDateTime"),
                                "title": resource["code"]["text"], "text": resource["conclusion"]})
        summary_text = " ".join(e.text for e in evidence_items(Summary.model_validate(summary)))
        if summary_text:
            records.append({**common, "id": patient["id"], "source_id": "summary-" + patient["id"],
                            "resource_type": "Patient", "date": sources[0]["date"] if sources else None,
                            "title": "Patient summary", "text": summary_text})
    return records


class SearchEngine:
    def __init__(self, encoder, records: list[dict], index, mapping: list[int], digest: str):
        self.encoder, self.records, self.index, self.mapping, self.digest = encoder, records, index, mapping, digest

    @classmethod
    def build(cls, patients: list[dict], encoder):
        records = search_records(patients)
        chunks, mapping = [], []
        for i, record in enumerate(records):
            for chunk in encoder.chunks(record["title"] + "\n" + record["text"]):
                chunks.append(chunk)
                mapping.append(i)
        index = faiss.IndexFlatIP(encoder.dimension)
        if chunks:
            index.add(encoder.encode(chunks))
        return cls(encoder, records, index, mapping, corpus_hash(patients))

    def save(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        generation = uuid.uuid4().hex
        index_file = f"{generation}.faiss"
        faiss.write_index(self.index, str(directory / index_file))
        metadata = {"version": INDEX_VERSION, "model": self.encoder.name, "corpus_hash": self.digest,
                    "records": self.records, "mapping": self.mapping, "index_file": index_file}
        temporary = directory / f"{generation}.json.tmp"
        temporary.write_text(json.dumps(metadata))
        os.replace(temporary, directory / "manifest.json")

    @classmethod
    def load(cls, directory: Path, patients: list[dict], encoder):
        metadata = json.loads((directory / "manifest.json").read_text())
        if (metadata["version"] != INDEX_VERSION or metadata["model"] != encoder.name
                or metadata["corpus_hash"] != corpus_hash(patients)):
            raise ValueError("Stale search index; rerun the ingestion command and restart the server")
        index = faiss.read_index(str(directory / metadata["index_file"]))
        if index.d != encoder.dimension or index.ntotal != len(metadata["mapping"]):
            raise ValueError("Index dimension or metadata count mismatch")
        return cls(encoder, metadata["records"], index, metadata["mapping"], metadata["corpus_hash"])

    def search(self, query: str, resource_type=None, date_from=None, date_to=None, limit=5) -> list[dict]:
        if self.index.ntotal == 0:
            return []
        # Search the complete small corpus, filter, then take top-k. Filtering after
        # top-5 would incorrectly miss lower-ranked eligible records.
        scores, positions = self.index.search(self.encoder.encode([query]), self.index.ntotal)
        result, seen = [], set()
        for score, position in zip(scores[0], positions[0]):
            if position < 0:
                continue
            record = self.records[self.mapping[int(position)]]
            if resource_type and record["resource_type"] != resource_type:
                continue
            if (date_from or date_to) and record["date"] is None:
                continue
            if date_from and record["date"] < str(date_from):
                continue
            if date_to and record["date"] > str(date_to):
                continue
            # Without a type filter, don't repeat the same underlying lab as both
            # a DocumentReference and a DiagnosticReport, or repeat text chunks.
            dedup = (record["patient_id"], record["source_id"])
            if dedup in seen:
                continue
            seen.add(dedup)
            result.append({k: v for k, v in record.items() if k != "text"} | {"score": round(float(np.clip(score, -1, 1)), 4)})
            if len(result) == limit:
                break
        return result
