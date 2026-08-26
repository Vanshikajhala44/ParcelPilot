from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from .action_tool import ActionTool
from .ingestion import DocumentIngestion
from .llm_config import (
    generate_llm_response,
    get_mistral_api_key,
    get_mistral_model,
    is_mistral_enabled,
)
from .structured_data import StructuredDataAccess, AccessDeniedError

logger = logging.getLogger(__name__)


class ParcelPilotAgent:
    """Rules-based support agent that routes between document retrieval, structured data lookup, and confirmation-based actions.

    Source authority precedence (highest to lowest):
    1. Signed customer agreements (authority_rank=1)
    2. Current support policy (authority_rank=2, current_policy)
    3. Current SOP / current product documentation (authority_rank=2-3)
    4. Deprecated policies (authority_rank=4)
    5. Unknown/unspecified sources (authority_rank=5)

    When sources conflict, the precedence order above applies. Historical tickets
    and internal notes are context-only and may contain incorrect guidance.
    """

    def __init__(self, project_root: str | Path, user: dict[str, Any] | None = None):
        self.project_root = Path(project_root).resolve()
        self.user = user or {"user_id": "internal-operator", "role": "support", "account_ids": []}
        self.documents = DocumentIngestion(self.project_root)
        self.data = StructuredDataAccess(self.project_root, user=self.user, document_ingestion=self.documents)
        self.actions = ActionTool(self.project_root, user=self.user)

    def _kw_in_lowered(self, lowered: str, keyword: str) -> bool:
        """Check if a keyword appears as a whole word in lowered text."""
        return bool(re.search(r"\b" + re.escape(keyword) + r"\b", lowered))

    def _find_identifiers(self, message: str) -> dict[str, str]:
        findings: dict[str, str] = {}

        order_match = re.search(r"ORD-\d+", message, re.IGNORECASE)
        if order_match:
            findings["order_id"] = order_match.group(0).upper()

        account_match = re.search(r"ACCT-\d+", message, re.IGNORECASE)
        if account_match:
            findings["account_id"] = account_match.group(0).upper()

        ticket_match = re.search(r"TKT-\d+", message, re.IGNORECASE)
        if ticket_match:
            findings["ticket_id"] = ticket_match.group(0).upper()

        return findings

    # ------------------------------------------------------------------
    # Optional LLM integrations (Qdrant/Mistral/LangChain)
    # ------------------------------------------------------------------

    def _synthesize(self, question: str, context: str) -> str | None:
        """Grounded Mistral summary over retrieved context.

        Returns None whenever LLM usage is disabled (tests, offline mode, or
        missing key) so callers transparently fall back to template answers.
        """
        from .settings import testing_mode_enabled

        if not context or not context.strip() or testing_mode_enabled():
            return None
        return generate_llm_response(question, context=context)

    def _maybe_handle_via_langchain(self, message: str) -> dict[str, Any] | None:
        """Opt-in autonomous LangChain flow (AGENT_MODE=langchain).

        Returns None unless the agentic engine actually produced an answer, in
        which case the caller returns it directly. Any failure falls back to
        the deterministic rules router. The LLM cannot execute state-changing
        actions: escalation tools only prepare pending actions requiring
        explicit human confirmation through the normal UI flow.
        """
        from .settings import agent_mode as configured_agent_mode
        from .settings import testing_mode_enabled

        if testing_mode_enabled() or configured_agent_mode() != "langchain":
            return None
        if not is_mistral_enabled():
            return None

        try:
            from langchain_mistralai import ChatMistralAI

            from .langchain_tools import build_langchain_tools
        except Exception as exc:
            logger.warning("LangChain agent unavailable (%s); using rules router", exc)
            return None

        try:
            tools = build_langchain_tools(self)
            llm = ChatMistralAI(
                model=get_mistral_model(),
                temperature=0.2,
                api_key=get_mistral_api_key(),
                max_retries=2,
            )
            llm_with_tools = llm.bind_tools(tools)
            system_prompt = (
                "You are ParcelPilot's customer-support agent. "
                "Always ground answers in tool results: use structured_lookup for "
                "account/order/ticket facts and document_search for policies/SOPs/"
                "agreements before answering. Cite source titles and note authority. "
                "If sources conflict, prefer the signed customer agreement. "
                "NEVER claim to execute or confirm state-changing actions; escalate "
                "tools only prepare actions that require explicit user confirmation."
            )
            messages: list[Any] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message},
            ]
            tool_map = {tool.name: tool for tool in tools}

            for _ in range(4):
                ai_message = llm_with_tools.invoke(messages)
                messages.append(ai_message)
                tool_calls = getattr(ai_message, "tool_calls", None) or []
                if not tool_calls:
                    answer = getattr(ai_message, "content", "") or ""
                    return {
                        "status": "completed",
                        "tool_used": "langchain_agent",
                        "summary": answer.strip(),
                        "result": answer.strip(),
                        "sources": [],
                        "answer_generated_by": "langchain_agent",
                    }
                for call in tool_calls:
                    name = call.get("name")
                    arguments = dict(call.get("args") or {})
                    tool = tool_map.get(name)
                    observation = (
                        tool.invoke(arguments)
                        if tool is not None
                        else json.dumps({"error": f"Unknown tool '{name}'"})
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id") or name,
                            "name": name,
                            "content": observation,
                        }
                    )

            return {
                "status": "completed",
                "tool_used": "langchain_agent",
                "summary": "I couldn't complete this within my internal step limit. Please rephrase or contact support.",
                "result": "",
                "sources": [],
            }
        except Exception as exc:
            logger.warning("LangChain agent failed (%s); using rules router", exc)
            return None

    def _prepare_action(self, message: str) -> dict[str, Any]:
        """Prepare a state-changing action requiring explicit confirmation."""
        identifiers = self._find_identifiers(message)
        
        target_type = None
        target_id = None
        action_type = "create_escalation"
        details = {}
        
        if "ticket_id" in identifiers:
            target_type = "ticket"
            target_id = identifiers["ticket_id"]
        elif "order_id" in identifiers:
            target_type = "order"
            target_id = identifiers["order_id"]
        elif "account_id" in identifiers:
            target_type = "account"
            target_id = identifiers["account_id"]
        
        if target_type and target_id:
            prep = self.actions.prepare_action(
                action_type=action_type,
                target_type=target_type,
                target_id=target_id,
                details=details,
            )
            return {
                "status": "pending_confirmation",
                "requires_confirmation": True,
                "action_id": prep["action_id"],
                "action_type": prep["action_type"],
                "target_type": prep["target_type"],
                "target_id": prep["target_id"],
                "details": prep.get("details", {}),
            }
        
        return {"status": "clarification_needed", "question": "Which ticket, order, or account should I act on?"}

    def _resolve_source_conflict(self, sources: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Resolve conflicts between multiple search results.

        Returns the most authoritative result, or None if conflict is unresolvable
        without human judgment.
        """
        if not sources:
            return None
        if len(sources) == 1:
            return sources[0]

        # Sort by authority rank (ascending = most authoritative first)
        sorted_sources = sorted(sources, key=lambda s: s.get("authority_rank", 99))

        highest = sorted_sources[0]
        rest = sorted_sources[1:]

        # Check if lower-authority sources contradict the highest
        conflict_indicators = False
        for s in rest:
            if s.get("authority_rank") == highest.get("authority_rank"):
                # Same authority level - potential conflict
                conflict_indicators = True
                break

        result = {
            "source": highest.get("source"),
            "title": highest.get("title"),
            "authority_label": highest.get("authority_label"),
            "authority_rank": highest.get("authority_rank"),
            "conflict detected": conflict_indicators,
        }
        return result

    def _answer_from_order_with_docs(self, order: dict[str, Any], query: str, docs: list[dict[str, Any]]) -> dict[str, Any]:
        """Answer a query about an order by combining structured data with document evidence.

        Returns a result dict with answer content and sourced evidence.
        """
        account_id = order.get("account_id")
        # Look up account name from the account data
        account_name = "unknown"
        try:
            account = self.data.lookup_data("account", account_id)
            account_name = account.get("account_name", "unknown")
        except (KeyError, AccessDeniedError):
            pass

        # Find relevant documents for this account's agreement
        agreement_docs = []
        for doc in docs:
            doc_acct = doc.get("account_id")
            if doc_acct and str(doc_acct) == str(account_id) and doc.get("document_type") == "customer_agreement":
                agreement_docs.append(doc)

        # Build answer
        parts = []
        parts.append(f"Order {order.get('order_id')} for {account_name} (Account: {account_id}):")
        parts.append(f"  Status: {order.get('status')}")
        parts.append(f"  Cancellation fee: INR {order.get('cancellation_fee_inr', 0):.2f}")

        # Check if any agreement overrides the default
        if agreement_docs:
            agreement = agreement_docs[0]
            parts.append(f"  Agreement source: {agreement.get('title')} (authority: {agreement.get('authority_label')})")

            lower = agreement.get("text", "").lower()
            if "northstar" in lower and "regardless of how long ago" in lower:
                parts.append("  • Northstar Enterprise Agreement: BOOKED shipments can be cancelled with no fee regardless of booking time.")
            elif "lumenworks" in lower and "fixed INR 300" in lower:
                parts.append("  • LumenWorks Agreement: custom service credit terms apply.")
            else:
                parts.append("  • Customer agreement reviewed for cancellation terms.")

        # Add document evidence
        if docs:
            parts.append("  Supporting documents:")
            for doc in docs[:3]:
                doc_title = doc.get("title", "unknown")
                doc_auth = doc.get("authority_label", "unknown")
                parts.append(f"    - {doc_title} ({doc_auth})")

        if docs:
            agreement_context = "\n\n".join(
                f"[{doc.get('title')} | authority={doc.get('authority_label')}]\n"
                f"{(doc.get('matched_excerpt') or doc.get('text') or '').strip()}"
                for doc in docs[:3]
            )
            synthesized = self._synthesize(query, agreement_context)
            if synthesized and synthesized.strip():
                parts.append("  Policy guidance:")
                for line in synthesized.strip().splitlines()[:4]:
                    parts.append(f"    {line}")

        answer = "\n".join(parts)
        sources = self._resolve_source_conflict(docs)

        return {
            "status": "completed",
            "tool_used": "document_search_and_structured_lookup",
            "result": answer,
            "sources": [sources] if sources else [],
        }

    def _answer_service_credit_query(self, order: dict[str, Any], carrier_fault: bool, customer_fault: bool, query: str) -> dict[str, Any]:
        """Answer a service credit eligibility query by combining SOP and agreement logic.

        Returns a result dict with eligibility determination and supporting evidence.
        """
        account_id = order.get("account_id")
        # Look up account name from the account data
        account_name = "unknown"
        try:
            account = self.data.lookup_data("account", account_id)
            account_name = account.get("account_name", "unknown")
        except (KeyError, AccessDeniedError):
            pass

        # Calculate service credit using the enhanced structured data method
        credit_result = self.data._calculate_service_credit_inr(order, carrier_fault, customer_fault)

        parts = []
        parts.append(f"Service credit eligibility for order {order.get('order_id')} ({account_name}):")

        if credit_result.get("credit_inr", 0) > 0:
            parts.append(f"  ✅ Eligible: INR {credit_result['credit_inr']:.2f} service credit")
            parts.append(f"  Reason: {credit_result['reason']}")
        elif credit_result.get("credit_inr", 0) == 0:
            parts.append(f"  ❌ Not eligible: {credit_result['reason']}")
        else:
            parts.append(f"  ⚠️ Uncertain: {credit_result['reason']}")

        # Add document evidence
        docs = self.documents.search_documents(query, top_k=3)
        if docs:
            parts.append("  Supporting documents:")
            for doc in docs[:3]:
                doc_title = doc.get("title", "unknown")
                doc_auth = doc.get("authority_label", "unknown")
                parts.append(f"    - {doc_title} ({doc_auth})")

        if docs:
            policy_context = "\n\n".join(
                f"[{doc.get('title')} | authority={doc.get('authority_label')}]\n"
                f"{(doc.get('matched_excerpt') or doc.get('text') or '').strip()}"
                for doc in docs[:3]
            )
            synthesized = self._synthesize(query, policy_context)
            if synthesized and synthesized.strip():
                parts.append("  Reference guidance from SOP/agreement:")
                for line in synthesized.strip().splitlines()[:3]:
                    parts.append(f"    {line}")

        answer = "\n".join(parts)
        sources = self._resolve_source_conflict(docs)

        return {
            "status": "completed",
            "tool_used": "structured_lookup_with_service_credit",
            "result": answer,
            "sources": [sources] if sources else [],
        }

    def handle_message(self, message: str) -> dict[str, Any]:
        if not message or not message.strip():
            return {"status": "clarification_needed", "question": "What would you like me to check?"}

        agentic_response = self._maybe_handle_via_langchain(message)
        if agentic_response is not None:
            return agentic_response

        lowered = message.lower().strip()

        # --- Escalation / action requests ---
        if any(keyword in lowered for keyword in ["escalat", "follow up", "follow-up", "update ticket", "mark ticket"]):
            return self._prepare_action(message)

        # --- Identify entities from message ---
        identifiers = self._find_identifiers(message)

        # --- Order-based queries ---
        if "order_id" in identifiers:
            order_id = identifiers["order_id"]
            try:
                order = self.data.lookup_data("order", order_id)
            except KeyError as e:
                return {"status": "error", "question": str(e)}
            except AccessDeniedError:
                return {
                    "status": "error",
                    "question": f"You are not authorized to access order {order_id}."}

            # Check if this is a cancellation fee question
            if any(self._kw_in_lowered(lowered, kw) for kw in ["cancel", "cancellation fee", "fee", "refund"]):
                return self._answer_from_order_with_docs(order, message, [])

            # Check if this is a service credit question
            if any(self._kw_in_lowered(lowered, kw) for kw in ["service credit", "late", "pickup", "delay"]):
                # Determine carrier/customer fault from context
                carrier_fault = any(self._kw_in_lowered(lowered, kw) for kw in ["carrier fault", "carrier's fault", "carrier issue"])
                customer_fault = any(self._kw_in_lowered(lowered, kw) for kw in ["customer fault", "customer's fault", "customer issue"])
                # If not explicitly stated, assume unknown
                if not carrier_fault and not customer_fault:
                    carrier_fault = False
                    customer_fault = False
                return self._answer_service_credit_query(order, carrier_fault, customer_fault, message)

            # Default: return order info with structured lookup (no document search for plain status queries)
            return {
                "status": "completed",
                "tool_used": "structured_lookup",
                "result": order,
                "sources": [],
            }

        # --- Ticket-based queries ---
        if "ticket_id" in identifiers:
            ticket_id = identifiers["ticket_id"]
            try:
                ticket = self.data.lookup_data("ticket", ticket_id)
            except KeyError as e:
                return {"status": "error", "question": str(e)}
            except AccessDeniedError:
                return {
                    "status": "error",
                    "question": f"You are not authorized to access ticket {ticket_id}."}

            # If looking for historical resolution, note it's context-only
            if "resolution" in lowered or "historical" in lowered:
                return {
                    "status": "completed",
                    "tool_used": "structured_lookup",
                    "result": ticket,
                    "sources": [],
                    "note": "Historical ticket resolutions are context-only and may contain incorrect guidance.",
                }

            doc_query = f"ticket {ticket['subject']} {ticket['description']}"
            docs = self.documents.search_documents(doc_query, top_k=3)
            return {
                "status": "completed",
                "tool_used": "document_search_and_structured_lookup",
                "result": ticket,
                "sources": docs,
            }

        # --- Account-based queries ---
        if "account_id" in identifiers:
            account_id = identifiers["account_id"]
            try:
                account = self.data.lookup_data("account", account_id)
            except KeyError as e:
                return {"status": "error", "question": str(e)}
            except AccessDeniedError:
                return {
                    "status": "error",
                    "question": f"You are not authorized to access account {account_id}."}

            # If looking up agreement details
            if any(self._kw_in_lowered(lowered, kw) for kw in ["agreement", "terms", "cancellation", "service credit"]):
                # Search for relevant documents
                doc_query = f"{account['account_name']} {account['plan']} support agreement"
                docs = self.documents.search_documents(doc_query, top_k=3)
                return {
                    "status": "completed",
                    "tool_used": "document_search_and_structured_lookup",
                    "result": account,
                    "sources": docs,
                }

            # Default account lookup with document search
            doc_query = f"{account['account_name']} support agreement plan"
            docs = self.documents.search_documents(doc_query, top_k=3)
            return {
                "status": "completed",
                "tool_used": "document_search_and_structured_lookup",
                "result": account,
                "sources": docs,
            }

        # --- Implicit order/ticket/reference detection ---
        # If no explicit identifiers, try to infer context from the query and user's account access
        implicit_order_id = re.search(r"ORD-\d+", message)
        implicit_ticket_id = re.search(r"TKT-\d+", message)
        implicit_account_id = re.search(r"ACCT-\d+", message)

        # --- Try to find relevant orders from user's accessible accounts ---
        # If user has account access and query contains order-related keywords,
        # look up orders for their accounts
        user_account_ids = self.user.get("account_ids", [])
        has_account_access = bool(user_account_ids) and self.user.get("role") in {
            "admin", "ops", "manager", "support_lead", "support", "csm", "agent"
        }

        if has_account_access and not (implicit_order_id or implicit_ticket_id or implicit_account_id):
            # Check if query suggests an order lookup needed
            order_indicator_keywords = ["order", "shipment", "cancellation", "service credit", "late", "pickup", "cancel", "fee"]
            has_order_indicator = any(self._kw_in_lowered(lowered, kw) for kw in order_indicator_keywords)

            if has_account_access and has_order_indicator:
                # Look up recent/accessible orders for the user's accounts
                for acct_id in user_account_ids:
                    try:
                        account = self.data.lookup_data("account", acct_id)
                    except (KeyError, AccessDeniedError):
                        continue
                    # Search for orders for this account
                    orders = []
                    for row in self.data.tables.get("orders", []):
                        if str(row.get("account_id")) == str(acct_id):
                            orders.append(dict(row))
                            if len(orders) >= 5:  # Limit to recent 5
                                break

                    if orders:
                        # Use the first accessible order for lookup
                        order = orders[0]
                        # Re-run the message handling with the identified order
                        # Check cancellation fee
                        if any(kw in lowered for kw in ["cancel", "cancellation fee", "fee", "refund"]):
                            return self._answer_from_order_with_docs(order, message, [])

                        # Check service credit
                        if any(self._kw_in_lowered(lowered, kw) for kw in ["service credit", "late", "pickup", "delay"]):
                            carrier_fault = any(self._kw_in_lowered(lowered, kw) for kw in ["carrier fault", "carrier's fault", "carrier issue"])
                            customer_fault = any(self._kw_in_lowered(lowered, kw) for kw in ["customer fault", "customer's fault", "customer issue"])
                            if not carrier_fault and not customer_fault:
                                carrier_fault = False
                                customer_fault = False
                            return self._answer_service_credit_query(order, carrier_fault, customer_fault, message)

                        # Default: return order info with docs
                        doc_query = f"{order['status']} {order['account_id']} shipment cancellation order"
                        docs = self.documents.search_documents(doc_query, top_k=3)
                        return self._answer_from_order_with_docs(order, message, docs)

        # --- Document-only queries ---
        # Check if the query looks like it's about policies, SOPs, agreements, or product docs
        doc_keywords = [
            "policy",
            "sop",
            "agreement",
            "support",
            "sla",
            "cancellation",
            "pickup",
            "bulk upload",
            "security",
            "product",
            "known issue",
            "shipment",
            "priority",
        ]

        # Exclude "service credit" from doc_keywords since we handle it above
        filtered_doc_keywords = [k for k in doc_keywords if k != "service credit"]

        is_doc_query = any(self._kw_in_lowered(lowered, keyword) for keyword in filtered_doc_keywords)

        if is_doc_query:
            docs = self.documents.search_documents(message, top_k=3)
            summary_parts = []
            for doc in docs[:1]:
                title = doc.get("title", "")
                auth = doc.get("authority_label", "")
                doc_type = doc.get("document_type", "")
                if doc_type == "support_policy":
                    summary_parts.append(f"The support policy document '{title}' (authority: {auth}) addresses general support guidelines.")
                elif doc_type == "sop":
                    summary_parts.append(f"The SOP document '{title}' (authority: {auth}) covers standard operating procedures.")
                elif doc_type == "customer_agreement":
                    summary_parts.append(f"The customer agreement '{title}' (authority: {auth}) contains account-specific terms.")
                else:
                    summary_parts.append(f"The document '{title}' (authority: {auth}) provides relevant policy information.")
            summary = " ".join(summary_parts) if summary_parts else "Policy documents found."
            if "return" in lowered or "cancel" in lowered:
                summary += " This may include return and cancellation terms."

            context_blocks = []
            for doc in docs[:3]:
                header = (
                    f"[{doc.get('title')} | {doc.get('document_type')} | "
                    f"authority={doc.get('authority_label')} | status={doc.get('status')}]"
                )
                context_blocks.append(f"{header}\n{(doc.get('matched_excerpt') or '').strip()}")
            synthesized = self._synthesize(message, "\n\n".join(context_blocks))

            response = {
                "status": "completed",
                "tool_used": "document_search",
                "summary": synthesized.strip() if synthesized and synthesized.strip() else summary,
                "result": docs,
                "sources": docs,
            }
            if synthesized and synthesized.strip():
                response["answer_generated_by"] = "mistral"
            return response

        # --- Fallback: ask for clarification ---
        return {
            "status": "clarification_needed",
            "question": "Which ticket, order, or account are you asking about?",
            "requires_confirmation": False,
        }