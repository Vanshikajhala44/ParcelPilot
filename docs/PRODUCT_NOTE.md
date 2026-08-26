# Product Note

## Chosen client problem

I focused on the trust and reliability problem, because a support agent is only useful if it answers correctly and does not overstep its authority.

The implementation handles this by:

- Preferring current policy and agreement documents
- Treating historical tickets as non-authoritative context
- Separating retrieval from action execution
- Enforcing access control in the data layer
- Requiring explicit confirmation before any state-changing action

## Additional work I would build next

1. **Proactive issue detection for the internal operations view** - Identify emerging issues before customers ask
2. **Slack or email alerting for repeated support patterns** - Notify team about recurring problems
3. **A case triage dashboard with SLA tracking** - Visual overview of support health
4. **Better evidence display for each answer** - Show exactly which sources supported the answer
5. **Role-aware permissioning for internal users** - Fine-grained access control based on role

## Intentionally left out

- A full enterprise auth system
- Real production deployment infrastructure
- Multilingual support
- Large-scale retrieval indexing or vector database scaling
- Real customer-facing CRM integrations

## One metric to judge usefulness

A strong metric would be: % of support questions answered correctly without manual escalation while preserving a low false-positive rate on actions.

In practice, this is a trust-and-operations metric: the product should help the support team move faster without creating unsafe or incorrect decisions.