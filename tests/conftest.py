import numpy as np
import pytest


class TestEncoder:
    """Small deterministic test double. Production always uses a transformer."""
    name = "test-only-encoder"
    dimension = 4

    def chunks(self, text):
        return [text]

    def encode(self, texts):
        result = []
        for text in texts:
            lowered = text.lower()
            row = np.array([1 + lowered.count("hemoglobin") * 5, 1 + lowered.count("glucose") * 5,
                            1 + lowered.count("cough") * 5, 1 + lowered.count("kidney") * 5], dtype="float32")
            result.append(row / np.linalg.norm(row))
        return np.asarray(result, dtype="float32")


@pytest.fixture
def encoder():
    return TestEncoder()


@pytest.fixture
def imported(tmp_path, encoder):
    from ehr_media.cli import run_ingestion
    from ehr_media.config import ROOT
    from ehr_media.search import SearchEngine
    from ehr_media.store import Store
    files = sorted((ROOT / "data/synthetic").glob("*.json")) + sorted((ROOT / "data/synthetic").glob("*.csv"))
    run_ingestion(files, tmp_path, "demo", "demo", encoder)
    store = Store(tmp_path / "ehr.sqlite")
    return store, SearchEngine.load(tmp_path / "index", store.patients(), encoder)
