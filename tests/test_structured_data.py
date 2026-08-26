import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from parcelpilot.structured_data import AccessDeniedError, StructuredDataAccess


ROOT = Path(__file__).resolve().parents[1]


def test_loads_workbook_and_extracts_schema():
    service = StructuredDataAccess(ROOT)
    schema = service.schema_summary()

    assert schema["sheets"] == ["README", "accounts", "orders", "tickets"]
    assert "accounts" in schema["tables"]
    assert "orders" in schema["tables"]
    assert "tickets" in schema["tables"]
    assert schema["snapshot_timestamp"] == "2026-08-16 11:00 Asia/Kolkata"


def test_lookup_account_data_returns_expected_record():
    service = StructuredDataAccess(ROOT)
    account = service.lookup_data("account", "ACCT-001")

    assert account["account_id"] == "ACCT-001"
    assert account["account_name"] == "Northstar Logistics"
    assert account["contract_file"] == "05_Northstar_Logistics_Enterprise_Agreement.pdf"


def test_lookup_order_data_and_cancellation_fee_are_calculated():
    service = StructuredDataAccess(ROOT)

    order = service.lookup_data("order", "ORD-1001")
    assert order["account_id"] == "ACCT-001"
    assert order["status"] == "BOOKED"
    assert order["cancellation_fee_inr"] == 0.0

    order_2001 = service.lookup_data("order", "ORD-2001")
    assert order_2001["account_id"] == "ACCT-002"
    assert order_2001["cancellation_fee_inr"] == 250.0


def test_ticket_lookup_is_restricted_by_role_and_scope():
    admin = StructuredDataAccess(ROOT, user={"user_id": "alice", "role": "admin", "account_ids": []})
    support = StructuredDataAccess(ROOT, user={"user_id": "bob", "role": "support", "account_ids": ["ACCT-001"]})
    no_access = StructuredDataAccess(ROOT, user={"user_id": "charlie", "role": "support", "account_ids": ["ACCT-003"]})

    assert admin.lookup_data("ticket", "TKT-501")["ticket_id"] == "TKT-501"
    assert support.lookup_data("ticket", "TKT-501")["ticket_id"] == "TKT-501"

    try:
        no_access.lookup_data("ticket", "TKT-501")
        raise AssertionError("Expected AccessDeniedError")
    except AccessDeniedError:
        pass


def test_account_lookup_respects_scope_and_redacts_sensitive_fields():
    support = StructuredDataAccess(ROOT, user={"user_id": "bob", "role": "support", "account_ids": ["ACCT-001"]})
    data = support.lookup_data("account", "ACCT-001")

    assert data["account_name"] == "Northstar Logistics"
    assert "notes" in data
    assert "csm" in data

    no_access = StructuredDataAccess(ROOT, user={"user_id": "charlie", "role": "support", "account_ids": ["ACCT-003"]})
    try:
        no_access.lookup_data("account", "ACCT-001")
        raise AssertionError("Expected AccessDeniedError")
    except AccessDeniedError:
        pass
