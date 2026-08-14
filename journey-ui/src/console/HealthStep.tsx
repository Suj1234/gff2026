import { useRef, useState } from "react"
import type { AppSnapshot } from "./useJourney"
import {
  Check, Spinner, SealCheck, Warning, QrCode, PaperPlaneTilt,
  Pulse, Heartbeat, Wind, Drop, FirstAid, X, Plus,
} from "@phosphor-icons/react"
import { Modal } from "./Modal"

// STEP 4 — Health Declaration (TERM LIFE, single life). Modelled on the REAL Indian
// Declaration of Good Health (LIC Form 300 Rev-2024 Section III; ICICI Pru / HDFC term
// mirror it). Real forms use the SCREENER QUESTIONS as the disclosure — you answer Yes/No,
// and only a Yes reveals its own specifics inline. There is NO scary body-system grid up
// front. So: six Yes/No screeners (all default No); the medical-conditions screener reveals
// a condition picker inline on Yes; the others reveal a short detail line. Then always:
// height/weight (->BMI), tobacco/alcohol/drugs (LIC Section II), meds + family history.
// Then a live NuralX face scan and an ABHA consent+fetch.
//
// Persists via /api/journey/health on Continue (Console.saveStep). Face scan + ABHA run
// on their own buttons (they mutate the bundle server-side and light the rail immediately).

// The medical-conditions screener (LIC 300 §e) is the one that reveals a condition picker.
// The named conditions ARE the ones LIC 300 §e enumerates, flattened to a plain list (no
// body-system grouping — that grouping is what read as confusing). Order = most common first.
const MEDICAL_CONDITIONS = [
  "Diabetes", "High blood pressure", "Heart disease", "High cholesterol",
  "Asthma / respiratory", "Thyroid disorder", "Cancer or tumour", "Stroke / TIA",
  "Kidney disease", "Liver disease / hepatitis", "Tuberculosis",
  "Epilepsy / neurological", "Depression / anxiety", "Other",
]

// The six screeners. `reveal` decides what a Yes opens: "conditions" = the picker above;
// "detail" = a single detail line. Wording matches the real form's questions.
const SCREENERS: { id: string; q: string; reveal: "conditions" | "detail"; detailLabel?: string }[] = [
  { id: "major_illness", reveal: "conditions",
    q: "Have you ever suffered from, been diagnosed with, or been treated for any medical condition?" },
  { id: "practitioner", reveal: "detail", detailLabel: "What for, and when?",
    q: "In the last 5 years, have you consulted a doctor for any ailment needing treatment for more than a week?" },
  { id: "hospitalised", reveal: "detail", detailLabel: "What for, and when?",
    q: "Have you ever been admitted to a hospital or nursing home for treatment, an accident, an injury, or an operation?" },
  { id: "medication", reveal: "detail", detailLabel: "Which medication, and for what?",
    q: "Are you currently taking any prescribed medication regularly?" },
  { id: "surgery", reveal: "detail", detailLabel: "What surgery, and when?",
    q: "Are you currently advised or planning to undergo any surgery, or awaiting any test results?" },
  { id: "prior_decline", reveal: "detail", detailLabel: "Which insurer, and the outcome?",
    q: "Has any life or health insurance proposal on you ever been declined, deferred, postponed, or accepted at a higher premium?" },
]

// first-degree relatives + the hereditary conditions the form calls out (LIC 300 §III)
const FAMILY_MEMBERS = ["Father", "Mother", "Brother", "Sister"]
const FAMILY_CONDITIONS = ["Heart disease", "Stroke", "High blood pressure", "Diabetes", "Cancer", "Kidney disease"]

// per-condition light deep-dive. Backend stores conditions[] as strings, so each ticked
// condition round-trips as a single labelled string ("Diabetes — since 2019, controlled").
type ConditionDetail = { year: string; controlled: "" | "yes" | "no"; note: string }

export type HealthState = {
  screeners: Record<string, boolean>            // all default false (No)
  screenerDetail: Record<string, string>        // free text for a "detail"-reveal screener
  conditions: Record<string, ConditionDetail>   // keyed by condition name, only ticked ones present
  height_cm: number | ""
  weight_kg: number | ""
  tobacco: boolean; tobacco_qty: string
  alcohol: boolean; alcohol_qty: string
  drugs: boolean; drugs_qty: string
  family_history: string[]                       // ["Father: Diabetes", ...]
}

export const emptyHealth: HealthState = {
  screeners: {}, screenerDetail: {}, conditions: {}, height_cm: "", weight_kg: "",
  tobacco: false, tobacco_qty: "", alcohol: false, alcohol_qty: "",
  drugs: false, drugs_qty: "", family_history: [],
}

// Flatten HealthState -> the /api/journey/health body shape. conditions + screener details
// + habit quantities all fold into the engine's string/list fields.
export function healthPayload(h: HealthState) {
  const conditions = Object.entries(h.conditions).map(([name, d]) => {
    const bits = [d.year && `since ${d.year}`, d.controlled && (d.controlled === "yes" ? "controlled" : "not controlled"), d.note].filter(Boolean)
    return bits.length ? `${name} — ${bits.join(", ")}` : name
  })
  // detail-reveal screeners that are Yes with text become past-history notes
  const notes = SCREENERS.filter((s) => s.reveal === "detail" && h.screeners[s.id] && h.screenerDetail[s.id])
    .map((s) => `${s.detailLabel?.replace(/\?$/, "") ?? s.id}: ${h.screenerDetail[s.id]}`)
  const qty = (on: boolean, q: string, label: string) => (on && q ? `${label}: ${q}` : "")
  const habits = [qty(h.tobacco, h.tobacco_qty, "Tobacco"), qty(h.alcohol, h.alcohol_qty, "Alcohol"), qty(h.drugs, h.drugs_qty, "Drugs")].filter(Boolean).join("; ")
  const past = [...notes, habits].filter(Boolean).join(" · ")
  const ongoing = h.screeners["medication"] ? (h.screenerDetail["medication"] || "yes, details on file") : ""
  return {
    conditions,
    height_cm: h.height_cm || null,
    weight_kg: h.weight_kg || null,
    tobacco: h.tobacco, alcohol: h.alcohol, drugs: h.drugs,
    ongoing_medication: ongoing || null,
    past_medical_history: past || null,
    family_history: h.family_history,
  }
}

const bmiOf = (h: number | "", w: number | ""): number | null => {
  if (!h || !w) return null
  const m = Number(h) / 100
  return m > 0 ? Math.round((Number(w) / (m * m)) * 10) / 10 : null
}
const bmiBand = (b: number): { label: string; tone: "ok" | "warn" | "bad" } =>
  b < 18.5 ? { label: "Underweight", tone: "warn" }
  : b < 25 ? { label: "Normal", tone: "ok" }
  : b < 30 ? { label: "Overweight", tone: "warn" }
  : { label: "Obese", tone: "bad" }

// Three sub-steps. Screeners + conditions are ONE flow now ("Health") — a Yes reveals its
// specifics inline, so there is no separate conditions page (your feedback: they're linked).
export const HEALTH_SUBSTEPS = [
  { key: "health", label: "Health" },
  { key: "vitals", label: "Vitals & lifestyle" },
  { key: "facescan", label: "Face scan & ABHA" },
] as const

// The sub-steps Console paginates over. Fixed three — nothing auto-skips anymore.
export function visibleHealthSubSteps(_h: HealthState): { key: string; label: string }[] {
  return HEALTH_SUBSTEPS.map(({ key, label }) => ({ key, label }))
}

export function HealthStep({
  appId, snap, value, onChange, subStep = 0,
}: {
  appId: number | null; snap: AppSnapshot
  value: HealthState; onChange: (s: HealthState) => void
  subStep?: number   // which visible sub-step to render (Console drives it via the footer)
}) {
  const set = (patch: Partial<HealthState>) => onChange({ ...value, ...patch })
  const anyYes = SCREENERS.some((s) => value.screeners[s.id])
  const bmi = bmiOf(value.height_cm, value.weight_kg)

  const visible = visibleHealthSubSteps(value)
  const active = visible[Math.min(subStep, visible.length - 1)]?.key ?? "health"

  const setScreener = (id: string, v: boolean) =>
    set({ screeners: { ...value.screeners, [id]: v } })
  const setDetail = (id: string, text: string) =>
    set({ screenerDetail: { ...value.screenerDetail, [id]: text } })
  const toggleCondition = (name: string) => {
    const next = { ...value.conditions }
    if (next[name]) delete next[name]
    else next[name] = { year: "", controlled: "", note: "" }
    set({ conditions: next })
  }

  return (
    <div className="space-y-8">
      {/* ── HEALTH ── the six screeners; a Yes reveals its own specifics inline ── */}
      {active === "health" && (
        <section className="animate-[fade-up_.2s_ease]">
          <RegionHead title="Health declaration" hint="Answer for the person being insured. Everything defaults to No — a Yes asks only for the details that matter." />
          <div className="space-y-2.5">
            {SCREENERS.map((s) => (
              <ScreenerCard key={s.id} screener={s} yes={!!value.screeners[s.id]}
                onYesNo={(v) => setScreener(s.id, v)}
                detail={value.screenerDetail[s.id] || ""} onDetail={(t) => setDetail(s.id, t)}
                conditions={value.conditions} onToggleCondition={toggleCondition}
                onConditionDetail={(name, d) => set({ conditions: { ...value.conditions, [name]: d } })} />
            ))}
          </div>
          {!anyYes && (
            <p className="mt-3 text-[12px] text-muted-foreground">All clear so far. Continue to vitals and lifestyle.</p>
          )}
        </section>
      )}

      {/* ── VITALS & LIFESTYLE ── height/weight -> BMI, habits, family history ── */}
      {active === "vitals" && (
        <div className="space-y-8 animate-[fade-up_.2s_ease]">
          <section>
            <RegionHead title="Vitals & lifestyle" hint="Height and weight give BMI. Declare tobacco, alcohol, and any other substances with quantities (LIC-standard)." />
            <fieldset className="rounded-xl border border-border bg-secondary/40 p-4 space-y-4">
              <legend className="sr-only">Vitals and lifestyle</legend>
              {/* height + weight -> derived BMI pill */}
              <div className="flex flex-wrap items-end gap-3">
                <NumField label="Height" unit="cm" value={value.height_cm} max={250}
                  onChange={(n) => set({ height_cm: n })} />
                <NumField label="Weight" unit="kg" value={value.weight_kg} max={400}
                  onChange={(n) => set({ weight_kg: n })} />
                {bmi != null && (
                  <div className="flex items-center gap-2 h-11 px-3 rounded-lg border border-border bg-white">
                    <span className="text-[11px] uppercase tracking-[0.06em] text-muted-foreground">BMI</span>
                    <span className="text-[15px] font-bold tabular-nums">{bmi}</span>
                    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold ${
                      bmiBand(bmi).tone === "ok" ? "stat-ok" : bmiBand(bmi).tone === "warn" ? "stat-warn" : "stat-bad"}`}>
                      {bmiBand(bmi).label}
                    </span>
                  </div>
                )}
              </div>
              {/* tobacco / alcohol / drugs — toggle reveals a quantity input */}
              <div className="space-y-2.5 pt-1">
                <HabitRow label="Tobacco" hint="cigarettes, beedis, gutkha, etc." qtyPlaceholder="e.g. 10 sticks/day"
                  on={value.tobacco} qty={value.tobacco_qty}
                  onToggle={(v) => set({ tobacco: v })} onQty={(q) => set({ tobacco_qty: q })} />
                <HabitRow label="Alcohol" hint="beer, wine, or spirits" qtyPlaceholder="e.g. 2 pegs/week"
                  on={value.alcohol} qty={value.alcohol_qty}
                  onToggle={(v) => set({ alcohol: v })} onQty={(q) => set({ alcohol_qty: q })} />
                <HabitRow label="Other substances" hint="narcotics or other substances" qtyPlaceholder="which, and how often"
                  on={value.drugs} qty={value.drugs_qty}
                  onToggle={(v) => set({ drugs: v })} onQty={(q) => set({ drugs_qty: q })} />
              </div>
            </fieldset>
          </section>

          <section>
            <RegionHead title="Family history" hint="First-degree relatives only. Tick a condition against the relative it affects." />
            <FamilyGrid value={value.family_history} onChange={(fh) => set({ family_history: fh })} />
          </section>
        </div>
      )}

      {/* ── FACE SCAN + ABHA ── the external scans, one page ── */}
      {active === "facescan" && (
        <div className="space-y-8 animate-[fade-up_.2s_ease]">
          <FaceScan appId={appId} snap={snap} />
          <AbhaFetch appId={appId} snap={snap} declaredClean={!anyYes} />
        </div>
      )}
    </div>
  )
}

// ─────────────────────────── screener card (inline reveal) ───────────────────────────

// One screener: the Yes/No question, and — on Yes — its own specifics inline. The
// medical-conditions screener reveals the condition picker; the rest reveal a detail line.
function ScreenerCard({
  screener, yes, onYesNo, detail, onDetail, conditions, onToggleCondition, onConditionDetail,
}: {
  screener: { id: string; q: string; reveal: "conditions" | "detail"; detailLabel?: string }
  yes: boolean; onYesNo: (v: boolean) => void
  detail: string; onDetail: (t: string) => void
  conditions: Record<string, ConditionDetail>
  onToggleCondition: (name: string) => void
  onConditionDetail: (name: string, d: ConditionDetail) => void
}) {
  return (
    <div className={`rounded-xl border transition-colors ${yes ? "border-primary bg-primary/[0.03]" : "border-border bg-white"}`}>
      <div className="flex items-start gap-4 p-4">
        <p className="text-[13.5px] leading-snug flex-1 min-w-0">{screener.q}</p>
        <YesNoToggle value={yes} onChange={onYesNo} />
      </div>

      {yes && screener.reveal === "detail" && (
        <div className="px-4 pb-4 animate-[fade-up_.15s_ease]">
          <input autoFocus placeholder={screener.detailLabel || "Please give details"}
            value={detail} onChange={(e) => onDetail(e.target.value)}
            className="w-full px-3 h-10 rounded-lg border border-input text-[13px] outline-none bg-white focus:border-ring focus:ring-[3px] focus:ring-ring/30" />
        </div>
      )}

      {yes && screener.reveal === "conditions" && (
        <div className="px-4 pb-4 space-y-3 animate-[fade-up_.15s_ease]">
          <div className="text-[12px] font-semibold text-muted-foreground">Which condition(s)? Tick each that applies.</div>
          <div className="flex flex-wrap gap-2">
            {MEDICAL_CONDITIONS.map((name) => (
              <button key={name} type="button" onClick={() => onToggleCondition(name)}
                className={`inline-flex items-center gap-1.5 h-9 px-3 rounded-lg border text-[13px] font-semibold transition-colors ${
                  conditions[name] ? "border-primary bg-primary/[0.08] text-primary" : "border-border bg-white text-foreground hover:border-muted-foreground/30"}`}>
                {conditions[name] && <Check weight="bold" className="size-3" />}{name}
              </button>
            ))}
          </div>
          {/* per-ticked-condition detail rows */}
          {Object.keys(conditions).length > 0 && (
            <div className="space-y-2 pt-1">
              {MEDICAL_CONDITIONS.filter((n) => conditions[n]).map((name) => (
                <ConditionDetailRow key={name} name={name} detail={conditions[name]}
                  onDetail={(d) => onConditionDetail(name, d)} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function ConditionDetailRow({
  name, detail, onDetail,
}: { name: string; detail: ConditionDetail; onDetail: (d: ConditionDetail) => void }) {
  return (
    <div className="rounded-lg border border-primary/30 bg-white p-2.5 flex flex-wrap items-center gap-2">
      <span className="text-[12.5px] font-semibold min-w-[92px]">{name}</span>
      <div className="flex items-stretch rounded-lg border border-input overflow-hidden bg-white focus-within:border-ring focus-within:ring-[3px] focus-within:ring-ring/30">
        <span className="grid place-items-center px-2 text-[11px] text-muted-foreground bg-muted border-r">Since</span>
        <input inputMode="numeric" maxLength={4} placeholder="Year" value={detail.year}
          onChange={(e) => onDetail({ ...detail, year: e.target.value.replace(/[^\d]/g, "").slice(0, 4) })}
          className="w-16 px-2 h-8 text-[13px] outline-none bg-white tabular-nums" />
      </div>
      <div className="flex items-center gap-1">
        {(["yes", "no"] as const).map((c) => (
          <button key={c} type="button"
            onClick={() => onDetail({ ...detail, controlled: detail.controlled === c ? "" : c })}
            className={`h-8 px-2.5 rounded-lg border text-[12px] font-semibold transition-colors ${
              detail.controlled === c ? "border-primary bg-primary/[0.08] text-primary" : "border-border bg-white hover:border-muted-foreground/30"}`}>
            {c === "yes" ? "Controlled" : "Not controlled"}
          </button>
        ))}
      </div>
      <input placeholder="Notes (optional)" value={detail.note}
        onChange={(e) => onDetail({ ...detail, note: e.target.value })}
        className="flex-1 min-w-[120px] px-3 h-8 rounded-lg border border-input text-[13px] outline-none bg-white focus:border-ring focus:ring-[3px] focus:ring-ring/30" />
    </div>
  )
}

// Compact Yes/No toggle (No default; Yes reads as the flagged/attention state).
function YesNoToggle({ value, onChange }: { value: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className="flex rounded-lg border border-input overflow-hidden shrink-0 bg-white">
      {(["No", "Yes"] as const).map((opt) => {
        const on = value === (opt === "Yes")
        const danger = opt === "Yes"
        return (
          <button key={opt} type="button" onClick={() => onChange(opt === "Yes")}
            className={`w-14 h-9 text-[13px] font-semibold transition-colors ${
              on ? (danger ? "bg-[oklch(0.52_0.13_25)] text-white" : "bg-primary text-primary-foreground")
                 : "text-muted-foreground hover:bg-muted"} ${opt === "Yes" ? "border-l border-input" : ""}`}>
            {opt}
          </button>
        )
      })}
    </div>
  )
}

// ─────────────────────────── vitals + lifestyle inputs ───────────────────────────

function NumField({ label, unit, value, max, onChange }: {
  label: string; unit: string; value: number | ""; max: number; onChange: (n: number | "") => void
}) {
  return (
    <div>
      <div className="text-[12px] font-semibold mb-1.5">{label}</div>
      <div className="flex items-stretch rounded-lg border border-input overflow-hidden bg-white focus-within:border-ring focus-within:ring-[3px] focus-within:ring-ring/30">
        <input inputMode="numeric" placeholder="0" value={value === "" ? "" : String(value)}
          onChange={(e) => {
            const raw = e.target.value.replace(/[^\d.]/g, "")
            if (raw === "") return onChange("")
            onChange(Math.min(Number(raw), max))
          }}
          className="w-20 px-3 h-11 text-[14px] font-semibold outline-none bg-white tabular-nums" />
        <span className="grid place-items-center px-2.5 text-[12px] text-muted-foreground bg-muted border-l">{unit}</span>
      </div>
    </div>
  )
}

function HabitRow({ label, hint, qtyPlaceholder, on, qty, onToggle, onQty }: {
  label: string; hint: string; qtyPlaceholder: string
  on: boolean; qty: string; onToggle: (v: boolean) => void; onQty: (q: string) => void
}) {
  return (
    <div className={`rounded-xl border transition-colors ${on ? "border-primary bg-primary/[0.04]" : "border-border bg-white"}`}>
      <div className="p-3 flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <div className="text-[13.5px] font-semibold">{label}</div>
          <div className="text-[12px] text-muted-foreground">{hint}</div>
        </div>
        <div className="flex rounded-lg border border-input overflow-hidden shrink-0 bg-white">
          {(["No", "Yes"] as const).map((opt) => {
            const active = on === (opt === "Yes")
            return (
              <button key={opt} type="button" onClick={() => onToggle(opt === "Yes")}
                className={`w-12 h-9 text-[13px] font-semibold transition-colors ${
                  active ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted"} ${opt === "Yes" ? "border-l border-input" : ""}`}>
                {opt}
              </button>
            )
          })}
        </div>
      </div>
      {on && (
        <div className="px-3 pb-3">
          <input placeholder={qtyPlaceholder} value={qty} onChange={(e) => onQty(e.target.value)}
            className="w-full px-3 h-9 rounded-lg border border-input text-[13px] outline-none bg-white focus:border-ring focus:ring-[3px] focus:ring-ring/30" />
        </div>
      )}
    </div>
  )
}

// first-degree relatives × hereditary conditions. LIC 300 §III names 6 conditions but is
// open-ended ("...or any hereditary disorders / insanity"), so an "Other" column captures
// named extras. The real UNDERWRITING signal is AGE OF ONSET per condition (premature <55
// is the risk marker); each ticked condition carries its OWN age of onset + note. Encodes
// to family_history: string[], one entry per ticked condition:
//   "Father: Diabetes"                        ticked, no detail
//   "Father: Diabetes @ 52"                   + age of onset
//   "Father: Diabetes @ 52 — on metformin"    + age + note
//   "Father: Diabetes — diet-controlled"      + note only
//   "Mother: Other — Alzheimer's @ 60 — maternal"   a named other condition
// Suffix parse order: split note on " — " FIRST (note may contain '@'), then age from " @ NN".
function parseSuffix(body: string): { head: string; age: string; note: string } {
  let head = body, note = ""
  const dash = body.indexOf(" — ")
  if (dash >= 0) { head = body.slice(0, dash); note = body.slice(dash + 3) }
  const am = head.match(/^(.*?)\s*@\s*(\d+)\s*$/)
  return am ? { head: am[1], age: am[2], note } : { head: head.trim(), age: "", note }
}
const buildSuffix = (age: string, note: string) => `${age ? ` @ ${age}` : ""}${note.trim() ? ` — ${note.trim()}` : ""}`

function FamilyGrid({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  // does an entry exist for relative m + condition c (ignoring any @age/—note suffix)?
  const entryFor = (m: string, c: string) => value.find((x) => x.startsWith(`${m}: ${c}`) && parseSuffix(x.slice(`${m}: `.length)).head === c)
  const has = (m: string, c: string) => !!entryFor(m, c)
  const detailOf = (m: string, c: string) => {
    const e = entryFor(m, c); if (!e) return { age: "", note: "" }
    const { age, note } = parseSuffix(e.slice(`${m}: `.length)); return { age, note }
  }
  const setDetail = (m: string, c: string, age: string, note: string) => {
    const cleaned = value.filter((x) => x !== entryFor(m, c))
    onChange([...cleaned, `${m}: ${c}${buildSuffix(age, note)}`])
  }
  const toggle = (m: string, c: string) => {
    const e = entryFor(m, c)
    onChange(e ? value.filter((x) => x !== e) : [...value, `${m}: ${c}`])
  }

  // "Other" named conditions: "M: Other — name" with its own @age + note
  const othersFor = (m: string) => value
    .filter((x) => x.startsWith(`${m}: Other — `))
    .map((x) => { const { head, age, note } = parseSuffix(x.slice(`${m}: Other — `.length)); return { name: head, age, note } })
  const otherOn = (m: string) => value.includes(`${m}: Other`) || othersFor(m).length > 0
  const setOthers = (m: string, list: { name: string; age: string; note: string }[]) => {
    const cleaned = value.filter((x) => !x.startsWith(`${m}: Other — `) && x !== `${m}: Other`)
    const add = list.filter((o) => o.name.trim()).map((o) => `${m}: Other — ${o.name.trim()}${buildSuffix(o.age, o.note)}`)
    onChange(add.length ? [...cleaned, ...add] : [...cleaned, `${m}: Other`])
  }
  const clearOther = (m: string) =>
    onChange(value.filter((x) => !x.startsWith(`${m}: Other`) && x !== `${m}: Other`))

  const rowSelected = (m: string) => FAMILY_CONDITIONS.some((c) => has(m, c)) || otherOn(m)

  return (
    <div className="space-y-3">
      <div className="rounded-xl border border-border bg-white overflow-x-auto">
        <table className="w-full border-collapse text-[12px]">
          <thead>
            <tr className="border-b border-border">
              <th className="text-left font-semibold text-muted-foreground px-3 py-2.5 sticky left-0 bg-white">Relative</th>
              {FAMILY_CONDITIONS.map((c) => (
                <th key={c} className="font-semibold text-muted-foreground px-2 py-2.5 whitespace-nowrap">{c}</th>
              ))}
              <th className="font-semibold text-muted-foreground px-2 py-2.5">Other</th>
            </tr>
          </thead>
          <tbody>
            {FAMILY_MEMBERS.map((m) => (
              <tr key={m} className="border-b border-border last:border-0">
                <td className="font-semibold px-3 py-2 sticky left-0 bg-white">{m}</td>
                {FAMILY_CONDITIONS.map((c) => (
                  <td key={c} className="text-center px-2 py-2">
                    <button type="button" aria-label={`${m}: ${c}`} onClick={() => toggle(m, c)}
                      className={`size-5 rounded border grid place-items-center transition-colors mx-auto ${
                        has(m, c) ? "bg-primary border-primary text-primary-foreground" : "border-muted-foreground/30 hover:border-primary/50"}`}>
                      {has(m, c) && <Check weight="bold" className="size-3" />}
                    </button>
                  </td>
                ))}
                <td className="text-center px-2 py-2">
                  <button type="button" aria-label={`${m}: Other`}
                    onClick={() => (otherOn(m) ? clearOther(m) : setOthers(m, [{ name: "", age: "", note: "" }]))}
                    className={`size-5 rounded border grid place-items-center transition-colors mx-auto ${
                      otherOn(m) ? "bg-primary border-primary text-primary-foreground" : "border-muted-foreground/30 hover:border-primary/50"}`}>
                    {otherOn(m) && <Check weight="bold" className="size-3" />}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* detail rows — per relative once anything is ticked: age of onset + note per condition */}
      {FAMILY_MEMBERS.filter(rowSelected).map((m) => (
        <FamilyDetail key={m} relative={m}
          conditions={FAMILY_CONDITIONS.filter((c) => has(m, c)).map((c) => ({ name: c, ...detailOf(m, c) }))}
          onConditionDetail={(c, age, note) => setDetail(m, c, age, note)}
          showOther={otherOn(m)} others={othersFor(m)} onOthers={(l) => setOthers(m, l)} />
      ))}
    </div>
  )
}

// Per-relative detail: age of onset + a note for EACH ticked condition, and named "Other"
// conditions (each with its own age + note). No relative-level catch-all note — each
// condition carries its own, which is what underwriting reads.
function FamilyDetail({
  relative, conditions, onConditionDetail, showOther, others, onOthers,
}: {
  relative: string
  conditions: { name: string; age: string; note: string }[]
  onConditionDetail: (name: string, age: string, note: string) => void
  showOther: boolean; others: { name: string; age: string; note: string }[]
  onOthers: (list: { name: string; age: string; note: string }[]) => void
}) {
  const list = others.length ? others : (showOther ? [{ name: "", age: "", note: "" }] : [])
  const setAt = (i: number, patch: Partial<{ name: string; age: string; note: string }>) =>
    onOthers(list.map((x, j) => (j === i ? { ...x, ...patch } : x)))
  const age4 = (s: string) => s.replace(/[^\d]/g, "").slice(0, 3)
  const AgeField = ({ age, onAge }: { age: string; onAge: (v: string) => void }) => (
    <div className="flex items-stretch rounded-lg border border-input overflow-hidden bg-white focus-within:border-ring focus-within:ring-[3px] focus-within:ring-ring/30 shrink-0">
      <span className="grid place-items-center px-2 text-[11px] text-muted-foreground bg-muted border-r whitespace-nowrap">Age at onset</span>
      <input inputMode="numeric" placeholder="—" value={age} onChange={(e) => onAge(age4(e.target.value))}
        className="w-12 px-2 h-8 text-[13px] outline-none bg-white tabular-nums" />
    </div>
  )
  return (
    <div className="rounded-lg border border-primary/30 bg-white p-3 space-y-2.5">
      <div className="text-[12.5px] font-semibold">{relative}</div>

      {/* per ticked condition: age at onset + a note */}
      {conditions.map((c) => (
        <div key={c.name} className="flex flex-wrap items-center gap-2">
          <span className="text-[12.5px] font-medium min-w-[110px]">{c.name}</span>
          <AgeField age={c.age} onAge={(v) => onConditionDetail(c.name, v, c.note)} />
          <input placeholder="Notes (cause, controlled, etc.)" value={c.note}
            onChange={(e) => onConditionDetail(c.name, c.age, e.target.value)}
            className="flex-1 min-w-[160px] px-3 h-8 rounded-lg border border-input text-[13px] outline-none bg-white focus:border-ring focus:ring-[3px] focus:ring-ring/30" />
        </div>
      ))}

      {/* named Other conditions, each with its own age + note */}
      {showOther && (
        <div className="space-y-2 pt-0.5">
          <div className="text-[11px] font-medium text-muted-foreground">Other condition(s)</div>
          {list.map((o, i) => (
            <div key={i} className="flex flex-wrap items-center gap-2">
              <input autoFocus={i === list.length - 1 && !o.name} placeholder="Name the condition"
                value={o.name} onChange={(e) => setAt(i, { name: e.target.value })}
                className="w-[150px] px-3 h-8 rounded-lg border border-input text-[13px] outline-none bg-white focus:border-ring focus:ring-[3px] focus:ring-ring/30" />
              <AgeField age={o.age} onAge={(v) => setAt(i, { age: v })} />
              <input placeholder="Notes" value={o.note} onChange={(e) => setAt(i, { note: e.target.value })}
                className="flex-1 min-w-[120px] px-3 h-8 rounded-lg border border-input text-[13px] outline-none bg-white focus:border-ring focus:ring-[3px] focus:ring-ring/30" />
              {list.length > 1 && (
                <button type="button" onClick={() => onOthers(list.filter((_, j) => j !== i))}
                  className="grid place-items-center size-8 rounded-lg border border-border text-muted-foreground hover:text-foreground hover:border-muted-foreground/40 transition-colors shrink-0">
                  <X weight="bold" className="size-3.5" />
                </button>
              )}
            </div>
          ))}
          <button type="button" onClick={() => onOthers([...list, { name: "", age: "", note: "" }])}
            className="inline-flex items-center gap-1.5 text-[12px] font-medium text-primary hover:underline">
            <Plus weight="bold" className="size-3.5" /> Add another
          </button>
        </div>
      )}
    </div>
  )
}

// ─────────────────────────── face scan (NuralX) ───────────────────────────

type Rppg = AppSnapshot["signals"]["rppg_scan"]
type ScanState = "idle" | "starting" | "waiting" | "done" | "error"

const bpText = (bp: unknown): string => {
  if (bp && typeof bp === "object") {
    const o = bp as { systolic?: number; diastolic?: number }
    if (o.systolic != null && o.diastolic != null) return `${Math.round(o.systolic)}/${Math.round(o.diastolic)}`
  }
  return typeof bp === "string" ? bp : "—"
}
const n0 = (v?: number) => (v != null ? String(Math.round(v)) : "—")
const n1 = (v?: number) => (v != null ? (Math.round(v * 100) / 100).toString() : "—")

// vendor risk flags: 0 none · 1 low · 2 moderate · 3 high (display only, never a decision).
const RISK_BAND = ["No risk", "Low", "Moderate", "High"] as const
const riskTone = (v?: number): "ok" | "warn" | "bad" => (v == null || v <= 0 ? "ok" : v === 1 ? "ok" : v === 2 ? "warn" : "bad")
// NuralX categorical levels (1-based indices from the vendor)
const STRESS_LABEL = ["", "Low", "Normal", "Mild", "High", "Very high"]
const WELLNESS_LABEL = ["", "Poor", "Fair", "Good", "Very good", "Excellent"]

// Inline SVG sparkline for the RR-interval series (no chart lib — a demo tachogram).
function Sparkline({ series }: { series: number[] }) {
  const W = 320, H = 44, n = series.length
  const min = Math.min(...series), max = Math.max(...series)
  const span = max - min || 1
  const pts = series.map((v, i) => {
    const x = (i / (n - 1)) * W
    const y = H - 4 - ((v - min) / span) * (H - 8)
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(" ")
  return (
    <div className="rounded-lg border border-border bg-[#faf9f7] p-3 overflow-x-auto">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none" role="img" aria-label="RR-interval tachogram">
        <polyline points={pts} fill="none" stroke="var(--primary)" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
      </svg>
      <div className="mt-1 flex justify-between text-[10px] text-muted-foreground tabular-nums">
        <span>{Math.round(min)} ms</span><span>{n} beats</span><span>{Math.round(max)} ms</span>
      </div>
    </div>
  )
}

function FaceScan({ appId, snap }: { appId: number | null; snap: AppSnapshot }) {
  const already = snap.signals.rppg_scan?.status === "available"
  const [state, setState] = useState<ScanState>(already ? "done" : "idle")
  const [rppg, setRppg] = useState<Rppg | null>(already ? snap.signals.rppg_scan ?? null : null)
  const [liveness, setLiveness] = useState<AppSnapshot["signals"]["liveness_facematch"] | null>(
    already ? snap.signals.liveness_facematch ?? null : null)
  const [scanUrl, setScanUrl] = useState<string>("")
  const [msg, setMsg] = useState<string>("")
  const pollRef = useRef<number | null>(null)

  // Poll the app snapshot for the webhook to land vitals (real NuralX is async); the mock
  // path fills them synchronously so the first poll already sees them.
  async function pollVitals() {
    if (appId == null) return
    try {
      const r = await fetch(`/api/journey/app/${appId}`)
      const d = (await r.json()) as AppSnapshot
      if (d?.signals?.rppg_scan?.status === "available") {
        setRppg(d.signals.rppg_scan!)
        setLiveness(d.signals.liveness_facematch ?? null)
        setState("done")
        if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
      }
    } catch { /* transient — keep polling */ }
  }

  async function start() {
    if (appId == null) return
    setState("starting"); setMsg("")
    try {
      const r = await fetch(`/api/journey/face-scan/start/${appId}`, { method: "POST" })
      const d = await r.json()
      if (!d.success) { setState("error"); setMsg(d.message || "Face scan unavailable — you can proceed."); return }
      if (d.mode === "mock") { await pollVitals(); return }              // mock filled vitals already
      setScanUrl(d.scan_url || ""); setState("waiting")
      pollRef.current = window.setInterval(pollVitals, 3000)             // wait for the webhook
    } catch { setState("error"); setMsg("Could not start the scan — you can proceed; vitals are optional.") }
  }

  const v = rppg?.vitals ?? {}
  const x = rppg?.vitals_extra ?? {}
  const livenessFailed = liveness?.liveness_pass === false || liveness?.deepfake_flag === true

  return (
    <section>
      <RegionHead title="Face scan" hint="A 60-second phone scan reads liveness and clinical vitals. Wellness estimates support triage only — not stand-alone underwriting." />

      <div className="rounded-xl border border-border bg-white p-4">
        {state === "done" ? (
          <div className="space-y-4">
            {/* result header — liveness/deepfake is the identity gate (R-003) */}
            <div className="flex items-center gap-3">
              <span className={`grid place-items-center size-9 rounded-lg border shrink-0 ${livenessFailed ? "stat-bad" : "stat-ok"}`}>
                <SealCheck weight="fill" className="size-[18px]" />
              </span>
              <div className="min-w-0 flex-1">
                <div className="text-[13.5px] font-semibold">Scan complete</div>
                <div className="text-[12px] text-muted-foreground">
                  {livenessFailed ? "Liveness / deepfake check flagged — see the rail." : "Liveness passed · vitals captured."}
                </div>
              </div>
              <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-semibold shrink-0 ${livenessFailed ? "stat-bad" : "stat-ok"}`}>
                <Check weight="bold" className="size-3" /> {livenessFailed ? "Flagged" : "Done"}
              </span>
            </div>

            {/* CARDIOVASCULAR — the four R-017 reads + derived pressures */}
            <VitalGrid title="Cardiovascular">
              <VitalTile icon={Heartbeat} label="Heart rate" value={n0(v.heart_rate)} unit="bpm" />
              <VitalTile icon={Wind} label="Respiratory" value={n0(v.respiratory_rate)} unit="/min" />
              <VitalTile icon={Drop} label="SpO₂" value={n0(v.spo2)} unit="%" />
              <VitalTile icon={Pulse} label="Blood pressure" value={bpText(v.bp)} unit="mmHg" />
              {x.map != null && <VitalTile icon={Heartbeat} label="Mean art. pr." value={n0(x.map)} unit="mmHg" />}
              {x.pulse_pressure != null && <VitalTile icon={Heartbeat} label="Pulse pressure" value={n0(x.pulse_pressure)} unit="mmHg" />}
              {x.cardiac_workload != null && <VitalTile icon={Heartbeat} label="Cardiac load" value={n1(x.cardiac_workload)} unit="" />}
              {x.prq != null && <VitalTile icon={Pulse} label="PRQ" value={n1(x.prq)} unit="" />}
            </VitalGrid>

            {/* METABOLIC — HbA1c / hemoglobin (screening estimates) */}
            {(x.hba1c != null || x.hemoglobin != null) && (
              <VitalGrid title="Metabolic" sub="Screening estimates · not underwriting inputs">
                {x.hemoglobin != null && <VitalTile icon={Drop} label="Hemoglobin" value={n1(x.hemoglobin)} unit="g/dL" />}
                {x.hba1c != null && <VitalTile icon={Pulse} label="HbA1c" value={n1(x.hba1c)} unit="%" />}
              </VitalGrid>
            )}

            {/* HRV / AUTONOMIC — the full heart-rate-variability + stress/wellness set */}
            {(x.sdnn != null || x.stress_index != null || x.pns_index != null) && (
              <VitalGrid title="HRV & autonomic balance" sub="Wellness estimates only">
                {x.sdnn != null && <VitalTile icon={Pulse} label="SDNN" value={n0(x.sdnn)} unit="ms" />}
                {x.rmssd != null && <VitalTile icon={Pulse} label="RMSSD" value={n0(x.rmssd)} unit="ms" />}
                {x.mean_rri != null && <VitalTile icon={Pulse} label="Mean RRI" value={n0(x.mean_rri)} unit="ms" />}
                {x.sd1 != null && <VitalTile icon={Pulse} label="SD1" value={n0(x.sd1)} unit="ms" />}
                {x.sd2 != null && <VitalTile icon={Pulse} label="SD2" value={n0(x.sd2)} unit="ms" />}
                {x.lf_hf != null && <VitalTile icon={Pulse} label="LF/HF" value={n1(x.lf_hf)} unit="" />}
                {x.pns_index != null && <VitalTile icon={Heartbeat} label="PNS index" value={n1(x.pns_index)} unit={x.pns_zone != null ? `zone ${n0(x.pns_zone)}` : ""} />}
                {x.sns_index != null && <VitalTile icon={Heartbeat} label="SNS index" value={n1(x.sns_index)} unit={x.sns_zone != null ? `zone ${n0(x.sns_zone)}` : ""} />}
                {x.stress_index != null && <VitalTile icon={Pulse} label="Stress index" value={n0(x.stress_index)} unit={x.stress_index_norm != null ? `norm ${n0(x.stress_index_norm)}` : ""} />}
                {x.stress_level != null && <VitalTile icon={Pulse} label="Stress level" value={STRESS_LABEL[Math.round(x.stress_level)] ?? n0(x.stress_level)} unit="" />}
                {x.wellness_index != null && <VitalTile icon={Heartbeat} label="Wellness" value={n0(x.wellness_index)} unit="/10" />}
                {x.wellness_level != null && <VitalTile icon={Heartbeat} label="Wellness level" value={WELLNESS_LABEL[Math.round(x.wellness_level)] ?? n0(x.wellness_level)} unit="" />}
              </VitalGrid>
            )}

            {/* RRI tachogram — the raw beat-to-beat waveform, as a sparkline */}
            {Array.isArray(x.rri_series) && x.rri_series.length > 4 && (
              <div>
                <div className="text-[11px] uppercase tracking-[0.07em] font-semibold text-muted-foreground mb-2">RR-interval tachogram</div>
                <Sparkline series={x.rri_series as unknown as number[]} />
              </div>
            )}

            {/* vendor risk flags — screening estimates, shown with severity, never a decision */}
            <RiskFlags x={x} />
          </div>
        ) : (
          <div className="flex items-start gap-3">
            <span className="grid place-items-center size-9 rounded-lg bg-primary/10 text-primary shrink-0"><Pulse weight="regular" className="size-[18px]" /></span>
            <div className="min-w-0 flex-1">
              <p className="text-[13px] text-muted-foreground leading-snug">
                The applicant opens the scan link on their own phone and holds still for about a minute. Liveness and rPPG vitals return here automatically.
              </p>
              {state === "error" && (
                <p className="mt-1.5 flex items-center gap-1.5 text-[12px] text-amber-700"><Warning weight="fill" className="size-3.5 shrink-0" /> {msg}</p>
              )}
              <div className="mt-2.5 flex flex-wrap items-center gap-2">
                <button type="button" disabled={state === "starting" || state === "waiting"} onClick={start}
                  className="inline-flex items-center gap-2 rounded-md bg-primary text-primary-foreground text-[13px] font-medium px-4 h-9 hover:bg-primary/90 transition-colors disabled:opacity-60">
                  {state === "starting" ? (<><Spinner weight="bold" className="size-4 animate-spin" /> Starting…</>)
                    : state === "waiting" ? (<><Spinner weight="bold" className="size-4 animate-spin" /> Waiting for scan…</>)
                    : (<><QrCode weight="bold" className="size-4" /> Start face scan</>)}
                </button>
                {scanUrl && (
                  <a href={scanUrl} target="_blank" rel="noreferrer"
                    className="inline-flex items-center gap-2 rounded-md border border-border text-[13px] font-medium px-4 h-9 hover:border-primary transition-colors">
                    <PaperPlaneTilt weight="bold" className="size-4" /> Open scan link
                  </a>
                )}
              </div>
              {state === "waiting" && scanUrl && (
                <p className="mt-2 text-[11px] text-muted-foreground break-all">Or share this link with the applicant: {scanUrl}</p>
              )}
            </div>
          </div>
        )}
      </div>
    </section>
  )
}

function VitalGrid({ title, sub, children }: { title: string; sub?: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="flex items-baseline gap-2 mb-2">
        <span className="text-[11px] uppercase tracking-[0.07em] font-semibold text-muted-foreground">{title}</span>
        {sub && <span className="text-[11px] text-muted-foreground/70">{sub}</span>}
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">{children}</div>
    </div>
  )
}

function VitalTile({ icon: Icon, label, value, unit }: {
  icon: React.ElementType; label: string; value: string; unit: string
}) {
  return (
    <div className="rounded-lg border border-border bg-[#faf9f7] px-3 py-2.5">
      <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.06em] font-semibold text-muted-foreground">
        <Icon weight="fill" className="size-3 text-primary shrink-0" /> <span className="truncate">{label}</span>
      </div>
      <div className="mt-1 flex items-baseline gap-1">
        <span className="text-[18px] font-bold tabular-nums">{value}</span>
        {unit && <span className="text-[11px] text-muted-foreground">{unit}</span>}
      </div>
    </div>
  )
}

// Vendor screening-risk flags (0..3). Displayed as severity chips; the agent, not this
// panel, decides — these are facts we surface, never underwriting inputs (§1.8).
function RiskFlags({ x }: { x: Record<string, number> }) {
  const flags: { key: string; label: string }[] = [
    { key: "risk_high_bp", label: "Blood pressure" },
    { key: "risk_hba1c", label: "HbA1c" },
    { key: "risk_glucose", label: "Fasting glucose" },
    { key: "risk_cholesterol", label: "Cholesterol" },
    { key: "risk_low_hemoglobin", label: "Low hemoglobin" },
  ].filter((f) => x[f.key] != null)
  if (!flags.length) return null
  return (
    <div>
      <div className="flex items-baseline gap-2 mb-2">
        <span className="text-[11px] uppercase tracking-[0.07em] font-semibold text-muted-foreground">Screening risk flags</span>
        <span className="text-[11px] text-muted-foreground/70">Estimates · agent decides</span>
      </div>
      <div className="flex flex-wrap gap-2">
        {flags.map((f) => {
          const val = x[f.key]
          const tone = riskTone(val)
          const cls = tone === "ok" ? "stat-ok" : tone === "warn" ? "stat-warn" : "stat-bad"
          return (
            <span key={f.key} className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-[12px] font-semibold ${cls}`}>
              {f.label}<span className="opacity-70">·</span>{RISK_BAND[Math.max(0, Math.min(3, Math.round(val)))]}
            </span>
          )
        })}
      </div>
    </div>
  )
}

// ─────────────────────────── ABHA (real ABDM handshake, keyed-mock records) ──────────────
// The real flow: (1) applicant gives their ABHA number/address + picks an OTP auth method,
// (2) verifies the OTP, (3) approves a consent request (record types + date range + purpose),
// then records are pulled. OTP is a demo formality (mock); the RECORD is the keyed mock.

type AbhaStage = "id" | "otp" | "done"
const ABHA_HI_TYPES = ["Diagnoses", "Prescriptions", "Lab reports", "Discharge summaries"]
const ABHA_RANGES = ["Last 1 year", "Last 3 years", "Last 5 years", "All records"]

// Format a 14-digit ABHA number as ##-####-####-#### as the underwriter types.
const fmtAbha = (s: string) => {
  const d = s.replace(/[^\d]/g, "").slice(0, 14)
  if (!d) return ""
  return [d.slice(0, 2), d.slice(2, 6), d.slice(6, 10), d.slice(10, 14)].filter(Boolean).join("-")
}

function AbhaFetch({ appId, snap, declaredClean }: { appId: number | null; snap: AppSnapshot; declaredClean: boolean }) {
  const existing = snap.signals.abha_health_records
  const [done, setDone] = useState(existing?.status === "available")
  const [count, setCount] = useState<number>(existing?.diagnoses?.length ?? 0)
  const [open, setOpen] = useState(false)
  // modal-internal flow state
  const [stage, setStage] = useState<AbhaStage>("id")
  const [abhaId, setAbhaId] = useState("")
  const [useAddress, setUseAddress] = useState(false)
  const [authMethod, setAuthMethod] = useState<"mobile_otp" | "aadhaar_otp">("mobile_otp")
  const [otp, setOtp] = useState("")
  const [demoOtp, setDemoOtp] = useState("")
  const [hiTypes, setHiTypes] = useState<string[]>(["Diagnoses", "Prescriptions"])
  const [range, setRange] = useState("Last 3 years")
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState("")

  const openModal = () => { setStage("id"); setOtp(""); setDemoOtp(""); setMsg(""); setOpen(true) }

  async function sendOtp() {
    if (appId == null || !abhaId.trim()) return
    setBusy(true); setMsg("")
    try {
      const r = await fetch("/api/journey/abha/otp/send", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ app_id: appId, abha_id: abhaId.trim(), auth_method: authMethod }),
      })
      const d = await r.json()
      if (d.success) { setDemoOtp(d.debug_otp || ""); setStage("otp") }
      else setMsg(d.message || "Could not start ABHA verification.")
    } catch { setMsg("Could not reach ABDM — you can proceed; ABHA is optional.") }
    finally { setBusy(false) }
  }

  async function verifyAndFetch() {
    if (appId == null) return
    setBusy(true); setMsg("")
    try {
      const r = await fetch(`/api/journey/abha/fetch/${appId}?otp=${encodeURIComponent(otp.trim())}`, { method: "POST" })
      const d = await r.json()
      if (d.success) { setCount((d.diagnoses || []).length); setDone(true); setOpen(false) }
      else setMsg(d.message || "ABHA verification failed.")
    } catch { setMsg("Could not reach ABDM — you can proceed.") }
    finally { setBusy(false) }
  }

  const toggleHi = (t: string) =>
    setHiTypes((cur) => (cur.includes(t) ? cur.filter((x) => x !== t) : [...cur, t]))

  return (
    <section>
      <RegionHead title="ABHA health records" hint="With the applicant's consent, pull their linked records from ABDM (Ayushman Bharat) to corroborate the declaration." />
      <div className="rounded-xl border border-border bg-white p-4">
        {done ? (
          <div className="flex items-center gap-3">
            <span className="grid place-items-center size-9 rounded-lg stat-ok border shrink-0"><FirstAid weight="fill" className="size-[18px]" /></span>
            <div className="min-w-0 flex-1">
              <div className="text-[13.5px] font-semibold">ABHA records fetched</div>
              <div className="text-[12px] text-muted-foreground">
                {count > 0
                  ? `${count} record${count > 1 ? "s" : ""} returned. The agent cross-checks these against the declaration${declaredClean ? " (declared clean)" : ""}.`
                  : "No records on file for this ABHA."}
              </div>
            </div>
            <span className="inline-flex items-center gap-1 rounded-full stat-ok border px-2 py-0.5 text-[11px] font-semibold shrink-0"><Check weight="bold" className="size-3" /> Done</span>
          </div>
        ) : (
          <div className="flex items-start gap-3">
            <span className="grid place-items-center size-9 rounded-lg bg-primary/10 text-primary shrink-0"><FirstAid weight="regular" className="size-[18px]" /></span>
            <div className="min-w-0 flex-1">
              <p className="text-[13px] text-muted-foreground leading-snug">
                Verify the applicant's ABHA and approve a consent request to pull their linked diagnoses and prescriptions from ABDM.
              </p>
              <button type="button" onClick={openModal}
                className="mt-2.5 inline-flex items-center gap-2 rounded-md bg-primary text-primary-foreground text-[13px] font-medium px-4 h-9 hover:bg-primary/90 transition-colors">
                <FirstAid weight="bold" className="size-4" /> Link ABHA
              </button>
            </div>
          </div>
        )}
      </div>

      {/* ── ABDM handshake modal: ABHA id -> OTP -> consent -> fetch ── */}
      <Modal open={open} onClose={() => setOpen(false)} title="Verify ABHA">
        {stage === "id" ? (
          <div className="space-y-4">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[12px] font-semibold">{useAddress ? "ABHA address" : "ABHA number"}</span>
              <button type="button" onClick={() => { setUseAddress((v) => !v); setAbhaId("") }}
                className="text-[12px] font-medium text-primary hover:underline">
                Use {useAddress ? "ABHA number" : "ABHA address"} instead
              </button>
            </div>
            {useAddress ? (
              <input autoFocus placeholder="name@abdm" value={abhaId} onChange={(e) => setAbhaId(e.target.value)}
                className="w-full px-3 h-11 rounded-lg border border-input text-[14px] font-semibold outline-none bg-white focus:border-ring focus:ring-[3px] focus:ring-ring/30" />
            ) : (
              <input autoFocus inputMode="numeric" placeholder="14-1234-5678-9012" value={fmtAbha(abhaId)}
                onChange={(e) => setAbhaId(e.target.value)}
                className="w-full px-3 h-11 rounded-lg border border-input text-[14px] font-semibold tabular-nums outline-none bg-white focus:border-ring focus:ring-[3px] focus:ring-ring/30" />
            )}

            <div>
              <div className="text-[12px] font-semibold mb-1.5">Verify via</div>
              <div className="flex gap-2">
                {([["mobile_otp", "Mobile OTP"], ["aadhaar_otp", "Aadhaar OTP"]] as const).map(([m, label]) => (
                  <button key={m} type="button" onClick={() => setAuthMethod(m)}
                    className={`h-10 px-4 rounded-lg border text-[13px] font-semibold transition-colors ${
                      authMethod === m ? "border-primary bg-primary/[0.06] text-primary" : "border-border bg-white hover:border-muted-foreground/30"}`}>
                    {label}
                  </button>
                ))}
              </div>
              <p className="mt-1.5 text-[11px] text-muted-foreground">An OTP is sent to the applicant's {authMethod === "mobile_otp" ? "registered mobile" : "Aadhaar-linked mobile"}.</p>
            </div>

            {msg && <p className="flex items-center gap-1.5 text-[12px] text-amber-700"><Warning weight="fill" className="size-3.5 shrink-0" /> {msg}</p>}
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setOpen(false)}
                className="rounded-md border border-border text-[13px] font-medium px-4 h-9 hover:border-muted-foreground/40 transition-colors">Cancel</button>
              <button type="button" disabled={!abhaId.trim() || busy} onClick={sendOtp}
                className="inline-flex items-center gap-2 rounded-md bg-primary text-primary-foreground text-[13px] font-medium px-4 h-9 hover:bg-primary/90 transition-colors disabled:opacity-50">
                {busy ? (<><Spinner weight="bold" className="size-4 animate-spin" /> Sending OTP…</>) : "Send OTP"}
              </button>
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            <div>
              <div className="flex items-center gap-2 text-[12px] font-semibold mb-1.5">
                Enter OTP
                {demoOtp && <span className="rounded-full bg-amber-100 text-amber-700 px-2 py-0.5 text-[10px] font-bold">demo OTP {demoOtp}</span>}
              </div>
              <input autoFocus inputMode="numeric" maxLength={6} placeholder="6-digit OTP" value={otp}
                onChange={(e) => setOtp(e.target.value.replace(/[^\d]/g, "").slice(0, 6))}
                className="w-40 px-3 h-11 rounded-lg border border-input text-[15px] font-semibold tabular-nums tracking-[0.2em] outline-none bg-white focus:border-ring focus:ring-[3px] focus:ring-ring/30" />
              <button type="button" onClick={() => setStage("id")} className="ml-3 text-[12px] font-medium text-primary hover:underline">Change ABHA</button>
            </div>

            {/* consent request — what the applicant approves */}
            <div className="rounded-lg border border-border bg-secondary/40 p-3 space-y-3">
              <div className="text-[11px] uppercase tracking-[0.06em] font-semibold text-muted-foreground">Consent request</div>
              <div>
                <div className="text-[12px] font-medium mb-1.5">Records to share</div>
                <div className="flex flex-wrap gap-2">
                  {ABHA_HI_TYPES.map((t) => (
                    <button key={t} type="button" onClick={() => toggleHi(t)}
                      className={`inline-flex items-center gap-1.5 h-8 px-3 rounded-lg border text-[12.5px] font-semibold transition-colors ${
                        hiTypes.includes(t) ? "border-primary bg-primary/[0.08] text-primary" : "border-border bg-white hover:border-muted-foreground/30"}`}>
                      {hiTypes.includes(t) && <Check weight="bold" className="size-3" />}{t}
                    </button>
                  ))}
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-[12px]">
                <label className="flex items-center gap-2">
                  <span className="text-muted-foreground">Date range</span>
                  <select value={range} onChange={(e) => setRange(e.target.value)}
                    className="h-8 px-2 rounded-lg border border-input bg-white text-[12.5px] font-medium outline-none focus:border-ring">
                    {ABHA_RANGES.map((r) => <option key={r}>{r}</option>)}
                  </select>
                </label>
                <span className="text-muted-foreground">Purpose <span className="font-semibold text-foreground">Insurance underwriting</span></span>
              </div>
            </div>

            {msg && <p className="flex items-center gap-1.5 text-[12px] text-amber-700"><Warning weight="fill" className="size-3.5 shrink-0" /> {msg}</p>}
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setOpen(false)}
                className="rounded-md border border-border text-[13px] font-medium px-4 h-9 hover:border-muted-foreground/40 transition-colors">Cancel</button>
              <button type="button" disabled={otp.length < 6 || !hiTypes.length || busy} onClick={verifyAndFetch}
                className="inline-flex items-center gap-2 rounded-md bg-primary text-primary-foreground text-[13px] font-medium px-4 h-9 hover:bg-primary/90 transition-colors disabled:opacity-50">
                {busy ? (<><Spinner weight="bold" className="size-4 animate-spin" /> Fetching records…</>) : "Approve consent & fetch"}
              </button>
            </div>
          </div>
        )}
      </Modal>
    </section>
  )
}

// ─────────────────────────── shared ───────────────────────────

function RegionHead({ title, hint }: { title: string; hint: string }) {
  return (
    <div className="mb-3">
      <h2 className="text-[15px] font-bold tracking-tight">{title}</h2>
      <p className="text-[12px] text-muted-foreground mt-0.5">{hint}</p>
    </div>
  )
}

