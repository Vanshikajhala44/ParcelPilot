import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from parcelpilot.agent import ParcelPilotAgent


ROOT = Path(__file__).resolve().parents[1]


def test_agent_routes_document_questions_to_document_search():
    agent = ParcelPilotAgent(ROOT, user={"user_id": "bob", "role": "support", "account_ids": ["ACCT-001"]})
    response = agent.handle_message("What is the support SLA for a P1 incident for Northstar?")

    assert response["tool_used"] in {"document_search", "document_search_and_structured_lookup"}
    assert response["sources"]


def test_agent_summarizes_document_policy_answers_for_ui():
    agent = ParcelPilotAgent(ROOT, user={"user_id": "bob", "role": "support", "account_ids": ["ACCT-001"]})
    response = agent.handle_message("What is the return policy?")

    assert response["tool_used"] == "document_search"
    assert "summary" in response
    assert "cancel" in response["summary"].lower() or "return" in response["summary"].lower()


def test_agent_routes_order_queries_to_structured_lookup():
    agent = ParcelPilotAgent(ROOT, user={"user_id": "bob", "role": "support", "account_ids": ["ACCT-001"]})
    response = agent.handle_message("What is the status of order ORD-1001?")

    assert response["tool_used"] == "structured_lookup"
    assert response["result"]["order_id"] == "ORD-1001"


def test_agent_requires_confirmation_before_execution_for_action_requests():
    agent = ParcelPilotAgent(ROOT, user={"user_id": "bob", "role": "support", "account_ids": ["ACCT-001"]})
    response = agent.handle_message("Escalate ticket TKT-501 for a P1 outage.")

    assert response["requires_confirmation"] is True
    assert response["status"] == "pending_confirmation"
    assert response["action_id"].startswith("ACT-")


def test_agent_asks_for_clarification_when_target_is_missing():
    agent = ParcelPilotAgent(ROOT, user={"user_id": "bob", "role": "support", "account_ids": ["ACCT-001"]})
    response = agent.handle_message("What is the latest update?")

    assert response["status"] == "clarification_needed"
    assert "ticket" in response["question"].lower() or "order" in response["question"].lower() or "account" in response["question"].lower()
