import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from parcelpilot.action_tool import ActionTool
from parcelpilot.structured_data import AccessDeniedError


ROOT = Path(__file__).resolve().parents[1]


def test_prepare_action_requires_confirmation_before_execute():
    tool = ActionTool(ROOT, user={"user_id": "bob", "role": "support", "account_ids": ["ACCT-001"]})
    action = tool.prepare_action(
        action_type="create_escalation",
        target_type="ticket",
        target_id="TKT-501",
        details={"reason": "Customer-facing outage", "severity": "P1"},
    )

    assert action["status"] == "pending_confirmation"
    assert action["requires_confirmation"] is True

    try:
        tool.execute_action(action["action_id"], confirmed=False)
        raise AssertionError("Expected action to require confirmation")
    except ValueError:
        pass


def test_execute_action_after_confirmation_updates_state_and_audit():
    tool = ActionTool(ROOT, user={"user_id": "bob", "role": "support", "account_ids": ["ACCT-001"]})
    action = tool.prepare_action(
        action_type="update_ticket",
        target_type="ticket",
        target_id="TKT-501",
        details={"field": "status", "value": "in_progress"},
    )

    result = tool.execute_action(action["action_id"], confirmed=True)

    assert result["status"] == "completed"
    assert result["executed_by"] == "bob"
    assert result["target_id"] == "TKT-501"
    assert len(tool.audit_log) >= 1


def test_action_tool_enforces_account_scope():
    tool = ActionTool(ROOT, user={"user_id": "charlie", "role": "support", "account_ids": ["ACCT-003"]})

    try:
        tool.prepare_action(
            action_type="create_follow_up_task",
            target_type="ticket",
            target_id="TKT-501",
            details={"summary": "Send status update"},
        )
        raise AssertionError("Expected account scope denial")
    except AccessDeniedError:
        pass
