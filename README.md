# Grey-Zone Underwriting Agent — first slice

The agent's brain only: schemas (N1) + Judge/decision-table/grounding-gate (N5) + one test fixture (N12).
No WhatsApp, no journey, no vendors. Data is mocked. Spec: `files/Agent_Build_Specification.md` §5/§6.

## Setup (already done once)
    python -m venv .venv
    .venv\Scripts\python.exe -m pip install -r requirements.txt

## Run the deterministic tests (NO API key needed)
    .venv\Scripts\python.exe -m pytest -v

Proves the decision table + grounding gate + path resolver. The live-AI test skips without a key.

## Run the live Vikram case (needs a key)
1. Open `.env`, set `LLM_MODEL` and paste your API key.
2. Run:

    .venv\Scripts\python.exe -m pytest -v -s -k vikram

Feeds Vikram's mock case through the real Judge (2 AI calls, hard-capped): cycle 1 flags the
9-day-declined velocity pattern, the mock ABHA gather returns an undisclosed cardiac record,
cycle 2 escalates it to a human. Prints the actual rulings.

## Files
- `agent.py` — the whole brain in one file.
- `tests/test_agent.py` — deterministic checks + the live Vikram smoke test.
- `tests/fixtures/vikram_mehta.json` — one mock grey-zone case (the eval-set seed, N12).
- `.env` — your key (gitignored).
