# Architecture Note

This document explains the ParcelPilot assessment implementation: agent design, tool responsibilities, data handling, source reliability, and the key trade-offs made for a small, auditable support assistant.

## Agent design

The agent is an explicit, rules-first router that composes small tools to answer user queries deterministically. The core capabilities are:

- Document search and retrieval (policy, SOPs, agreements, product docs)
- Structured-data lookup and calculations (accounts, orders, tickets)
- State-changing action preparation and confirmation (escalations, ticket updates)
- Source reliability, conflict detection and resolution

Routing is implemented in `src/parcelpilot/agent.py`. The agent first extracts entity identifiers (account_id, order_id, ticket_id) from the user message, then selects tools based on intent heuristics:

- Factual/policy questions → Document search
- Account/order/ticket lookups → Structured data tool
- Requests that may change state → Prepare action, then ask for confirmation
- Multi-step questions → Sequential tool calls and an explicit plan returned to the user

### Source authority (1 = highest)

1. Signed customer agreements (customer-specific contracts)
2. Current support policy documents
3. Current SOPs and product documentation
4. Historical tickets and past resolutions (context-only)
5. Deprecated or superseded documents

The agent prefers higher-authority sources and surfaces conflicts when multiple top-level sources disagree.

## Tool design

Tools are small, testable components with clear responsibilities. The agent calls them; tools never call the model directly.

### DocumentSearch

- Indexes ingested, chunked text with metadata (document id, document_type, account_id, authority_rank, status)
- Default retrieval: hybrid ranking that merges keyword/score-based matching with Mistral (`mistral-embed`) semantic similarity served from Qdrant (embedded in-memory by default, or a Qdrant Cloud cluster when `QDRANT_URL` is configured); degrades gracefully to keyword-only when the embedding API or Qdrant is unreachable
- Returns: ranked snippets with provenance and authority metadata for the agent to reason over

### StructuredDataAccess

- Loads the provided workbook and exposes safe lookup methods: `get_account()`, `get_order()`, `get_ticket()`, `search_tickets()`
- Enforces account scoping and role checks at the data layer (not via prompts)
- Performs calculations (fees, credits, SLA windows) using dataset snapshot time found in the workbook README
- Implementation detail: workbook → in-memory tables (pandas/sqlite) → API layer for consistent, testable queries

### ActionTool

- Prepares state-changing operations (create_escalation, update_ticket, create_follow_up)
- Requires explicit user confirmation before execution; unconfirmed actions are kept in a pending queue
- Enforces account scope and logs prepared/executed actions for auditability

## Document and data handling

### Ingestion pipeline (assessment scope)

1. PDFs → PyMuPDF text extraction
2. Lightweight parsing & chunking (paragraph / section level)
3. Store chunks with provenance metadata and simple term-based indexes
4. Optional: embed chunks via Mistral embeddings API (`mistral-embed`) and persist vectors plus provenance payloads in Qdrant (cloud or embedded in-memory); unchanged corpora are detected via a stored fingerprint and not re-uploaded

This design avoids heavy external infra while remaining capable of multi-source retrieval and explainability.

### Provenance metadata per chunk

- `document`: filename
- `document_type`: e.g., `customer_agreement`, `support_policy`, `sop`, `product_doc`, `historical_ticket`
- `account_id` / `customer_name` when available
- `authority_rank`: integer (1..5)
- `status`: `active` / `deprecated` / `superseded`

## Source reliability & conflict handling

Principles:

- Never treat all sources equally; prefer higher-authority documents.
- Use historical tickets as context only and not as the final authority.
- Surface conflicts and explain the reasoning (which source won and why).
- Escalate to humans when no authoritative answer exists or when policy ambiguity could cause harm.

Conflict resolution (high level):

1. Gather candidate sources with authority ranks.
2. Select highest-authority source(s).
3. If a single highest-authority source is applicable, use it and cite it.
4. If multiple highest-authority sources disagree, present the conflict and recommend escalation.
5. If no authoritative source applies, avoid inventing answers and escalate.

## Multi-step workflows

The agent supports multi-step flows by composing tool calls and returning an explicit plan and intermediate results to the user. Example flow for "Can Northstar cancel ORD-1001 without fee?":

1. `get_order(ORD-1001)` → validate account
2. `get_account(account_id)` → find linked agreement
3. `DocumentSearch(query="cancellation fee", account_id=...)` → retrieve agreement and policy chunks
4. Apply conflict-resolution rules → decide which document governs
5. Return findings with citations and next recommended actions (escalate / apply fee / no fee)

## Trade-offs and rationale

- Deterministic, rules-driven routing improves auditability and makes the system safer for operational use.
- Lightweight retrieval (keyword + optional embeddings) avoids heavy infra and is sufficient for the assessment dataset.
- Access control in the data layer prevents accidental data leakage even if the model prompt is manipulated.
- Explicit confirmation for state changes reduces risk and creates an auditable trail for operations.

## Implementation notes and repo links

- Routing & orchestration: `src/parcelpilot/agent.py`
- Ingestion & document search: `src/parcelpilot/ingestion.py`
- Structured data layer: `src/parcelpilot/structured_data.py`
- Action tool: `src/parcelpilot/action_tool.py`
- Optional LLM adapter: `src/parcelpilot/llm_config.py` (provider-optional; local fallback enabled)

---

This architecture favors clarity, safety, and reproducibility for a small team delivering an operational support assistant.
