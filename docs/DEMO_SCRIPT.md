# Demo Script (5 minutes)

## 1. Intro (30 seconds)

"This is ParcelPilot AI Operations Copilot. It helps support teams answer policy questions, look up order and ticket information, and prepare actions with explicit confirmation. The system emphasizes reliability, access control, and explainability - critical for operations workflows."

## 2. Architecture overview (60 seconds)

Show the main pieces:

- **Document ingestion** for PDFs with metadata-aware retrieval
- **Structured workbook lookup** for orders/accounts/tickets with access control
- **Agent routing logic** - routes between document search, structured lookup, and action flows
- **Confirmation-based action tool** - requires explicit user confirmation before execution
- **Small Streamlit interface** - chat interface with tool trace display

Key design decisions:

- Source authority hierarchy: agreements > policy > SOP > deprecated
- Historical tickets are context-only, not authoritative
- All actions require confirmation - no blind execution
- Access control enforced in data layer, not just model instructions

## 3. Policy question demo (60 seconds)

Ask: "What is the return policy?"

Highlight that the app uses the SOP and agreement documents and returns a concise summary, not just raw search results. Show the source hierarchy in action - current policy takes precedence over deprecated policies.

## 4. Structured data example (60 seconds)

Ask: "Can Northstar cancel ORD-1001 without a cancellation fee?"

Explain how the system combines:

- Order status from the workbook (ORD-1001, ACCT-001, BOOKED)
- Customer agreement override (Northstar Enterprise Agreement waives fee)
- Default SOP rule (INR 250 after 30 minutes cancellation)
- Source priority: agreement (rank 1) wins over policy (rank 2)

Demonstrate the tool trace showing each step.

## 5. Service credit demo (60 seconds)

Ask: "A pickup is three hours late because of carrier fault. Should I get a service credit?"

Show the combined logic:

- Order data (pickup window, actual pickup time)
- Carrier fault & customer fault determination
- Agreement override check (LumenWorks terms)
- Default SOP calculation (credit = lower of INR 500 or 10% of shipment fee)
- Display the reason and eligibility result

## 6. Action confirmation demo (60 seconds)

Ask: "Escalate ticket TKT-501 for a P1 outage."

Show the pending action and explain the explicit confirmation step before execution:

```
⚙ create_escalation
   TKT-501

✓ Ticket found
  Account: Northstar Logistics
  Severity: P1
  Reason: SLA breach risk

Proposed action:
Create escalation
Priority: HIGH
Reason: SLA breach risk

Confirm?

[ Confirm ] [ Cancel ]
```

Then:

User → Confirm
        ↓
create_escalation()
        ↓
ESC-023 created

## 7. Proactive dashboard (45 seconds)

Show the second page - Operations Intelligence:

```
Operations Intelligence
┌─────────────────────────────────────┐
│ Open Tickets              127       │
│ SLA At Risk                 9       │
│ High Severity               6       │
│ Repeated Issues            18       │
└─────────────────────────────────────┘

Top emerging issues
1. Pickup delay complaints
   14 tickets
   ↑ 180% vs previous period

2. Cancellation failures
   9 tickets
   3 customers affected

3. Label generation errors
   7 tickets
   HIGH PRIORITY
```

## 8. Design decisions talk (45 seconds)

Cover the key product and technical decisions:

- **Source hierarchy** - why agreements override policy
- **Access control** - role-based scoping (SUPPORT_AGENT can only see ACCT-001)
- **Confirmation workflow** - why explicit confirmation matters
- **Uncertainty handling** - what happens when no authoritative source found
- **Why historical tickets aren't authoritative** - examples of conflicting guidance
- **What would be built next** - proactive issue detection, alerting, dashboard

## 9. Close (30 seconds)

"The system is intentionally explainable and deterministic. It uses local, rule-based logic for safety and reliability. Mistral is optional and enabled through environment variables when configured."

"Questions?"