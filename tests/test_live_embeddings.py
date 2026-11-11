"""Optional integration check with the real downloaded sentence-transformer."""
import os

import pytest

from ehr_media.config import data_dir
from ehr_media.search import DEFAULT_MODEL, SearchEngine, SentenceEncoder
from ehr_media.store import Store


@pytest.mark.integration
@pytest.mark.skipif(os.getenv("RUN_EMBEDDING_TESTS") != "1", reason="Set RUN_EMBEDDING_TESTS=1 after ingesting the synthetic fixtures")
def test_real_semantic_retrieval():
    store = Store(data_dir() / "ehr.sqlite")
    encoder = SentenceEncoder(DEFAULT_MODEL, data_dir() / "models")
    engine = SearchEngine.load(data_dir() / "index", store.patients(), encoder)
    cases = [("low blood count and depleted iron stores", "Leila Mercer"),
             ("poorly controlled blood sugar", "Daniel Rowan"),
             ("reduced renal filtration", "Elias Hart")]
    for query, name in cases:
        matches = engine.search(query)
        assert name in [m["patient_name"] for m in matches[:3]]
