"""Tax Invoice Scanner — fourth use case.

The user points their camera at a tax invoice / bill. Claude Vision reads
it (printed AND handwritten) and extracts the IBAN, BIC, recipient (the
gov't or person demanding the tax), amount, and reference. The agent
speaks a question out loud — "Looks like a €X invoice from <recipient>.
Pay it?" — opens the mic, and on YES executes a real bunq IBAN payment.

Unlike the other missions, this one doesn't run through the Claude
tool-loop — it's a tight 4-step linear worker thread (Vision → TTS →
voice yes/no → bunq pay). The mission entry exists so the dashboard can
show a display name + the mission appears in /health and /state.
"""

TAX_MISSION = {
    "name": "tax",
    "display_name": "Tax Invoice Scan",
    # No system_prompt or default_user_prompt — the flow is custom-orchestrated
    # in `server._run_tax_scan` rather than via the agent loop.
    "system_prompt": "",
    "default_user_prompt": "",
}
