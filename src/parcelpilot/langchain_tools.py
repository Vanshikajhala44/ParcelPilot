"""LangChain tool wrappers around ParcelPilot's existing capabilities.

These tools expose the deterministic backing services to LLM agents
(``langchain_mistralai.ChatMistralAI`` via ``bind_tools``). Tools only ever
*prepare* state-changing actions; execution still flows through the explicit
human-confirmation workflow in ``action_tool.execute_action(confirmed=True)``,
so an autonomous agent cannot mutate state on its own.
"""
from __future__ import annotations

import json
from typing import Any, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from .structured_data import AccessDeniedError

_MARSHAL_ERRORS = (KeyError, AccessDeniedError, PermissionError)


class DocumentSearchInput(BaseModel):
    query: str = Field(
        description="Natural-language question about policies, SOPs, agreements or product docs."
    )
    top_k: int = Field(default=3, ge=1, le=10, description="How many snippets to return.")


class StructuredLookupInput(BaseModel):
    entity_type: Literal["account", "order", "ticket"] = Field(
        description="Which record type to look up."
    )
    entity_id: str = Field(description="Record id such as ACCT-001, ORD-1001 or TKT-501.")


class EscalationInput(BaseModel):
    target_type: Literal["account", "order", "ticket"] = Field(
        description="Kind of record the escalation is about."
    )
    target_id: str = Field(description="ID of the record to escalate.")


def build_langchain_tools(agent: Any) -> list[StructuredTool]:
    """Create LangChain-compatible tools bound to a ParcelPilotAgent instance."""

    def document_search(query: str, top_k: int = 3) -> str:
        docs = agent.documents.search_documents(query, top_k=top_k)
        if not docs:
            return json.dumps({"results": [], "note": "No matching documents found"})
        return json.dumps({"results": docs}, default=str)

    def structured_lookup(entity_type: str, entity_id: str) -> str:
        entity_type = entity_type.strip().lower()
        if entity_type not in {"account", "order", "ticket"}:
            return json.dumps(
                {"error": "entity_type must be 'account', 'order' or 'ticket'"}
            )
        try:
            record = agent.data.lookup_data(entity_type, entity_id.strip())
            return json.dumps(
                {"entity_type": entity_type, "record": record}, default=str
            )
        except _MARSHAL_ERRORS as exc:
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"})

    def prepare_escalation(target_type: str, target_id: str) -> str:
        try:
            prep = agent.actions.prepare_action(
                action_type="create_escalation",
                target_type=target_type.strip().lower(),
                target_id=target_id.strip().upper(),
                details={},
            )
        except _MARSHAL_ERRORS as exc:
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"})
        payload = {
            "status": "pending_confirmation",
            "requires_confirmation": True,
            "action_id": prep.get("action_id"),
            "action_type": prep.get("action_type"),
            "target_type": prep.get("target_type"),
            "target_id": prep.get("target_id"),
            "note": (
                "Prepared but NOT executed. The user must explicitly confirm "
                "this action before it can run."
            ),
        }
        return json.dumps(payload, default=str)

    return [
        StructuredTool.from_function(
            func=document_search,
            name="document_search",
            description=(
                "Search ParcelPilot policy/SOP/agreement/product documents and get "
                "ranked excerpts with provenance (authority rank, status, account)."
            ),
            args_schema=DocumentSearchInput,
        ),
        StructuredTool.from_function(
            func=structured_lookup,
            name="structured_lookup",
            description=(
                "Look up authoritative account/order/ticket facts from the operations "
                "workbook (respects access control)."
            ),
            args_schema=StructuredLookupInput,
        ),
        StructuredTool.from_function(
            func=prepare_escalation,
            name="prepare_escalation",
            description=(
                "Prepare a ticket/order/account escalation. Requires explicit user "
                "confirmation afterwards; this tool never executes anything."
            ),
            args_schema=EscalationInput,
        ),
    ]