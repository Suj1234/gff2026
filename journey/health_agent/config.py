"""config.py — the ONLY tunable knobs for the health-triage agent (HEALTH_AGENT_PLAN.md §3).

`CONDITION_BUCKETS` is a fixed catalog: new condition buckets, trigger hints, or info
targets are a CONFIG EDIT, not a prompt-engineering exercise (same philosophy as
`underwriting.config.MEDICAL_GRID`).

`info_targets` are CLINICAL TOPICS an underwriter needs covered for that condition —
written in enough shorthand for a developer/underwriter to scan this file, NOT verbatim
question text. There is no `ask:`/pre-written-question field anywhere in this file
(HEALTH_AGENT_PLAN.md §0's whole point: what's fixed is the target INFORMATION, not the
target QUESTIONS or their order — the LLM decides phrasing + order + branching fresh
every turn, per `signatures.py`'s `NextAdaptiveQuestion`).

`trigger_hints` are PROMPT GUIDANCE — concrete examples shown to the triage LLM so its
reasoning stays grounded and citable. They are NOT a keyword whitelist filtering what
the LLM even sees: `TriageConditions` (`signatures.py`) receives the FULL raw facts
(all of NuralX's vitals/risk flags, the full ABHA free-text notes, the full OCR'd
prescription text) and reasons over the actual content — a drug or phrasing not listed
here can still trigger a bucket (HEALTH_AGENT_PLAN.md §3.1).
"""

from __future__ import annotations

CONDITION_BUCKETS: dict[str, dict] = {
    "cardiac": {
        "label": "Cardiac / heart condition",
        "trigger_hints": [
            "nuralx.risk_high_bp>=2", "nuralx.risk_cholesterol>=2",
            "abha.icd_prefix:I", "abha.drug:atorvastatin|amlodipine|metoprolol",
            "prescription.drug:beta_blocker|statin",
        ],
        "info_targets": [
            "onset (when first diagnosed, or approx. duration if unsure)",
            "current status (active / resolved / in remission / recurring — and if not "
            "active, when and how it resolved)",
            "current treatment (medication + dosage, or 'none' if resolved/untreated)",
            "severity markers (any hospitalization, ER visit, or procedure/surgery for it)",
            "control/stability (well-controlled vs. recent symptoms/flare-ups, IF still active)",
        ],
    },
    "hypertension": {
        "label": "Blood pressure / hypertension",
        "trigger_hints": ["nuralx.risk_high_bp>=1", "nuralx.bp_systolic>=140", "abha.icd_prefix:I10-I15"],
        "info_targets": [
            "onset (when first told BP was high)",
            "current status (still elevated / controlled / resolved)",
            "current treatment (medication + dosage, or lifestyle-only, or none)",
            "control/stability (recent readings or symptoms, IF still active)",
        ],
    },
    "diabetes": {
        "label": "Diabetes / blood sugar",
        "trigger_hints": [
            "nuralx.risk_hba1c>=2", "nuralx.risk_glucose>=2",
            "abha.icd_prefix:E10-E14", "abha.drug:metformin|insulin",
        ],
        "info_targets": [
            "onset + type (Type 1 or Type 2, if known)",
            "current status (active / well-controlled / poorly-controlled)",
            "current treatment (medication, insulin, or diet-controlled)",
            "complications (any eye, kidney, nerve, or cardiac complications ever noted)",
        ],
    },
    "respiratory": {
        "label": "Respiratory / asthma",
        "trigger_hints": ["nuralx.respiratory_rate_out_of_range", "abha.icd_prefix:J"],
        "info_targets": [
            "onset (when this started)",
            "current status (active / resolved / seasonal-only)",
            "current treatment (inhaler / medication / none)",
            "severity markers (any hospitalization or ER visit for a breathing attack)",
        ],
    },
    "thyroid": {
        "label": "Thyroid disorder",
        "trigger_hints": ["abha.icd_prefix:E00-E07", "abha.drug:levothyroxine", "prescription.drug:levothyroxine"],
        "info_targets": [
            "onset + type (underactive/hypo or overactive/hyper)",
            "current status (controlled / uncontrolled / resolved post-treatment)",
            "current treatment (medication + dosage, or none)",
        ],
    },
    "mental_health": {
        "label": "Mental health",
        "trigger_hints": ["abha.icd_prefix:F", "prescription.drug:ssri|benzodiazepine"],
        # Never triggered by face-scan/vitals — those don't diagnose mental health.
        "info_targets": [
            "onset (when treatment was first sought)",
            "current status (active / resolved / in remission)",
            "current treatment (medication and/or therapy, or none)",
            "severity markers (any hospitalization ever related to this)",
        ],
    },
    "renal_hepatic": {
        "label": "Kidney / liver condition",
        "trigger_hints": ["abha.icd_prefix:N", "abha.icd_prefix:K7"],
        "info_targets": [
            "onset (when diagnosed)",
            "current status and severity (stage, if known; on dialysis or not)",
            "current treatment (ongoing treatment or none)",
        ],
    },
    "oncology": {
        "label": "Cancer / tumour",
        "trigger_hints": ["abha.icd_prefix:C", "abha.icd_prefix:D0-D48"],
        # Thickened per underwriter review (HEALTH_AGENT_PLAN.md §revision): type/site +
        # diagnosis year alone are not enough to price a cancer history — STAGE and
        # TIME-SINCE-REMISSION are the two facts that actually drive the decision (an
        # early-stage, 5-years-clear history prices completely differently from a
        # late-stage, 8-months-clear one), and recurrence status is what separates
        # "resolved" from "monitored."
        "info_targets": [
            "type and site (what kind of cancer, where)",
            "diagnosis year and stage at diagnosis (if known — e.g. localized / spread / "
            "advanced; do not press hard for exact TNM staging, a general sense is enough)",
            "current treatment status (ongoing treatment / completed / in remission)",
            "if in remission: how long since treatment ended or remission was confirmed",
            "any recurrence since the original diagnosis, and if so when",
        ],
    },
}

MAX_TURNS_PER_CONDITION = 5  # hard cap on turns, NOT on which/how many facts fill per turn
# Was 4; bumped to 5 (2026-08-21) after the live Gemini smoke test showed a thorough,
# well-answered cardiac conversation (5 info_targets, the max of any bucket) can
# legitimately need a 5th turn to cover severity + control/stability separately —
# hitting the cap on a GOOD conversation isn't the intended failure mode; the cap is
# meant to catch a model that never converges, not to truncate a conversation going well.
MAX_CONDITIONS_PROBED = 4  # cap total conditions probed in one triage pass (cost + fatigue)
MAX_SECOND_PASS_BUCKETS = 2  # cap on new buckets from the volunteered-condition catch-all (§4.2)

# Underwriter build-note (not a prompt fix — a routing question for later): an ACTIVE or
# RECENT cancer history is exactly the kind of case this system's own doctrine reserves
# for a deterministic POSTPONE/REFER rule (files/CLAUDE.md, IMPLEMENTATION_PLAN.md's
# Core-6 outcomes), not a conversational close feeding straight to pricing. This agent
# still collects the facts either way (facts in, judgments out), but whether "recent
# oncology" should short-circuit into REFER/POSTPONE independent of this conversation's
# outcome is a `underwriting/rules.py` decision, out of scope for this module.
