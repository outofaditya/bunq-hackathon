"""CLI entry point for Phase 1 testing.

Usage:
    python -m orchestrator.run_mission weekend
    python -m orchestrator.run_mission weekend "€500 best weekend for me and Sara"
    python -m orchestrator.run_mission weekend --no-wait-draft
"""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

from bunq_client import BunqClient

from .agent_loop import run_mission
from .bunq_tools import BunqToolbox
from .missions import MISSIONS


def main() -> int:
    load_dotenv(override=True)

    parser = argparse.ArgumentParser(description="Run a Mission Mode cascade via CLI.")
    parser.add_argument("mission", choices=sorted(MISSIONS.keys()), help="Mission template to run.")
    parser.add_argument("user_prompt", nargs="?", default=None, help="Override the default user prompt.")
    parser.add_argument("--seed", type=float, default=0.0, help="Seed primary account with this many EUR from sugardaddy (default 0 = skip).")
    parser.add_argument("--no-wait-draft", action="store_true", help="Do NOT poll for draft-payment approval after the cascade.")
    parser.add_argument("--wait-seconds", type=float, default=60.0, help="How long to block waiting for draft approval (default 60s). Ctrl-C to exit sooner.")
    args = parser.parse_args()

    api_key = os.getenv("BUNQ_API_KEY", "").strip()
    if not api_key:
        print("BUNQ_API_KEY missing in .env", file=sys.stderr)
        return 1

    client = BunqClient(api_key=api_key, sandbox=True)
    client.authenticate()
    print(f"[cli] authenticated as user {client.user_id}")

    toolbox = BunqToolbox(client)
    print(f"[cli] primary account {toolbox.primary_id} IBAN {toolbox.primary_iban}")

    if args.seed > 0:
        print(f"[cli] seeding primary with €{args.seed:.2f} from sugardaddy…")
        toolbox.seed_primary(args.seed)
        toolbox.snapshot_balance(step_label="seed")

    mission = MISSIONS[args.mission]
    user_prompt = args.user_prompt or mission["default_user_prompt"]
    print(f"[cli] mission: {mission['display_name']}")
    print(f"[cli] user prompt: {user_prompt!r}")
    print()

    result = run_mission(
        toolbox=toolbox,
        system_prompt=mission["system_prompt"],
        user_prompt=user_prompt,
        wait_for_draft=not args.no_wait_draft,
        wait_timeout_s=args.wait_seconds,
    )

    print()
    print("=" * 60)
    print("Mission complete")
    print("=" * 60)
    print(f"  primary id: {result['primary_id']}")
    print(f"  primary IBAN: {result['primary_iban']}")
    if result["draft_id"]:
        print(f"  draft payment id: {result['draft_id']}")
    print(f"  narrations: {len(result['narrations'])}")
    for n in result["narrations"]:
        print(f"    • {n}")
    if result["final_summary"]:
        print(f"  final: {result['final_summary']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
