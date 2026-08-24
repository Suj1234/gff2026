import { useCallback, useEffect, useState } from "react"
import type { DecisionReport, DecisionResult, Section, SoftFlag, AmbiguousFlag, ScoreRowT, CitedEvidence, AuditEntry } from "./useJourney"
import { Gauge, type Tone } from "./AgentRail"
import { Modal } from "./Modal"
import {
  ArrowClockwise, CheckCircle, Warning, XCircle, Clock, ShieldWarning,
  Scales, Spinner, ArrowRight, ListChecks, CaretDown, Database,
} from "@phosphor-icons/react"

// STEP 5 — THE DECISION. POST /decide (live engine; grey-zone -> LLM ~seconds) then
// GET /decision -> render the FULL report, grouped so nothing is dropped:
//   1. Application summary  (report_meta: product, SI, premium, profile)
//   2. Verdict banner       (decision + loading + flags)
//   3. Risk overview        (safety gauge + band note, fraud/anomaly/graph + SHAP)
//   4. Safety-score breakdown TABLE (scoring_breakdown 11 rows + scoring_total footer)
//   5. Section assessments  (10 section cards; each with a collapsible drawer of the raw
//                            signals that fed it — signals nested in their section family)
//   6. Rule-engine flags    (soft + ambiguous grey-zone)
//   7. Risk & fraud narrative · 8. audit trail · [gauge -> cited-evidence modal]
//
// Sections/signals use extra="allow" (arbitrary nested shapes); the Field renderer handles
// any shape at any depth. A "Low"/"Not Assessed" section is NOT proof a source was checked
// (report.py KNOWN LIMITATION). ?mock=1 renders the canned rich report, no live engine.

const VERDICT: Record<string, { tone: "ok" | "warn" | "bad" | "brand" | "slate"; icon: any; title: string }> = {
  ISSUE: { tone: "ok", icon: CheckCircle, title: "Issue — clean" },
  ISSUE_WITH_LOADING: { tone: "brand", icon: Scales, title: "Issue with loading" },
  STEP_UP: { tone: "warn", icon: Clock, title: "Step up — evidence needed" },
  POSTPONE: { tone: "slate", icon: Clock, title: "Postpone" },
  REFER: { tone: "warn", icon: ShieldWarning, title: "Refer to underwriter" },
  DECLINE: { tone: "bad", icon: XCircle, title: "Decline" },
}
const BANNER: Record<string, string> = {
  ok: "border-emerald-200 bg-emerald-50 text-emerald-800",
  warn: "border-amber-200 bg-amber-50 text-amber-800",
  bad: "border-red-200 bg-red-50 text-red-800",
  brand: "border-primary/30 bg-primary/[0.06] text-primary",
  slate: "border-border bg-secondary/50 text-muted-foreground",
}
const gaugeTone = (band?: string): Tone =>
  band === "Low Risk" ? "ok" : band === "Moderate Risk" ? "warn" : band ? "bad" : "idle"

const LEVEL: Record<string, string> = {
  Low: "stat-ok", Moderate: "stat-warn", High: "stat-bad",
  "Not Assessed": "bg-muted text-muted-foreground border-border",
}
const SEV: Record<string, string> = {
  critical: "stat-bad", high: "stat-bad", moderate: "stat-warn", medium: "stat-warn",
  low: "stat-ok", clean: "stat-ok", none: "stat-ok",
}
const statFor = (word?: string) => LEVEL[word || ""] || SEV[(word || "").toLowerCase()] || "bg-muted text-muted-foreground border-border"

const title = (s: string) => s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
const inr = (n: number) => "₹" + n.toLocaleString("en-IN")
// ₹ figure -> lakh/crore words for the headline amounts
function amt(n?: number): string {
  if (n == null) return "—"
  if (n >= 10_000_000) return `₹${(n / 10_000_000).toFixed(n % 10_000_000 ? 2 : 0)} Cr`
  if (n >= 100_000) return `₹${(n / 100_000).toFixed(n % 100_000 ? 1 : 0)} L`
  return inr(n)
}
function fmt(v: any): string {
  if (v == null) return "—"
  if (typeof v === "boolean") return v ? "Yes" : "No"
  if (typeof v === "number") return v >= 100000 && Number.isInteger(v) ? inr(v) : String(v)
  return String(v)
}

// Source group -> {label, section keys it owns, raw signal keys that feed it}. Drives the
// per-source section cards: each section family carries its matching signals in a drawer.
const GROUPS: { key: string; label: string; sections: string[]; signals: string[] }[] = [
  { key: "identity_kyc",        label: "Identity & KYC",        sections: ["identity_checks", "consistency_check"], signals: ["pan_verify", "aadhaar_ekyc", "ckyc", "liveness_facematch", "mobile_to_pan", "device_fingerprint"] },
  { key: "contactability",      label: "Contactability",        sections: ["contactability"], signals: ["mobile_vintage", "mobile_fraud"] },
  { key: "occupation_employer", label: "Occupation & Employer", sections: ["occupation_self_employed", "occupation"], signals: ["epfo", "gst_itr", "mca_director_legal", "occupation_hazard"] },
  { key: "financial",           label: "Financial",             sections: ["financial_evaluation"], signals: ["account_aggregator"] },
  { key: "lifestyle",           label: "Lifestyle",             sections: ["lifestyle_analysis"], signals: [] },
  { key: "medical",             label: "Medical",               sections: ["medical_evaluation"], signals: ["abha_health_records", "rppg_scan"] },
  { key: "velocity_graph",      label: "Velocity & Graph",      sections: ["velocity_graph"], signals: ["velocity_graph"] },
  { key: "geography",           label: "Geography",             sections: ["geography"], signals: ["geography"] },
  { key: "fraud_check",         label: "Fraud",                 sections: ["fraud_check"], signals: [] },
  { key: "insurance_portfolio", label: "Insurance portfolio",   sections: ["insurance_portfolio_iib"], signals: [] },
  // No `sections` entry (name-based match, not scored into Safety Score — see IdentityCenter's
  // LitigationCard) — this card shows facts only, never a risk-level badge, via `signals` alone.
  { key: "litigation_fir",      label: "Litigation & FIR",      sections: [], signals: ["litigation_fir"] },
]
const SCORE_LABEL: Record<string, string> = Object.fromEntries(GROUPS.map((g) => [g.key, g.label]))

type Phase = "running" | "ready" | "error"

export function DecisionStep({ appId }: { appId: number | null }) {
  const [phase, setPhase] = useState<Phase>("running")
  const [res, setRes] = useState<DecisionResult | null>(null)
  const [err, setErr] = useState("")
  const [chainOpen, setChainOpen] = useState(false)

  const mock = typeof window !== "undefined" && new URLSearchParams(window.location.search).has("mock")

  const run = useCallback(async () => {
    setPhase("running"); setErr("")
    try {
      // No real app (seeded demo preview) OR ?mock → show the sample decision. Without this
      // a null appId returned early and the step hung on "Running…" forever.
      if (mock || appId == null) {
        const full = await fetch(`/api/journey/decision/0?mock=1`).then((r) => r.json())
        setRes(full); setPhase("ready"); return
      }
      const d = await fetch(`/api/journey/decide/${appId}`, { method: "POST" }).then((r) => r.json())
      if (d.success === false) { setErr(d.message || "Underwriting failed."); setPhase("error"); return }
      const full = await fetch(`/api/journey/decision/${appId}`).then((r) => r.json())
      if (!full.success) { setErr(full.message || "Could not load the report."); setPhase("error"); return }
      setRes(full); setPhase("ready")
    } catch { setErr("Could not reach the underwriting engine."); setPhase("error") }
  }, [appId, mock])

  useEffect(() => { run() }, [run])

  if (phase === "running") return <Running />
  if (phase === "error") return <Failed message={err} onRetry={run} />

  const report = res?.report || {}
  const verdict = (res?.verdict || report.decision?.verdict || "REFER") as string
  const pending = res?.status === "pending"
  const chain = report.cited_evidence_chain || []

  return (
    <div className="space-y-8">
      {/* 1 */}
      <AppSummary meta={report.report_meta} />

      <div id="decision-verdict" className="scroll-mt-24 space-y-8">
        {/* 2 */}
        <VerdictBanner verdict={verdict} report={report} />
        {pending && <StepUpNotice waitingOn={res?.waiting_on} onRedecide={run} />}
        {/* 3 */}
        <RiskOverview report={report} chain={chain} onChainClick={() => setChainOpen(true)} />
        {/* 4 */}
        <ScoringTable rows={report.scoring_breakdown} total={report.scoring_total} />
      </div>

      {/* 5 */}
      <div id="decision-report" className="scroll-mt-24">
        <PerSource report={report} />
      </div>

      {/* 6, 7, 8 */}
      <Flags soft={report.bre_result?.soft_flags} ambiguous={report.bre_result?.ambiguous_flags}
        outcome={report.bre_result?.outcome} hardGate={report.bre_result?.hard_gate} />
      <Narrative verdict={report.risk_and_fraud_verdict} />
      <AuditLog log={report.audit_log} />

      <Modal open={chainOpen} onClose={() => setChainOpen(false)} title="LLM cited-evidence chain">
        <EvidenceChain chain={chain} />
      </Modal>
    </div>
  )
}

// ---- states ----------------------------------------------------------------------------
function Running() {
  return (
    <div className="grid place-items-center py-24 text-center">
      <Spinner weight="bold" className="size-8 text-primary animate-spin" />
      <div className="mt-4 text-[15px] font-semibold">Running the underwriting agent…</div>
      <p className="mt-1 text-[13px] text-muted-foreground max-w-sm">
        Assembling every signal from Steps 1–4 into one decision. Grey-zone cases consult the LLM judge — a few seconds.
      </p>
    </div>
  )
}
function Failed({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="grid place-items-center py-20 text-center">
      <span className="grid place-items-center size-11 rounded-xl stat-bad border"><Warning weight="fill" className="size-5" /></span>
      <div className="mt-3 text-[15px] font-semibold">Underwriting couldn't complete</div>
      <p className="mt-1 text-[13px] text-muted-foreground max-w-sm">{message}</p>
      <button onClick={onRetry} className="mt-4 inline-flex items-center gap-2 rounded-md bg-primary text-primary-foreground text-[13px] font-medium px-4 h-9 hover:bg-primary/90 transition-colors">
        <ArrowClockwise weight="bold" className="size-4" /> Retry
      </button>
    </div>
  )
}

// ---- 1 · application summary (report_meta) ---------------------------------------------
function AppSummary({ meta }: { meta?: Record<string, any> }) {
  if (!meta) return null
  const p = meta.profile || {}
  const chips = [p.age && `${p.age}y`, p.gender, p.marital_status, p.location, p.occupation_type && title(p.occupation_type)]
    .filter(Boolean).map((c) => title(String(c)))
  return (
    <section className="rounded-xl border border-border bg-secondary/30 p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
        <div>
          <div className="text-[15px] font-bold tracking-tight">{meta.applicant_name || "Applicant"}</div>
          <div className="font-mono text-[11px] text-muted-foreground">{meta.application_no}{meta.report_date ? ` · ${meta.report_date}` : ""}</div>
        </div>
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-0.5 text-[12.5px]">
          {meta.product_name && <span className="font-semibold">{meta.product_name}</span>}
          {meta.sum_assured != null && <span><span className="text-muted-foreground">SI </span><span className="font-semibold tabular-nums">{amt(meta.sum_assured)}</span></span>}
          {meta.premium != null && <span><span className="text-muted-foreground">Premium </span><span className="font-semibold tabular-nums">{inr(meta.premium)}/yr</span></span>}
        </div>
      </div>
      {chips.length > 0 && (
        <div className="mt-2.5 flex flex-wrap gap-1.5">
          {chips.map((c, i) => <span key={i} className="rounded-md bg-white border px-2 py-0.5 text-[11.5px] text-muted-foreground">{c}</span>)}
        </div>
      )}
    </section>
  )
}

// ---- 2 · verdict banner ----------------------------------------------------------------
function VerdictBanner({ verdict, report }: { verdict: string; report: DecisionReport }) {
  const v = VERDICT[verdict] || VERDICT.REFER
  const Icon = v.icon
  const d = report.decision
  const reason = d?.reason_summary || d?.escalation_reason || ""
  const loading = d?.loading_band || (d?.loading_pct != null ? `${d.loading_pct}%` : d?.indicative_loading_if_cleared)
  return (
    <section className={`rounded-2xl border p-5 sm:p-6 animate-fade-up ${BANNER[v.tone]}`}>
      <div className="flex items-start gap-4">
        <span className="grid place-items-center size-11 rounded-xl bg-white/70 shrink-0"><Icon weight="fill" className="size-6" /></span>
        <div className="min-w-0">
          <div className="font-mono text-[11px] uppercase tracking-wider opacity-70">Core-6 · {verdict}</div>
          <h2 className="text-[22px] font-bold tracking-tight mt-0.5">{v.title}</h2>
          {reason && <p className="mt-1.5 text-[13.5px] leading-snug opacity-90 max-w-2xl">{reason}</p>}
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {loading && <span className="rounded-full bg-white/60 px-2.5 py-0.5 text-[12px] font-semibold">Loading {loading}</span>}
            {d?.secondary_flag && <span className="rounded-full bg-white/60 px-2.5 py-0.5 text-[11.5px] font-medium">{title(d.secondary_flag)}</span>}
            {(d?.reason_codes || []).map((c) => <span key={c} className="rounded-full bg-white/50 px-2 py-0.5 font-mono text-[10.5px]">{c}</span>)}
          </div>
        </div>
      </div>
    </section>
  )
}

function StepUpNotice({ waitingOn, onRedecide }: { waitingOn?: string | null; onRedecide: () => void }) {
  const [busy, setBusy] = useState(false)
  const go = async () => { setBusy(true); await onRedecide(); setBusy(false) }
  return (
    <section className="rounded-xl border border-amber-200 bg-amber-50/60 p-4">
      <div className="flex items-start gap-3">
        <Clock weight="fill" className="size-5 text-amber-600 shrink-0 mt-0.5" />
        <div className="min-w-0 flex-1">
          <div className="text-[13.5px] font-semibold text-amber-900">Pending — the agent needs corroborating evidence</div>
          {waitingOn && <p className="mt-0.5 text-[12px] text-amber-800 font-mono">{waitingOn}</p>}
          <p className="mt-1 text-[12px] text-amber-800/90">Evidence gathered in the journey is on the bundle. Re-run to let the agent re-judge and resolve.</p>
          <button onClick={go} disabled={busy} className="mt-2.5 inline-flex items-center gap-2 rounded-md bg-amber-600 text-white text-[13px] font-medium px-4 h-9 hover:bg-amber-700 transition-colors disabled:opacity-60">
            {busy ? <><Spinner weight="bold" className="size-4 animate-spin" /> Re-judging…</> : <><ArrowClockwise weight="bold" className="size-4" /> Gather evidence & re-decide</>}
          </button>
        </div>
      </div>
    </section>
  )
}

// ---- 3 · risk overview -----------------------------------------------------------------
function RiskOverview({ report, chain, onChainClick }: { report: DecisionReport; chain: CitedEvidence[]; onChainClick: () => void }) {
  const ss = report.safety_score
  const r = report.risk_scores
  const cycles = report.run_metadata?.judge_cycles || 0
  const bars: [string, number | undefined][] = [["Fraud", r?.fraud_score], ["Anomaly", r?.anomaly_score], ["Graph", r?.graph_score]]
  const shap = Object.entries(r?.shap || {}).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
  return (
    <section className="rounded-2xl border border-border bg-white p-5">
      <div className="grid grid-cols-1 lg:grid-cols-[auto_1px_1fr] gap-5 lg:gap-6">
        <div className="flex items-start gap-4">
          <Gauge value={ss?.value ?? null} tone={gaugeTone(ss?.band)} size={96} />
          <div>
            <div className="text-[11px] uppercase tracking-wider text-muted-foreground font-semibold">Safety Score</div>
            <div className="text-3xl font-bold tabular-nums leading-tight">{ss?.value != null ? Math.round(ss.value) : "—"}<span className="text-base text-muted-foreground font-normal"> / 100</span></div>
            <div className="text-[12px] font-semibold text-muted-foreground">{ss?.band || "—"}</div>
            {ss?._note && <p className="mt-1 text-[10.5px] text-muted-foreground italic max-w-[16rem] leading-snug">{ss._note}</p>}
            {chain.length > 0 && (
              <button onClick={onChainClick} className="mt-2 inline-flex items-center gap-1.5 rounded-lg border border-border bg-secondary/40 px-2.5 h-8 text-[12px] font-medium hover:border-muted-foreground/30 transition-colors">
                <ListChecks weight="bold" className="size-3.5 text-primary" />
                {chain.length} ruling{chain.length > 1 ? "s" : ""}{cycles >= 2 ? " · 2 cycles" : ""}
                <ArrowRight weight="bold" className="size-3 text-muted-foreground" />
              </button>
            )}
          </div>
        </div>
        <div className="hidden lg:block bg-border" />
        {r && (
          <div>
            <div className="text-[11px] uppercase tracking-wider text-muted-foreground font-semibold mb-2.5">Fraud · anomaly · graph<span className="font-normal normal-case tracking-normal"> — 0–1, higher = riskier{r.composite_band ? ` · ${r.composite_band} band` : ""}</span></div>
            <div className="grid grid-cols-3 gap-4">
              {bars.map(([label, val]) => (
                <div key={label}>
                  <div className="flex items-baseline justify-between"><span className="text-[12px] text-muted-foreground">{label}</span>
                    <span className="text-[13px] font-bold tabular-nums">{val != null ? val.toFixed(2) : "—"}</span></div>
                  <div className="mt-1 h-1.5 rounded-full bg-muted overflow-hidden">
                    <div className="h-full rounded-full bg-primary/70" style={{ width: `${Math.min(100, Math.max(0, (val ?? 0) * 100))}%` }} />
                  </div>
                </div>
              ))}
            </div>
            {shap.length > 0 && (
              <div className="mt-3 pt-3 border-t">
                <div className="text-[11px] uppercase tracking-wider text-muted-foreground font-semibold mb-1.5">Top contributors (SHAP)</div>
                <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1">
                  {shap.map(([feat, contrib]) => (
                    <div key={feat} className="flex items-baseline justify-between gap-3">
                      <dt className="text-[12px] text-muted-foreground truncate">{title(feat)}</dt>
                      <dd className="text-[12px] font-semibold tabular-nums shrink-0">{contrib > 0 ? "+" : ""}{contrib.toFixed(3)}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  )
}

// ---- 4 · safety-score breakdown TABLE (standalone) -------------------------------------
function ScoringTable({ rows, total }: { rows?: ScoreRowT[]; total?: Record<string, any> }) {
  const list = rows || []
  if (!list.length) return null
  return (
    <section>
      <RegionHead title="Safety-score breakdown" hint="Each source group's sub-score (0–100, higher = safer), its weight, weighted contribution, and reason." />
      <div className="rounded-xl border border-border bg-white overflow-x-auto">
        <table className="w-full text-[12.5px]">
          <thead className="text-muted-foreground text-left">
            <tr className="border-b">
              <th className="font-semibold px-4 py-2.5">Source</th>
              <th className="font-semibold px-3 py-2.5 text-right">Sub-score</th>
              <th className="font-semibold px-3 py-2.5 text-right">Weight</th>
              <th className="font-semibold px-3 py-2.5 text-right">Contribution</th>
              <th className="font-semibold px-4 py-2.5">Why</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {list.map((r) => (
              <tr key={r.source_group}>
                <td className="px-4 py-2.5 font-semibold whitespace-nowrap">{SCORE_LABEL[r.source_group] || title(r.source_group)}</td>
                <td className="px-3 py-2.5 text-right tabular-nums font-bold">{r.risk_sub_score != null ? Math.round(r.risk_sub_score) : "—"}</td>
                <td className="px-3 py-2.5 text-right tabular-nums text-muted-foreground">{r.weight != null ? r.weight.toFixed(2) : "—"}</td>
                <td className="px-3 py-2.5 text-right tabular-nums text-muted-foreground">{r.contribution != null ? r.contribution.toFixed(2) : "—"}</td>
                <td className="px-4 py-2.5 text-muted-foreground">{r.why || "—"}</td>
              </tr>
            ))}
          </tbody>
          {total && (
            <tfoot className="border-t-2 font-semibold bg-secondary/30">
              <tr>
                <td className="px-4 py-2.5">Total</td>
                <td className="px-3 py-2.5 text-right tabular-nums">{total.computed_safety_score != null ? Math.round(total.computed_safety_score) : "—"}</td>
                <td className="px-3 py-2.5 text-right tabular-nums">{total.sum_of_weights != null ? Number(total.sum_of_weights).toFixed(2) : "—"}</td>
                <td className="px-3 py-2.5" />
                <td className="px-4 py-2.5 text-muted-foreground font-normal text-[11.5px]">weighted composite = safety score</td>
              </tr>
            </tfoot>
          )}
        </table>
      </div>
    </section>
  )
}

// ---- 5 · per-source section cards (with nested signal drawers) -------------------------
function PerSource({ report }: { report: DecisionReport }) {
  const scoreByGroup = new Map((report.scoring_breakdown || []).map((r) => [r.source_group, r]))
  const sections = report.sections || {}
  const signals = report.signals || {}
  const claimedSec = new Set(GROUPS.flatMap((g) => g.sections))
  const claimedSig = new Set(GROUPS.flatMap((g) => g.signals))
  const extraSec = Object.keys(sections).filter((k) => !claimedSec.has(k))
  const extraSig = Object.keys(signals).filter((k) => !claimedSig.has(k))

  const cards = GROUPS.map((g) => ({
    gkey: g.key, label: g.label, score: scoreByGroup.get(g.key),
    secs: g.sections.filter((s) => sections[s]).map((s) => [s, sections[s]] as [string, Section]),
    sigs: g.signals.filter((s) => signals[s]).map((s) => [s, signals[s]] as [string, any]),
  })).filter((c) => c.score || c.secs.length || c.sigs.length)

  return (
    <section>
      <RegionHead title="Per-source assessment" hint="Each source group: the assessment detail, and the raw data sources it was built from (expand each)." />
      <div className="space-y-3">
        {cards.map(({ gkey, ...c }) => <SourceCard key={gkey} {...c} />)}
        {(extraSec.length > 0 || extraSig.length > 0) && (
          <SourceCard label="Other" score={undefined}
            secs={extraSec.map((s) => [s, sections[s]] as [string, Section])}
            sigs={extraSig.map((s) => [s, signals[s]] as [string, any])} />
        )}
      </div>
    </section>
  )
}

function SourceCard({ label, score, secs, sigs }: {
  label: string; score?: ScoreRowT; secs: [string, Section][]; sigs: [string, any][]
}) {
  const [sigOpen, setSigOpen] = useState(false)
  const level = secs.find(([, s]) => s.risk_level)?.[1]?.risk_level
  return (
    <div className="rounded-xl border border-border bg-white overflow-hidden">
      <div className="flex items-center gap-3 px-4 py-3 border-b bg-secondary/30">
        <div className="text-[13.5px] font-semibold flex-1 min-w-0 truncate">{label}</div>
        {score?.risk_sub_score != null && (
          <span className="text-[13px] font-bold tabular-nums" title="safety sub-score (higher = safer)">{Math.round(score.risk_sub_score)}<span className="text-[10px] text-muted-foreground font-normal">/100</span></span>
        )}
        {level && <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-semibold ${statFor(level)}`}>{level === "Not Assessed" ? "Not assessed" : level}</span>}
      </div>

      <div className="p-4 space-y-3">
        {secs.map(([name, s]) => {
          const detail = Object.entries(s).filter(([k]) => !SECTION_META_KEYS.has(k))
          return (
            <div key={name} className={secs.length > 1 ? "rounded-lg border border-border/70 p-3" : ""}>
              {secs.length > 1 && (
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-[11.5px] font-semibold">{title(name)}</span>
                  {s.risk_level && <span className={`inline-flex items-center rounded-full border px-1.5 py-0.5 text-[10px] font-semibold ${statFor(s.risk_level)}`}>{s.risk_level}</span>}
                </div>
              )}
              {s.findings && <p className="text-[12px] text-muted-foreground leading-snug mb-2">{s.findings}</p>}
              {detail.length > 0 && <div className="space-y-2.5">{detail.map(([k, v]) => <Field key={k} k={k} v={v} />)}</div>}
            </div>
          )
        })}

        {sigs.length > 0 && (
          <div className="rounded-lg border border-border/70">
            <button onClick={() => setSigOpen((o) => !o)} className="w-full flex items-center gap-2 px-3 py-2.5 text-[12px] font-semibold text-muted-foreground hover:text-foreground transition-colors">
              <Database weight="bold" className="size-4 text-primary/70" />
              {sigs.length} data source{sigs.length > 1 ? "s" : ""}
              <CaretDown weight="bold" className={`size-3.5 ml-auto transition-transform ${sigOpen ? "rotate-180" : ""}`} />
            </button>
            {sigOpen && (
              <div className="px-3 pb-3 grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                {sigs.map(([name, sig]) => <SignalBlock key={name} name={name} sig={sig} />)}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// One raw signal as a roomy labeled block: result pill + consent/consumed_by + data fields.
function SignalBlock({ name, sig }: { name: string; sig: any }) {
  if (!sig || typeof sig !== "object") return null
  const { result, consumed_by, consent, ...data } = sig
  const dataRows = Object.entries(data)
  const bad = typeof result === "string" && /flag|mismatch|weak|delay/i.test(result)
  return (
    <div className="rounded-lg border border-border bg-secondary/20 p-3">
      <div className="flex items-center gap-2 flex-wrap mb-2">
        <span className="text-[12px] font-semibold font-mono">{name}</span>
        {result && <span className={`inline-flex items-center rounded-full border px-1.5 py-0.5 text-[10px] font-semibold ${bad ? "stat-warn" : "stat-ok"}`}>{result}</span>}
      </div>
      {dataRows.length > 0 && (
        <dl className="space-y-1">
          {dataRows.map(([k, v]) => <Field key={k} k={k} v={v} dense />)}
        </dl>
      )}
      <div className="mt-2 pt-2 border-t border-border/60 flex items-center justify-between gap-2 text-[10px] text-muted-foreground">
        <span>{consent && consent !== "none" ? `consent · ${consent}` : "no consent"}</span>
        {consumed_by && <span>→ {Array.isArray(consumed_by) ? consumed_by.join(", ") : consumed_by}</span>}
      </div>
    </div>
  )
}

// ---- generic shape-tolerant field renderer (sections AND signals) ----------------------
const SECTION_META_KEYS = new Set(["risk_level", "sub_score", "weight", "findings", "assessed"])

function Field({ k, v, dense }: { k: string; v: any; dense?: boolean }) {
  if (v == null || v === "" || (Array.isArray(v) && v.length === 0)) return null
  if (Array.isArray(v)) {
    if (typeof v[0] === "object" && v[0] !== null) return <ObjTable label={k} rows={v} />
    return (
      <div>
        <FieldLabel>{k}</FieldLabel>
        <div className="flex flex-wrap gap-1.5">{v.map((x, i) => <span key={i} className="rounded-md bg-white border px-2 py-0.5 text-[11px]">{fmt(x)}</span>)}</div>
      </div>
    )
  }
  if (typeof v === "object") {
    const rows = Object.entries(v).filter(([, x]) => x != null && x !== "")
    if (!rows.length) return null
    return (
      <div>
        <FieldLabel>{k}</FieldLabel>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 rounded-lg bg-white border p-2.5">
          {rows.map(([kk, vv]) => (
            <div key={kk} className="flex items-baseline justify-between gap-2">
              <dt className="text-[11px] text-muted-foreground truncate">{title(kk)}</dt>
              <dd className={`text-[11.5px] font-medium tabular-nums text-right ${kk.includes("mismatch") && vv === true ? "text-red-600" : ""}`}>{fmt(vv)}</dd>
            </div>
          ))}
        </dl>
      </div>
    )
  }
  const danger = (k.includes("flag") || k.includes("mismatch") || k.includes("misrepresentation") || k.includes("default")) && v === true
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className={`${dense ? "text-[11px]" : "text-[11.5px]"} text-muted-foreground`}>{title(k)}</span>
      <span className={`text-[12px] font-semibold tabular-nums text-right ${danger ? "text-red-600" : ""}`}>{fmt(v)}</span>
    </div>
  )
}

function ObjTable({ label, rows }: { label: string; rows: any[] }) {
  const cols = Array.from(rows.reduce((set: Set<string>, r) => { Object.keys(r).forEach((k) => set.add(k)); return set }, new Set<string>()))
  return (
    <div>
      <FieldLabel>{label}</FieldLabel>
      <div className="rounded-lg border border-border overflow-x-auto">
        <table className="w-full text-[11.5px]">
          <thead className="text-muted-foreground text-left bg-secondary/40">
            <tr>{cols.map((c) => <th key={c} className="font-semibold px-2.5 py-1.5 whitespace-nowrap">{title(c)}</th>)}</tr>
          </thead>
          <tbody className="divide-y">
            {rows.map((r, i) => (
              <tr key={i}>
                {cols.map((c) => {
                  const cell = r[c]
                  const sevCol = ["severity", "risk"].includes(c) && typeof cell === "string"
                  return (
                    <td key={c} className="px-2.5 py-1.5 whitespace-nowrap">
                      {sevCol ? <span className={`inline-flex items-center rounded-full border px-1.5 py-0.5 text-[10px] font-semibold ${statFor(cell)}`}>{cell}</span> : fmt(cell)}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function FieldLabel({ children }: { children: React.ReactNode }) {
  return <div className="text-[10.5px] uppercase tracking-wider text-muted-foreground font-semibold mb-1">{title(String(children))}</div>
}

// ---- 6 · BRE flags ---------------------------------------------------------------------
function Flags({ soft, ambiguous, outcome, hardGate }: { soft?: SoftFlag[]; ambiguous?: AmbiguousFlag[]; outcome?: string; hardGate?: string | null }) {
  const softList = soft || []
  const ambList = ambiguous || []
  const none = !softList.length && !ambList.length
  return (
    <section>
      <RegionHead title="Rule-engine flags" hint={`BRE outcome: ${outcome || "—"}${hardGate ? ` · hard gate ${hardGate}` : ""}`} />
      {none ? (
        <div className="rounded-xl border border-border bg-white p-4 flex items-center gap-2.5 text-[13px] text-muted-foreground">
          <CheckCircle weight="fill" className="size-4 text-emerald-600" /> No flags raised.
        </div>
      ) : (
        <div className="space-y-2.5">
          {softList.map((f, i) => (
            <div key={`s${i}`} className="rounded-xl border border-border bg-white p-4">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[13px] font-semibold">{title(f.flag_type)}</span>
                <span className="font-mono text-[10.5px] text-muted-foreground">{f.related_rule}</span>
                {f.severity && <span className={`ml-auto inline-flex items-center rounded-full border px-2 py-0.5 text-[10.5px] font-semibold uppercase ${statFor(f.severity)}`}>{f.severity}</span>}
              </div>
              {f.reason && <p className="mt-1 text-[12.5px] text-muted-foreground leading-snug">{f.reason}</p>}
            </div>
          ))}
          {ambList.map((f, i) => (
            <div key={`a${i}`} className="rounded-xl border border-amber-200 bg-amber-50/50 p-4 flex items-center gap-2 flex-wrap">
              <span className="text-[13px] font-semibold text-amber-900">{title(f.flag_type)}</span>
              <span className="font-mono text-[10.5px] text-amber-700">{f.related_rule}</span>
              <span className="ml-auto inline-flex items-center rounded-full border border-amber-300 bg-amber-100 px-2 py-0.5 text-[10.5px] font-semibold uppercase text-amber-800">grey-zone → LLM</span>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}

// ---- 7 · narrative + 8 · audit ---------------------------------------------------------
function Narrative({ verdict }: { verdict?: Record<string, any> }) {
  if (!verdict) return null
  const rows: [string, any][] = [
    ["Clinical risk", verdict.risk_summary],
    ["Fraud", verdict.fraud_summary],
    ["Non-disclosure", typeof verdict.non_disclosure === "string" ? verdict.non_disclosure : verdict.non_disclosure ? "Present" : null],
  ]
  return (
    <section>
      <RegionHead title="Risk & fraud verdict" hint="The agent's narrative summary, built from its own flags and scores." />
      <div className="rounded-xl border border-border bg-white p-4 space-y-3">
        {rows.filter(([, v]) => v).map(([k, v]) => (
          <div key={k}>
            <div className="text-[11px] uppercase tracking-wider text-muted-foreground font-semibold">{k}</div>
            <p className="text-[13px] mt-0.5 leading-snug">{v}</p>
          </div>
        ))}
        {verdict.band_vs_decision_note && (
          <p className="text-[12px] text-amber-800 bg-amber-50 border border-amber-200 rounded-lg p-2.5 leading-snug">{verdict.band_vs_decision_note}</p>
        )}
      </div>
    </section>
  )
}

function AuditLog({ log }: { log?: AuditEntry[] }) {
  const list = log || []
  if (!list.length) return null
  return (
    <section>
      <RegionHead title="Audit trail" hint="Append-only record of every stage the engine ran, in order." />
      <ol className="rounded-xl border border-border bg-white divide-y">
        {list.map((e, i) => (
          <li key={i} className="px-4 py-2.5 flex items-baseline gap-3">
            <span className="font-mono text-[10.5px] text-muted-foreground w-28 shrink-0">{e.step}</span>
            <span className="text-[12.5px] flex-1">{e.detail}</span>
            <span className="text-[10.5px] text-muted-foreground shrink-0">{e.actor}</span>
          </li>
        ))}
      </ol>
    </section>
  )
}

const RULING_TONE: Record<string, string> = {
  benign_explained: "bg-emerald-500",
  needs_income_corroboration: "bg-amber-500",
  needs_medical_check: "bg-amber-500",
  needs_identity_reverification: "bg-amber-500",
  unresolvable_escalate: "bg-red-500",
}
function EvidenceChain({ chain }: { chain: CitedEvidence[] }) {
  if (!chain.length) return <p className="text-[13px] text-muted-foreground">No LLM rulings — decided deterministically (no grey-zone).</p>
  return (
    <ol className="relative space-y-5">
      {chain.map((c, i) => (
        <li key={i} className="relative pl-6">
          <span className={`absolute left-0 top-1.5 size-2.5 rounded-full ${RULING_TONE[c.ruling || ""] || "bg-muted-foreground"}`} />
          {i < chain.length - 1 && <span className="absolute left-[4.5px] top-4 bottom-[-1.25rem] w-px bg-border" />}
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[13px] font-semibold">{title(c.ruling || "ruling")}</span>
            {c.cycle != null && <span className="font-mono text-[10.5px] text-muted-foreground">cycle {c.cycle}</span>}
          </div>
          <div className="mt-0.5 text-[11.5px] text-muted-foreground">flag: {c.claim}</div>
          <code className="mt-1.5 inline-block rounded-md bg-primary/[0.07] text-primary px-2 py-1 text-[11.5px] font-mono break-all">{c.cited_source}</code>
        </li>
      ))}
    </ol>
  )
}

function RegionHead({ title: t, hint }: { title: string; hint: string }) {
  return (
    <div className="mb-3">
      <h2 className="text-[15px] font-bold tracking-tight">{t}</h2>
      <p className="text-[12px] text-muted-foreground mt-0.5">{hint}</p>
    </div>
  )
}
