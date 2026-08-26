from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .structured_data import AccessDeniedError, StructuredDataAccess


INTERNAL_ROLES = {"admin", "ops", "manager", "support_lead", "support"}

# First-response targets from Support Policy v3 CURRENT (minutes).
SLA_TARGET_MINUTES = {"P1": 15, "P2": 60, "P3": 240, "P4": 480}

# Agreement-based first-response overrides keyed by account_id (minutes).
# Derived from signed agreements in doc/, which outrank the general policy.
ACCOUNT_SLA_OVERRIDES: dict[str, dict[str, int]] = {
    "ACCT-001": {"P1": 15, "P2": 60},
}

# Keyword rules used to infer ticket priority. First match wins and the matched
# rule is kept so classification stays explainable and auditable.
PRIORITY_RULES: list[tuple[str, tuple[str, ...]]] = (
    (
        "P1",
        (
            "p1",
            "outage",
            "all shipment",
            "all users",
            "everyone",
            "http 500",
            "is failing",
            "completely failing",
            "down",
            "data loss",
            "key exposure",
            "credential exposure",
            "security",
        ),
    ),
    (
        "P2",
        (
            "p2",
            "bulk upload",
            "fails for",
            "blocked from",
            "unable to complete",
            "payments failing",
        ),
    ),
    (
        "P3",
        ("how do we", "how do i", "question about", "billing contact", "change the"),
    ),
)

ISSUE_CLUSTERS: list[tuple[str, tuple[str, ...]]] = (
    ("Bulk upload failures", ("bulk upload", "csv upload", "row csv")),
    ("Shipment creation errors", ("shipment creation", "create shipment", "creating any shipment", "http 500")),
    ("Tracking / status sync issues", ("stuck as", "shows booked", "tracking not updating", "status not updating", "status sync")),
    ("Pickup delays and missed pickups", ("pickup", "driver", "delay")),
    ("Cancellation requests", ("cancel", "cancellation")),
    ("Billing and invoicing questions", ("invoice", "billing contact", "charge", "gst", "refund")),
    ("API, keys and integrations", ("api key", "webhook", "token", "integration")),
)


class NotAuthorizedError(PermissionError):
    """Raised when a non-internal user attempts proactive-detection analytics."""


def parse_naive_datetime(value: Any) -> datetime | None:
    """Parse workbook datetime strings; workbook times are Asia/Kolkata-local."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    match = re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", text)
    if match:
        try:
            return datetime.strptime(match.group(0), "%Y-%m-%d %H:%M")
        except ValueError:
            return None
    return None


class ProactiveIssueDetector:
    """Detects recurring, urgent, and unusual issues across ParcelPilot support activity.

    All analysis runs strictly over the supplied workbook, anchored to the dataset
    snapshot time declared in the README sheet. Internal-only surfaces enforce role
    checks here so unauthorised callers cannot obtain aggregates that leak
    cross-account information.
    """

    def __init__(self, project_root: str | Path, user: dict[str, Any] | None = None,
                 data_service: StructuredDataAccess | None = None):
        self.project_root = Path(project_root).resolve()
        self.user = user or {"user_id": "internal-operator", "role": "ops", "account_ids": []}
        self.data = data_service or StructuredDataAccess(self.project_root, user=self.user)
        self.snapshot_time = parse_naive_datetime(self.data.snapshot_timestamp) or datetime(2026, 8, 16, 11, 0)

    # ------------------------------------------------------------------ auth --
    def ensure_internal_access(self) -> None:
        role = str(self.user.get("role", "")).lower()
        if role not in INTERNAL_ROLES:
            raise NotAuthorizedError(
                "Proactive issue detection is restricted to authorised ParcelPilot staff."
            )

    def _visible_tickets(self) -> list[dict[str, Any]]:
        """Return rows already narrowed to the user's data-layer account scope."""
        tickets: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in self.data.tables.get("tickets", []):
            ticket_id = row.get("ticket_id")
            if not ticket_id or str(ticket_id) in seen:
                continue
            try:
                self.data._require_account_access(row.get("account_id"))
            except AccessDeniedError:
                continue
            seen.add(str(ticket_id))
            tickets.append(dict(row))
        return tickets

    def _visible_orders(self) -> list[dict[str, Any]]:
        orders: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in self.data.tables.get("orders", []):
            order_id = row.get("order_id")
            if not order_id or str(order_id) in seen:
                continue
            try:
                self.data._require_account_access(row.get("account_id"))
            except AccessDeniedError:
                continue
            seen.add(str(order_id))
            orders.append(dict(row))
        return orders

    def _account_name(self, account_id: Any) -> str:
        for row in self.data.tables.get("accounts", []):
            if str(row.get("account_id")) == str(account_id):
                return str(row.get("account_name") or account_id)
        return str(account_id or "unknown")

    # ------------------------------------------------------------- priority --
    def infer_priority(self, subject: Any, description: Any) -> tuple[str, str]:
        """Return (priority, matched_rule_text). Explainable keyword heuristic."""
        text = f"{subject or ''} {description or ''}".lower()
        for priority, keywords in PRIORITY_RULES:
            for keyword in keywords:
                if re.search(r"\b" + re.escape(keyword) + r"\b", text):
                    return priority, keyword
        return "P3", "default"

    def _sla_target_minutes(self, priority: str, account_id: Any) -> tuple[int, str]:
        overrides = ACCOUNT_SLA_OVERRIDES.get(str(account_id))
        if overrides and priority in overrides:
            return overrides[priority], "customer agreement override"
        return SLA_TARGET_MINUTES.get(priority, 480), "standard support policy v3"

    # ----------------------------------------------------------- detectors --
    def analyse_sla(self, tickets: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        """SLA exposure for open tickets. Returns (findings, at_risk_count)."""
        findings: list[dict[str, Any]] = []
        for ticket in tickets:
            if str(ticket.get("status", "")).lower() != "open":
                continue
            created_at = parse_naive_datetime(ticket.get("created_at"))
            if created_at is None:
                continue
            priority, rule = self.infer_priority(ticket.get("subject"), ticket.get("description"))
            target_minutes, source = self._sla_target_minutes(priority, ticket.get("account_id"))
            age_minutes = max(0.0, (self.snapshot_time - created_at).total_seconds() / 60.0)
            state = None
            if age_minutes > target_minutes:
                state = "breached"
            elif age_minutes >= 0.8 * target_minutes:
                state = "at_risk"
            if state is None:
                continue
            security_related = rule in {"security", "key exposure", "data loss"}
            findings.append(
                {
                    "ticket_id": str(ticket.get("ticket_id")),
                    "account_id": str(ticket.get("account_id")),
                    "account_name": self._account_name(ticket.get("account_id")),
                    "subject": str(ticket.get("subject") or ""),
                    "priority": priority,
                    "priority_rule_matched": rule,
                    "age_minutes": round(age_minutes),
                    "target_minutes": target_minutes,
                    "sla_state": state,
                    "target_source": source,
                    "security_related": security_related,
                    "severity": "CRITICAL" if (state == "breached" and (priority == "P1" or security_related)) else "HIGH",
                }
            )
        severity_rank = {"CRITICAL": 2, "HIGH": 1}
        findings.sort(
            key=lambda f: (
                -severity_rank.get(f["severity"], 0),
                -(f["age_minutes"] / max(1, f["target_minutes"])),
            )
        )
        at_risk_count = sum(1 for f in findings if f["sla_state"] == "at_risk")
        return findings, at_risk_count

    def detect_recurring_issues(self, tickets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Group tickets into recurring issue themes with recency vs history split."""
        recent_cutoff = self.snapshot_time - timedelta(hours=24)
        history_start = self.snapshot_time - timedelta(days=90)
        clusters: list[dict[str, Any]] = []

        for label, keywords in ISSUE_CLUSTERS:
            recent: list[dict[str, Any]] = []
            earlier: list[dict[str, Any]] = []
            for ticket in tickets:
                haystack = f"{ticket.get('subject') or ''} {ticket.get('description') or ''}".lower()
                if not any(keyword in haystack for keyword in keywords):
                    continue
                created_at = parse_naive_datetime(ticket.get("created_at"))
                record = {
                    "ticket_id": str(ticket.get("ticket_id")),
                    "account": self._account_name(ticket.get("account_id")),
                    "status": str(ticket.get("status") or ""),
                    "created_at": str(ticket.get("created_at") or ""),
                    "subject": str(ticket.get("subject") or ""),
                }
                if created_at and created_at >= recent_cutoff:
                    recent.append(record)
                elif created_at and history_start <= created_at < recent_cutoff:
                    earlier.append(record)
            if not recent:
                continue
            distinct_accounts = sorted({r["account"] for r in recent} | {r["account"] for r in earlier})
            severity = "HIGH" if (len(recent) >= 2 or earlier) else "MEDIUM"
            if label.startswith(("Shipment creation", "API")):
                severity = "CRITICAL"
            trend = f"new ({len(recent)} in last 24h)"
            if earlier:
                if len(recent) == len(earlier):
                    trend = f"flat vs history ({len(earlier)} prior occurrence)"
                elif len(recent) > len(earlier):
                    delta = round((len(recent) - len(earlier)) / len(earlier) * 100)
                    trend = f"up ~{delta}% vs prior occurrences ({len(earlier)})"
                else:
                    trend = f"down vs prior occurrences ({len(earlier)})"
            clusters.append(
                {
                    "issue": label,
                    "recent_tickets_24h": len(recent),
                    "historical_occurrences": len(earlier),
                    "distinct_accounts": distinct_accounts,
                    "affected_customer_count": len({r["account"] for r in recent}),
                    "trend": trend,
                    "severity": severity,
                    "evidence": recent[:6],
                    "historical_evidence": earlier[:3],
                }
            )
        rank = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1}
        clusters.sort(key=lambda c: (-rank[c["severity"]], -c["recent_tickets_24h"]))
        return clusters

    def detect_order_anomalies(self, orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Unusual operational patterns: missed/late pickups, post-pickup cancels, fees."""
        anomalies: list[dict[str, Any]] = []

        def truthy(value: Any) -> bool:
            return str(value).strip().lower() in {"true", "yes", "1"}

        for order in orders:
            pickup_actual = parse_naive_datetime(order.get("pickup_actual_at"))
            window_end = parse_naive_datetime(order.get("pickup_window_end"))
            booked_at = parse_naive_datetime(order.get("booked_at"))
            cxl_at = parse_naive_datetime(order.get("cancellation_requested_at"))

            if pickup_actual and window_end and pickup_actual > window_end:
                hours_late = (pickup_actual - window_end).total_seconds() / 3600.0
                if hours_late > 0.25:
                    credit = self.data.estimate_service_credit(
                        order, truthy(order.get("carrier_fault")), truthy(order.get("customer_fault"))
                    )
                    anomalies.append(
                        {
                            "type": "late_pickup",
                            "severity": "HIGH" if hours_late > 2 else "MEDIUM",
                            "order_id": str(order.get("order_id")),
                            "account": self._account_name(order.get("account_id")),
                            "detail": f"Picked up {hours_late:.1f}h past the window.",
                            "credit_exposure_inr": credit.get("credit_inr"),
                            "credit_basis": credit.get("reason"),
                        }
                    )

            if not pickup_actual and window_end and self.snapshot_time > window_end and truthy(order.get("carrier_fault")):
                credit = self.data.estimate_service_credit(order, True, truthy(order.get("customer_fault")))
                anomalies.append(
                    {
                        "type": "missed_pickup_carrier_fault",
                        "severity": "HIGH",
                        "order_id": str(order.get("order_id")),
                        "account": self._account_name(order.get("account_id")),
                        "detail": "Pickup missed; carrier accepted fault and no pickup has occurred yet.",
                        "credit_exposure_inr": credit.get("credit_inr"),
                        "credit_basis": credit.get("reason"),
                    }
                )

            if cxl_at and pickup_actual and cxl_at > pickup_actual:
                minutes_after = (cxl_at - pickup_actual).total_seconds() / 60.0
                anomalies.append(
                    {
                        "type": "post_pickup_cancellation",
                        "severity": "MEDIUM",
                        "order_id": str(order.get("order_id")),
                        "account": self._account_name(order.get("account_id")),
                        "detail": f"Cancellation requested {minutes_after:.0f} min after pickup; return-to-origin process applies.",
                        "credit_exposure_inr": None,
                        "credit_basis": None,
                    }
                )

            if cxl_at and booked_at and not pickup_actual:
                elapsed_minutes = (cxl_at - booked_at).total_seconds() / 60.0
                try:
                    enriched = self.data.get_order(str(order.get("order_id")))
                    fee = float(enriched.get("cancellation_fee_inr") or 0)
                except (KeyError, ValueError):
                    fee = 0.0
                if fee > 0 and elapsed_minutes <= 60:
                    anomalies.append(
                        {
                            "type": "cancellation_fee_applied",
                            "severity": "MEDIUM",
                            "order_id": str(order.get("order_id")),
                            "account": self._account_name(order.get("account_id")),
                            "detail": f"INR {fee:.0f} fee with cancellation requested only {elapsed_minutes:.0f} min after booking - dispute watch.",
                            "credit_exposure_inr": None,
                            "credit_basis": None,
                        }
                    )

        rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        anomalies.sort(key=lambda a: rank[a["severity"]])
        return anomalies

    def prioritized_findings(
        self,
        sla_findings: list[dict[str, Any]],
        clusters: list[dict[str, Any]],
        anomalies: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """One merged, ranked attention list across every detector."""
        merged: list[dict[str, Any]] = []
        for f in sla_findings:
            why = (
                f"{f['priority']} first-response target is {f['target_minutes']} min "
                f"({f['target_source']}); ticket is {f['age_minutes']} min old. "
            )
            why += "Security-relevant report needs an immediate owner." if f["security_related"] else "Customer-visible impact risk."
            merged.append(
                {
                    "kind": "sla",
                    "severity": f["severity"],
                    "title": f"SLA breach risk on {f['ticket_id']} ({f['account_name']})",
                    "why_it_matters": why,
                    "evidence": f,
                }
            )
        for c in clusters:
            why = (
                f"{c['recent_tickets_24h']} tickets in the last 24h; trend: {c['trend']}; "
                f"affects {', '.join(c['distinct_accounts'])}. Recurrence suggests a systemic "
                "product or carrier issue worth a proactive fix."
            )
            merged.append(
                {
                    "kind": "recurring_issue",
                    "severity": c["severity"],
                    "title": c["issue"],
                    "why_it_matters": why,
                    "evidence": c,
                }
            )
        for a in anomalies:
            exposure = ""
            if a.get("credit_exposure_inr"):
                exposure = f" Potential service-credit exposure INR {a['credit_exposure_inr']:.0f} ({a.get('credit_basis') or ''})."
            merged.append(
                {
                    "kind": "order_anomaly",
                    "severity": a["severity"],
                    "title": f"{a['type'].replace('_', ' ').title()} - {a['order_id']} ({a['account']})",
                    "why_it_matters": a["detail"] + exposure,
                    "evidence": a,
                }
            )

        severity_rank = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1, "LOW": 0}
        merged.sort(key=lambda m: (-severity_rank[m["severity"]], m["kind"]))
        for idx, item in enumerate(merged, start=1):
            item["rank"] = idx
        return merged

    def generate_insights(self) -> dict[str, Any]:
        """Top-level entry point used by the internal Operations Intelligence view."""
        self.ensure_internal_access()
        tickets = self._visible_tickets()
        orders = self._visible_orders()

        sla_findings, at_risk_count = self.analyse_sla(tickets)
        clusters = self.detect_recurring_issues(tickets)
        anomalies = self.detect_order_anomalies(orders)
        prioritized = self.prioritized_findings(sla_findings, clusters, anomalies)

        open_tickets = sum(1 for t in tickets if str(t.get("status", "")).lower() == "open")
        breached = sum(1 for f in sla_findings if f["sla_state"] == "breached")

        return {
            "summary": {
                "open_tickets": open_tickets,
                "sla_breached": breached,
                "sla_at_risk": at_risk_count,
                "recurring_issue_clusters": len(clusters),
                "order_anomalies": len(anomalies),
                "snapshot_time": str(self.data.snapshot_timestamp),
                "scope_user": str(self.user.get("user_id", "")),
                "scope_role": str(self.user.get("role", "")),
            },
            "sla_findings": sla_findings,
            "issue_clusters": clusters,
            "order_anomalies": anomalies,
            "prioritized_findings": prioritized,
        }
