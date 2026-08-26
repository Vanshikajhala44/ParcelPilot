"""Live integration tests for the Qdrant + Mistral RAG stack.

Skipped by default so CI/unit runs stay offline-deterministic. Enable with:

    PARCELPILOT_RAG_TESTS=1 pytest tests/test_vector_rag.py -q

Requires MISTRAL_API_KEY (embeddings) and optionally QDRANT_URL/keys
(when absent, the embedded in-memory Qdrant store is exercised).
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

ROOT = Path(__file__).resolve().parents[1]

RAG_ENABLED = os.getenv("PARCELPILOT_RAG_TESTS", "").strip().lower() in {"1", "true", "yes"}
MISTRAL_CONFIGURED = bool(os.getenv("MISTRAL_API_KEY"))


def _enable_live_rag() -> None:
    os.environ["PARCELPILOT_TESTING"] = "0"
    os.environ["USE_QDRANT"] = "true"


def _restore_offline_mode() -> None:
    os.environ["PARCELPILOT_TESTING"] = "1"


@pytest.mark.skipif(
    not (RAG_ENABLED and MISTRAL_CONFIGURED),
    reason="live RAG tests disabled (set PARCELPILOT_RAG_TESTS=1 and provide MISTRAL_API_KEY)",
)
def test_hybrid_search_returns_results_with_provenance():
    _enable_live_rag()
    try:
        from parcelpilot.ingestion import DocumentIngestion

        store = DocumentIngestion(ROOT)
        results = store.search_documents("how do refunds work if we cancel early?")

        assert results, "hybrid search returned no results"
        first = results[0]
        assert first["source"]
        assert isinstance(first["authority_rank"], int)
        assert first["matched_excerpt"]
    finally:
        _restore_offline_mode()


@pytest.mark.skipif(
    not (RAG_ENABLED and MISTRAL_CONFIGURED),
    reason="live RAG tests disabled (set PARCELPILOT_RAG_TESTS=1 and provide MISTRAL_API_KEY)",
)
def test_agent_completes_policy_question_with_hybrid_retrieval():
    _enable_live_rag()
    try:
        from parcelpilot.agent import ParcelPilotAgent

        agent = ParcelPilotAgent(ROOT, user={"user_id": "bob", "role": "support", "account_ids": ["ACCT-001"]})
        response = agent.handle_message("What is the support SLA for a P1 incident for Northstar?")
        assert response["status"] == "completed"
        assert response.get("sources")
    finally:
        _restore_offline_mode()