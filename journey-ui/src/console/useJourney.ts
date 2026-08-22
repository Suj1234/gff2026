import { useEffect, useState, useCallback } from "react"

// Data hooks for the console: fetch the app snapshot + poll the live rail.
// Falls back to a clearly-labeled seeded profile when the API returns an empty bundle
// (common for test numbers), so the demo always shows a populated Step 1.

export type Applicant = {
  name?: string; dob?: string; gender?: string; age?: number
  address?: string; pincode?: string; mobile?: string
}
export type Financial = {
  declared_annual_income?: number; source_of_funds?: string; purpose_of_cover?: string
}
export type Product = {
  plan?: string; product_type?: string; sum_assured?: number; tenure_years?: number
  riders?: { id: string; amount: number }[]
  premium?: number; payment_mode?: string
}
export type Signals = {
  pan_verify?: { pan?: string; pan_status?: string }
  mobile_intel?: { provider?: string; ported_recently?: boolean }
  epfo?: { uan?: string; employer?: string; employment_type?: string }
  gst?: { status?: string; gstin?: string; gstin_count?: number; any_cancelled?: boolean
    statuses?: string[]; turnover_slab?: string; registration_date?: string
    nature_of_business?: string[]; trade_name?: string }
  litigation_fir?: Record<string, unknown>
  mca_director?: { director_default?: boolean }
  email_intel?: { email?: string }
  aadhaar_ekyc?: { status?: string; name?: string; dob?: string; address?: string; photo?: boolean }
  account_aggregator?: { status?: string; imputed_annual_income?: number }
  rppg_scan?: {
    status?: string; consented?: boolean
    vitals?: { heart_rate?: number; respiratory_rate?: number; spo2?: number; bp?: { systolic?: number; diastolic?: number } | string }
    vitals_extra?: Record<string, number>   // display-only secondary vitals (HRV/stress/HbA1c/risk flags)
  }
  liveness_facematch?: { status?: string; liveness_pass?: boolean; liveness_score?: number; face_match_score?: number; deepfake_flag?: boolean }
  abha_health_records?: { status?: string; diagnoses?: string[]; icd_codes?: string[] }
  prescription_ocr?: { status?: string; drug_names?: string[]; diagnosis_notes?: string[] }
}
// Health-triage agent state (HEALTH_AGENT_PLAN.md §6-§7) — one flagged bucket + its
// thread state, as persisted server-side and echoed back by GET /app/{id}.
export type HealthThreadState = {
  bucket: string; trigger_fact: string
  transcript: { q: string; a: string }[]
  covered: string[]; turns_used: number
  done: boolean; ended_reason?: "complete" | "turn_cap" | null
  next_question?: string | null
  summary?: { onset?: string | null; current_status?: string | null; treatment?: string | null
    severity_notes?: string | null; free_text_summary?: string } | null
  unprompted_conditions?: string[]
}
export type HealthAgentState = {
  flagged?: { bucket: string; label?: string; trigger_fact: string; confidence?: string }[]
  threads?: Record<string, HealthThreadState>
  second_pass_run?: boolean
}
export type NomineeSnapshot = {
  name?: string; dob?: string; relationship?: string; share_pct?: number; address?: string
  appointee?: { name?: string; dob?: string; relationship?: string }
}
export type AppSnapshot = {
  success: boolean
  application_number?: string
  created_at?: string          // UTC ISO from the DB; rendered in IST
  current_step?: number
  applicant: Applicant
  financial?: Financial
  product?: Product
  nominees?: NomineeSnapshot[]  // Step 6 pre-fill on revisit
  health_declaration?: Record<string, unknown>   // Step 4 pre-fill on revisit (flat payload)
  health_agent?: HealthAgentState                 // Step 4 conversational deep-dive state
  bank_statement_upload?: { status?: "processing" | "done" | "error"; filename?: string; message?: string }
  status?: string
  signals: Signals
  seeded?: boolean
}

// ---- Step 5 decision report (underwriting/report.py ReportOutput; extra="allow", so
// sections + report carry arbitrary richer nested fields we render shape-tolerantly). ----
export type Section = { risk_level: string; sub_score?: number; weight?: number; findings?: string; assessed?: boolean; [k: string]: any }
export type SoftFlag = { flag_type: string; related_rule: string; severity?: string; reason_code?: string; reason?: string }
export type AmbiguousFlag = { flag_id?: string; flag_type: string; related_rule: string; context?: Record<string, any> }
export type ScoreRowT = { source_group: string; weight?: number; risk_sub_score?: number; contribution?: number; why?: string }
export type CitedEvidence = { claim: string; cited_source: string; ruling?: string | null; cycle?: number | null }
export type AuditEntry = { step: string; actor: string; timestamp?: string | null; detail: string }
export type DecisionReport = {
  report_meta?: Record<string, any>
  safety_score?: { value: number; band: string; bands?: Record<string, string>; _note?: string } | null
  scoring_breakdown?: ScoreRowT[]
  scoring_total?: Record<string, any>
  signals?: Record<string, any>
  sections?: Record<string, Section>
  risk_scores?: { fraud_score?: number; anomaly_score?: number; graph_score?: number; composite_band?: string; shap?: Record<string, number> } | null
  bre_result?: { outcome: string; hard_gate?: string | null; soft_flags?: SoftFlag[]; ambiguous_flags?: AmbiguousFlag[] } | null
  risk_and_fraud_verdict?: Record<string, any>
  decision?: { verdict: string; escalation_reason?: string | null; next_step?: string | null; reason_summary?: string; loading_pct?: number | null; loading_band?: string | null; indicative_loading_if_cleared?: string | null; secondary_flag?: string | null; reason_codes?: string[] } | null
  cited_evidence_chain?: CitedEvidence[]
  run_metadata?: { model?: string | null; prompt_version?: string | null; judge_cycles?: number } | null
  audit_log?: AuditEntry[]
}
export type DecisionResult = {
  success: boolean; pending_decision?: boolean
  verdict?: string; status?: string; waiting_on?: string | null
  safety_score?: number | null; report?: DecisionReport
}

export type RailContext = { label: string; value: string | null }
export type RailGroup = {
  key: string; label: string; sub_score: number
  severity: "ok" | "warn" | "bad" | "idle"; why: string
  context?: RailContext[]   // read-only sub-items (Financial: GST / vehicle / imputed income)
  gate?: boolean            // pass/fail gate chip (e.g. Cover/R-006) — show status, no 0-100 score
}
export type Rail = { safety_score: number | null; band: string | null; groups: RailGroup[]
  persona?: string; assessed_count?: number; total_count?: number }

const SEED: AppSnapshot = {
  success: true,
  application_number: "GFF-DEMO01",
  current_step: 1,
  seeded: true,
  applicant: {
    name: "Rajesh Kumar Menon", dob: "1986-04-12", gender: "male", age: 39,
    address: "14, Whitefield Main Road, Bengaluru, Karnataka", pincode: "560066",
    mobile: "9554259281",
  },
  signals: {
    pan_verify: { pan: "EKOPS9572K", pan_status: "valid" },
    mobile_intel: { provider: "Airtel", ported_recently: false },
    epfo: { uan: "100234567890", employer: "Infosys Ltd", employment_type: "salaried" },
    gst: {},
    litigation_fir: {},
    mca_director: { director_default: false },
    email_intel: { email: "" },
    aadhaar_ekyc: { status: "pending" },
  },
}

export function useAppSnapshot(appId: number | null) {
  const [snap, setSnap] = useState<AppSnapshot | null>(null)
  const load = useCallback(async () => {
    if (appId == null) { setSnap(SEED); return }        // no app yet -> pure demo preview only
    try {
      const r = await fetch(`/api/journey/app/${appId}`)
      const d = (await r.json()) as AppSnapshot
      // Show the REAL snapshot as-is. A thin result (mobile prefill returned no PAN) is a real
      // state the UI handles (PAN-entry sub-view) — do NOT paper over it with the demo SEED,
      // which is what made a live run look like mock data.
      if (d.success) setSnap(d)
      else setSnap({ ...SEED, seeded: true })            // API error only -> labeled demo fallback
    } catch { setSnap({ ...SEED, seeded: true }) }
  }, [appId])
  useEffect(() => { load() }, [load])
  return { snap, reload: load }
}

export function useRail(appId: number | null, step: number, si = 0, sub = 0) {
  const [rail, setRail] = useState<Rail | null>(null)
  useEffect(() => {
    if (appId == null) return
    let live = true
    // si (Step 2 only): the live-selected sum insured, so the Cover/R-006 chip reacts as the
    // underwriter toggles the cover — before Continue persists it. 0 = use the saved SI.
    const siq = si ? `&si=${si}` : ""
    // sub (Step 4 only): the active health sub-step (0 Health · 1 Vitals · 2 Face/ABHA), so
    // the rail scopes its chips to that sub-step's evidence — same as Steps 1–3 scope theirs.
    const subq = step === 4 ? `&sub=${sub}` : ""
    const poll = async () => {
      try {
        const r = await fetch(`/api/journey/rail/${appId}?step=${step}${siq}${subq}`, { headers: { "Cache-Control": "no-cache" } })
        const d = await r.json()
        if (live && d.success) setRail({ safety_score: d.safety_score, band: d.band, groups: d.groups || [],
          persona: d.persona, assessed_count: d.assessed_count, total_count: d.total_count })
      } catch { /* transient */ }
    }
    poll()
    const t = setInterval(poll, 4000)
    return () => { live = false; clearInterval(t) }
  }, [appId, step, si, sub])
  return rail
}
