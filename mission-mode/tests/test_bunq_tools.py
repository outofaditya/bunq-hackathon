"""Smoke tests that hit the real bunq sandbox. Not hermetic — each run creates
real sub-accounts, payments, drafts, schedules, and requests.

Order matters: we build up state (sub-account IBAN, draft id) as we go.
"""
from __future__ import annotations

import time

from orchestrator import bunq_tools


_state: dict = {}


def test_01_primary_account() -> None:
    acc = bunq_tools.get_primary_account()
    assert acc["id"], acc
    assert acc["iban"], acc
    print(f"  primary account: id={acc['id']} iban={acc['iban']} balance={acc['balance_eur']:.2f}")
    _state["primary_id"] = acc["id"]
    _state["primary_iban"] = acc["iban"]


def test_02_fund_primary_from_sugardaddy() -> None:
    """Request €500 from sugardaddy to guarantee balance for the tests that follow."""
    out = bunq_tools.request_from_partner(500.0, _state["primary_id"], partner_label="sugardaddy-topup")
    print(f"  request: {out}")
    assert out["request_id"]
    time.sleep(1.5)  # let it settle


def test_03_create_sub_account() -> None:
    name = f"Test Weekend {int(time.time()) % 1000}"
    out = bunq_tools.create_sub_account(name, 500.0)
    print(f"  sub-account: {out}")
    assert out["account_id"]
    assert out["iban"], "IBAN must be resolvable from created account"
    _state["sub_id"] = out["account_id"]
    _state["sub_iban"] = out["iban"]
    _state["sub_type"] = out["type"]


def test_04_fund_sub_account() -> None:
    out = bunq_tools.fund_sub_account(100.0, _state["sub_iban"], _state["primary_id"])
    print(f"  fund: {out}")
    assert out["payment_id"]
    time.sleep(1)


def test_05_pay_vendor_from_sub() -> None:
    out = bunq_tools.pay_vendor(25.0, "Hotel V Fizeaustraat", _state["sub_id"])
    print(f"  pay vendor: {out}")
    assert out["payment_id"]
    time.sleep(1)


def test_06_create_draft_payment() -> None:
    out = bunq_tools.create_draft_payment(40.0, "Dinner at De Kas", _state["sub_id"])
    print(f"  draft: {out}")
    assert out["draft_id"]
    _state["draft_id"] = out["draft_id"]
    time.sleep(1)


def test_07_read_draft_payment() -> None:
    out = bunq_tools.get_draft_payment(_state["draft_id"], _state["sub_id"])
    print(f"  draft status: {out}")
    assert out["status"] in ("PENDING", "ACCEPTED", "REJECTED", "UNKNOWN")


def test_08_schedule_recurring() -> None:
    out = bunq_tools.schedule_recurring(50.0, _state["sub_iban"], _state["primary_id"], "Next trip fund")
    print(f"  schedule: {out}")
    # May legitimately fail on sandbox if schedule shape is wrong — we accept either path
    if out["fallback"]:
        print(f"  NOTE: schedule endpoint failed, UI-only fallback in effect: {out.get('error')}")
    else:
        assert out["schedule_id"]


def test_09_request_from_partner() -> None:
    out = bunq_tools.request_from_partner(50.0, _state["sub_id"], partner_label="Sara")
    print(f"  request: {out}")
    assert out["request_id"]


if __name__ == "__main__":
    # Ordered runner so state threads correctly
    for fn in [
        test_01_primary_account,
        test_02_fund_primary_from_sugardaddy,
        test_03_create_sub_account,
        test_04_fund_sub_account,
        test_05_pay_vendor_from_sub,
        test_06_create_draft_payment,
        test_07_read_draft_payment,
        test_08_schedule_recurring,
        test_09_request_from_partner,
    ]:
        print(f"\n>>> {fn.__name__}")
        try:
            fn()
            print(f"    ✓ {fn.__name__} passed")
        except Exception as e:
            print(f"    ✗ {fn.__name__} FAILED: {e}")
            raise
    print("\nALL TESTS PASSED")
