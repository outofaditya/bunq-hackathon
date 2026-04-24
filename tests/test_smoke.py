"""Smoke tests — run before every demo to confirm the full chain is healthy.

Two tiers:
  * FAST — direct bunq-tool probes, no LLM spend. ~15s.
  * SLOW — full Claude cascade end-to-end. ~30-45s + small Anthropic cost.

Run all:          pytest tests/test_smoke.py -v
Fast only:        pytest tests/test_smoke.py -v -m "not slow"
Slow only:        pytest tests/test_smoke.py -v -m slow
As a script:      python -m tests.test_smoke           (runs everything)

Exit code 0 ⇒ all green.
"""

from __future__ import annotations

import os
import sys
import time

import pytest
from dotenv import load_dotenv

from bunq_client import BunqClient
from orchestrator.agent_loop import run_mission
from orchestrator.bunq_tools import BunqToolbox
from orchestrator.events import bus
from orchestrator.missions import MISSIONS


load_dotenv(override=True)


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------

@pytest.fixture(scope="module")
def client() -> BunqClient:
    api_key = os.getenv("BUNQ_API_KEY", "").strip()
    assert api_key, "BUNQ_API_KEY missing in .env"
    c = BunqClient(api_key=api_key, sandbox=True)
    c.authenticate()
    assert c.user_id, "Auth succeeded but user_id not populated"
    return c


@pytest.fixture
def toolbox(client: BunqClient) -> BunqToolbox:
    bus.reset()
    return BunqToolbox(client)


# ----------------------------------------------------------------------
# FAST tests — no LLM
# ----------------------------------------------------------------------

def test_auth_populates_user_id(client: BunqClient) -> None:
    assert client.user_id is not None
    assert isinstance(client.user_id, int)


def test_primary_account_visible(toolbox: BunqToolbox) -> None:
    assert toolbox.primary_id is not None
    assert toolbox.primary_iban.startswith("NL")
    assert "BUNQ" in toolbox.primary_iban


def test_snapshot_balance_returns_expected_shape(toolbox: BunqToolbox) -> None:
    snap = toolbox.snapshot_balance("test_probe")
    assert snap["step"] == "test_probe"
    assert snap["primary_id"] == toolbox.primary_id
    assert isinstance(snap["primary_balance_eur"], float)


def test_seed_primary_increases_balance(toolbox: BunqToolbox) -> None:
    before = toolbox._balance_of(toolbox.primary_id)
    toolbox.seed_primary(10.0)  # small amount to keep sandbox tidy
    time.sleep(1.0)
    after = toolbox._balance_of(toolbox.primary_id)
    assert after - before == pytest.approx(10.0, abs=0.01), (
        f"Expected +€10.00 after seed, got before={before} after={after}"
    )


def test_pay_vendor_on_primary_moves_money(toolbox: BunqToolbox) -> None:
    # Make sure there's money to move.
    toolbox.seed_primary(10.0)
    time.sleep(1.0)

    before = toolbox._balance_of(toolbox.primary_id)
    result = toolbox.pay_vendor(
        amount_eur=5.0,
        vendor_name="SmokeTest Vendor",
        description="pytest smoke",
    )
    assert "payment_id" in result
    time.sleep(1.0)
    after = toolbox._balance_of(toolbox.primary_id)
    assert before - after == pytest.approx(5.0, abs=0.01)


def test_create_draft_payment_pending(toolbox: BunqToolbox) -> None:
    toolbox.seed_primary(10.0)
    time.sleep(1.0)

    before = toolbox._balance_of(toolbox.primary_id)
    result = toolbox.create_draft_payment(
        amount_eur=7.0,
        vendor_name="SmokeTest Draft",
        description="pytest smoke",
    )
    assert result["status"] == "PENDING"
    assert isinstance(result["draft_id"], int)

    # A pending draft should NOT have moved money yet.
    time.sleep(1.0)
    after = toolbox._balance_of(toolbox.primary_id)
    assert abs(before - after) < 0.01, (
        f"Draft should be pending but balance changed: before={before} after={after}"
    )


def test_bus_event_history_records_calls(toolbox: BunqToolbox) -> None:
    bus.reset()
    toolbox.pay_vendor(amount_eur=1.0, vendor_name="Ledger", description="bus event test")
    kinds = [e["type"] for e in bus._history]
    assert "step_started" in kinds
    assert "step_finished" in kinds


# ----------------------------------------------------------------------
# SLOW test — full Claude-driven Weekend cascade
# ----------------------------------------------------------------------

@pytest.mark.slow
def test_weekend_cascade_end_to_end(toolbox: BunqToolbox) -> None:
    bus.reset()
    # Seed €500 so the cascade has the expected budget.
    toolbox.seed_primary(500.0)
    time.sleep(1.0)

    before = toolbox._balance_of(toolbox.primary_id)

    result = run_mission(
        toolbox=toolbox,
        system_prompt=MISSIONS["weekend"]["system_prompt"],
        user_prompt=MISSIONS["weekend"]["default_user_prompt"],
        wait_for_draft=False,
    )

    # One draft payment should have been produced (Ticketmaster, €120).
    assert result["draft_id"] is not None, "Expected a draft_id from the Ticketmaster step"

    # Final summary from the agent.
    assert result["final_summary"], "Agent should have called finish_mission with a summary"

    # Event stream should include our three bunq-mutating steps + balance snapshots.
    finished = [e for e in bus._history if e["type"] == "step_finished"]
    tool_names = [e.get("tool") for e in finished]
    assert tool_names.count("pay_vendor") == 2, f"Expected 2 pay_vendor calls, got {tool_names}"
    assert tool_names.count("create_draft_payment") == 1, f"Expected 1 draft call, got {tool_names}"
    # Mission must NOT use excluded tools.
    for excluded in ("create_sub_account", "fund_sub_account", "create_bunqme_link",
                     "request_money", "schedule_recurring_payment"):
        assert excluded not in tool_names, f"{excluded} was called but shouldn't be in Weekend"

    # Balance deltas: €85 (Café de Klos) + €40 (Uber) = €125 moved, draft still pending.
    time.sleep(1.5)
    after = toolbox._balance_of(toolbox.primary_id)
    delta = before - after
    assert delta == pytest.approx(125.0, abs=0.5), (
        f"Expected €125 moved (excluding pending draft), got €{delta:.2f}"
    )

    # Balance snapshot events should exist for each mutating step.
    snaps = [e for e in bus._history if e["type"] == "balance_snapshot"]
    assert len(snaps) >= 3, f"Expected ≥3 balance snapshots, got {len(snaps)}"


# ----------------------------------------------------------------------
# Direct-run shim — `python -m tests.test_smoke`
# ----------------------------------------------------------------------

def _direct_run() -> int:
    """Invoke pytest programmatically for CLI runs."""
    return pytest.main(["-v", __file__])


if __name__ == "__main__":
    sys.exit(_direct_run())
