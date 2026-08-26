import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from parcelpilot.ingestion import DocumentIngestion


ROOT = Path(__file__).resolve().parents[1]


def test_document_sources_are_loaded_and_ranked():
    store = DocumentIngestion(ROOT)
    documents = store.load_documents()

    assert len(documents) >= 6

    current_policy = next(doc for doc in documents if doc["source"].endswith("01_Support_Policy_v3_CURRENT.pdf"))
    deprecated_policy = next(doc for doc in documents if doc["source"].endswith("02_Support_Policy_v2_DEPRECATED.pdf"))
    northstar = next(doc for doc in documents if doc["source"].endswith("05_Northstar_Logistics_Enterprise_Agreement.pdf"))

    assert current_policy["status"] == "current"
    assert deprecated_policy["status"] == "deprecated"
    assert northstar["doc_type"] == "customer_agreement"
    assert northstar["authority_rank"] < current_policy["authority_rank"]


def test_document_search_matches_policy_language():
    store = DocumentIngestion(ROOT)
    store.load_documents()

    results = store.search_documents("no cancellation fee before pickup")

    assert results
    assert any(r["source"].endswith("05_Northstar_Logistics_Enterprise_Agreement.pdf") for r in results)


def test_document_chunks_include_metadata():
    store = DocumentIngestion(ROOT)
    chunks = store.load_chunks()

    assert chunks
    first = chunks[0]
    assert "source" in first
    assert "doc_type" in first
    assert "status" in first
    assert "authority_rank" in first
