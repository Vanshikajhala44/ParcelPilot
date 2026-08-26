from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import uuid

from .structured_data import AccessDeniedError, StructuredDataAccess


@dataclass
class PendingAction:
    action_id: str
    action_type: str
    target_type: str
    target_id: str
    details: dict[str, Any]
    status: str = "pending_confirmation"
    requires_confirmation: bool = True
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    created_by: str = ""
    account_id: str | None = None
    executed_by: str | None = None


class ActionTool:
    """Mock state-changing action system that requires explicit confirmation before execution."""

    VALID_ACTION_TYPES = {"create_escalation", "update_ticket", "create_follow_up_task"}

    def __init__(self, project_root: str | Path, user: dict[str, Any] | None = None):
        self.project_root = Path(project_root).resolve()
        self.user = user or {"user_id": "internal-operator", "role": "support", "account_ids": []}
        self.data_service = StructuredDataAccess(self.project_root, user=self.user)
        self.pending_actions: dict[str, PendingAction] = {}
        self.audit_log: list[dict[str, Any]] = []

    def _resolve_account_id(self, target_type: str, target_id: str) -> str | None:
        if target_type == "ticket":
            ticket = self.data_service.lookup_data("ticket", target_id)
            return ticket.get("account_id")
        if target_type == "account":
            account = self.data_service.lookup_data("account", target_id)
            return account.get("account_id")
        if target_type == "order":
            order = self.data_service.lookup_data("order", target_id)
            return order.get("account_id")
        return None

    def _require_account_scope(self, target_type: str, target_id: str) -> str | None:
        account_id = self._resolve_account_id(target_type, target_id)
        if account_id is None:
            return None
        if self.user.get("role") in {"admin", "ops", "manager", "support_lead"}:
            return account_id
        allowed = set(self.user.get("account_ids") or [])
        if not allowed:
            return account_id
        if account_id not in allowed:
            raise AccessDeniedError(
                f"User '{self.user.get('user_id')}' is not authorized for account {account_id}."
            )
        return account_id

    def prepare_action(
        self,
        *,
        action_type: str,
        target_type: str,
        target_id: str,
        details: dict[str, Any],
    ) -> dict[str, Any]:
        action_type = (action_type or "").strip()
        if action_type not in self.VALID_ACTION_TYPES:
            raise ValueError(f"Unsupported action type: {action_type}")

        target_type = (target_type or "").strip().lower()
        if target_type not in {"account", "order", "ticket"}:
            raise ValueError(f"Unsupported target type: {target_type}")

        self._require_account_scope(target_type, target_id)

        action = PendingAction(
            action_id=f"ACT-{uuid.uuid4().hex[:8].upper()}",
            action_type=action_type,
            target_type=target_type,
            target_id=target_id,
            details=dict(details or {}),
            created_by=self.user.get("user_id", "anonymous"),
            account_id=self._resolve_account_id(target_type, target_id),
        )
        self.pending_actions[action.action_id] = action

        self.audit_log.append(
            {
                "action_id": action.action_id,
                "event": "prepared",
                "action_type": action.action_type,
                "target_type": target_type,
                "target_id": target_id,
                "created_by": action.created_by,
                "created_at": action.created_at,
            }
        )

        return {
            "action_id": action.action_id,
            "action_type": action.action_type,
            "target_type": action.target_type,
            "target_id": action.target_id,
            "details": dict(action.details),
            "status": action.status,
            "requires_confirmation": action.requires_confirmation,
            "created_by": action.created_by,
            "account_id": action.account_id,
        }

    def execute_action(self, action_id: str, *, confirmed: bool = False) -> dict[str, Any]:
        action = self.pending_actions.get(action_id)
        if action is None:
            raise KeyError(f"Unknown action id: {action_id}")

        if not confirmed:
            action.status = "rejected"
            raise ValueError(f"Action {action_id} requires explicit confirmation before execution.")

        action.status = "completed"
        action.executed_by = self.user.get("user_id", "anonymous")

        payload = {
            "action_id": action.action_id,
            "action_type": action.action_type,
            "target_type": action.target_type,
            "target_id": action.target_id,
            "details": dict(action.details),
            "status": action.status,
            "executed_by": action.executed_by,
            "created_by": action.created_by,
            "account_id": action.account_id,
        }

        self.audit_log.append(
            {
                "action_id": action.action_id,
                "event": "executed",
                "action_type": action.action_type,
                "target_type": action.target_type,
                "target_id": action.target_id,
                "executed_by": action.executed_by,
                "executed_at": datetime.now(timezone.utc).isoformat(),
            }
        )

        return payload

    def list_pending_actions(self) -> list[dict[str, Any]]:
        return [
            {
                "action_id": action.action_id,
                "action_type": action.action_type,
                "target_type": action.target_type,
                "target_id": action.target_id,
                "status": action.status,
                "created_by": action.created_by,
            }
            for action in self.pending_actions.values()
            if action.status == "pending_confirmation"
        ]

    def get_pending_action(self, action_id: str) -> dict[str, Any] | None:
        action = self.pending_actions.get(action_id)
        if action is None:
            return None
        return {
            "action_id": action.action_id,
            "action_type": action.action_type,
            "target_type": action.target_type,
            "target_id": action.target_id,
            "details": dict(action.details),
            "status": action.status,
            "created_by": action.created_by,
            "account_id": action.account_id,
        }

    def cancel_action(self, action_id: str) -> dict[str, Any]:
        """Reject a prepared action without executing it; keeps an audit trail."""
        action = self.pending_actions.get(action_id)
        if action is None:
            raise KeyError(f"Unknown action id: {action_id}")
        if action.status == "completed":
            raise ValueError(f"Action {action_id} was already executed and cannot be cancelled.")
        action.status = "rejected"
        self.audit_log.append(
            {
                "action_id": action.action_id,
                "event": "cancelled",
                "action_type": action.action_type,
                "target_type": action.target_type,
                "target_id": action.target_id,
                "cancelled_by": self.user.get("user_id", "anonymous"),
                "cancelled_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return {
            "action_id": action.action_id,
            "status": action.status,
            "message": f"Action {action.action_id} was cancelled before execution.",
        }
