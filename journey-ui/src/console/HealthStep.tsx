import { useEffect, useRef, useState } from "react"
import QRCode from "qrcode"
import { Tooltip } from "radix-ui"
import type { AppSnapshot } from "./useJourney"
import {
  Check, Spinner, SealCheck, Warning, QrCode, Copy, Info,
  Pulse, Heartbeat, Wind, Drop, FirstAid, X, Plus, UploadSimple, CaretDown,
} from "@phosphor-icons/react"
import { Modal } from "./Modal"
import { HealthChatPanel } from "./HealthChatPanel"

// STEP 4 — Health Declaration (TERM LIFE, single life). Originally modelled on the real
// Indian Declaration of Good Health (LIC Form 300 Rev-2024 Section III); simplified
// 2026-08-21 (explicit request) down to 3 condition Yes/No screeners — hypertension,
// kidney, any other ailment — each revealing a short detail line on Yes. A screener whose
// matching condition was already covered in the AI follow-up chat (Step 4's
// "healthchat" sub-step) is skipped here rather than re-asked. Then always: height/weight
// (->BMI), tobacco/alcohol/drugs (LIC Section II), family history. Then a live NuralX
// face scan and an ABHA consent+fetch.
//
// Persists via /api/journey/health on Continue (Console.saveStep). Face scan + ABHA run
// on their own buttons (they mutate the bundle server-side and light the rail immediately).

// Simplified to 3 questions (2026-08-21, explicit request): the original 6-screener LIC
// form is replaced entirely — hospitalization/current-meds/planned-surgery/prior-decline/
// recent-doctor-visit are dropped, keeping only condition questions. `aiBucket` links a
// screener to the matching health-agent CONDITION_BUCKETS key (journey/health_agent/
// config.py) — if the AI chat already flagged+covered that bucket, this screener is
// SKIPPED rather than asked again (see `visibleScreeners` in HealthStep below).
// `other_ailment` has no aiBucket: the agent's fixed catalog doesn't have an open-ended
// "anything else" bucket, so it's always asked.
const SCREENERS: { id: string; q: string; reveal: "detail"; detailLabel?: string; aiBucket?: string }[] = [
  { id: "hypertension", reveal: "detail", detailLabel: "Since when, and is it controlled?",
    q: "Have you ever been diagnosed with high blood pressure / hypertension?", aiBucket: "hypertension" },
  { id: "kidney", reveal: "detail", detailLabel: "Since when, and current treatment?",
    q: "Have you ever been diagnosed with a kidney condition?", aiBucket: "renal_hepatic" },
  { id: "other_ailment", reveal: "detail", detailLabel: "Please describe",
    q: "Any other medical condition not already covered in this application?" },
]

// first-degree relatives + the hereditary conditions the form calls out (LIC 300 §III)
const FAMILY_MEMBERS = ["Father", "Mother", "Brother", "Sister"]
const FAMILY_CONDITIONS = ["Heart disease", "Stroke", "High blood pressure", "Diabetes", "Cancer", "Kidney disease"]

export type HealthState = {
  screeners: Record<string, boolean>            // all default false (No)
  screenerDetail: Record<string, string>        // free text for a "detail"-reveal screener
  height_cm: number | ""
  weight_kg: number | ""
  tobacco: boolean; tobacco_qty: string
  alcohol: boolean; alcohol_qty: string
  drugs: boolean; drugs_qty: string
  family_history: string[]                       // ["Father: Diabetes", ...]
}

export const emptyHealth: HealthState = {
  screeners: {}, screenerDetail: {}, height_cm: "", weight_kg: "",
  tobacco: false, tobacco_qty: "", alcohol: false, alcohol_qty: "",
  drugs: false, drugs_qty: "", family_history: [],
}

// Flatten HealthState -> the /api/journey/health body shape. The 3 simplified screeners
// (hypertension/kidney/other_ailment) ARE conditions, so a Yes+detail folds straight into
// the `conditions` list — same shape "Name — detail text" the engine already expects.
const SCREENER_CONDITION_LABEL: Record<string, string> = {
  hypertension: "High blood pressure", kidney: "Kidney condition", other_ailment: "Other",
}
export function healthPayload(h: HealthState) {
  const screenerConditions = SCREENERS
    .filter((s) => h.screeners[s.id])
    .map((s) => {
      const detail = h.screenerDetail[s.id]
      const label = SCREENER_CONDITION_LABEL[s.id] ?? s.id
      return detail ? `${label} — ${detail}` : label
    })
  const qty = (on: boolean, q: string, label: string) => (on && q ? `${label}: ${q}` : "")
  const habits = [qty(h.tobacco, h.tobacco_qty, "Tobacco"), qty(h.alcohol, h.alcohol_qty, "Alcohol"), qty(h.drugs, h.drugs_qty, "Drugs")].filter(Boolean).join("; ")
  return {
    conditions: screenerConditions,
    height_cm: h.height_cm || null,
    weight_kg: h.weight_kg || null,
    tobacco: h.tobacco, alcohol: h.alcohol, drugs: h.drugs,
    ongoing_medication: null,
    past_medical_history: habits || null,
    family_history: h.family_history,
  }
}

// Inverse of healthPayload: rebuild HealthState from the saved /api/journey/app payload so
// a revisit prefills what the applicant already declared (form ⇄ rail stay in sync).
// Best-effort — matches a "Label — detail" condition string back to its screener id via
// SCREENER_CONDITION_LABEL.
type HealthPayload = {
  conditions?: string[]; height_cm?: number | null; weight_kg?: number | null
  tobacco?: boolean; alcohol?: boolean; drugs?: boolean
  ongoing_medication?: string | null; past_medical_history?: string | null
  family_history?: string[]
}
export function healthFromPayload(p: HealthPayload): HealthState {
  const screeners: Record<string, boolean> = {}
  const screenerDetail: Record<string, string> = {}
  const labelToId = Object.fromEntries(Object.entries(SCREENER_CONDITION_LABEL).map(([id, label]) => [label.toLowerCase(), id]))
  for (const raw of p.conditions || []) {
    const [namePart, detailPart = ""] = raw.split(/\s+—\s+/, 2)
    const id = labelToId[namePart.trim().toLowerCase()]
    if (!id) continue  // a condition string from before this simplification — drop silently
    screeners[id] = true
    if (detailPart) screenerDetail[id] = detailPart
  }
  return {
    ...emptyHealth,
    screeners, screenerDetail,
    height_cm: p.height_cm ?? "",
    weight_kg: p.weight_kg ?? "",
    tobacco: !!p.tobacco, alcohol: !!p.alcohol, drugs: !!p.drugs,
    family_history: p.family_history || [],
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

// Four sub-steps, REORDERED per HEALTH_AGENT_PLAN.md §7: intake (face scan/ABHA/
// prescription) must come FIRST because the conversational deep-dive's triage step
// needs those facts before it can run anything — the fixed mandatory screeners +
// vitals/lifestyle don't depend on triage, so they move to the end.
//   OLD: Health screeners -> Vitals & lifestyle -> Face scan & ABHA
//   NEW: Face scan & ABHA -> Conversational deep-dive (NEW) -> Health -> Vitals & lifestyle
export const HEALTH_SUBSTEPS = [
  { key: "facescan", label: "Face scan & ABHA" },
  { key: "healthchat", label: "Follow-up questions" },
  { key: "health", label: "Health" },
  { key: "vitals", label: "Vitals & lifestyle" },
] as const

// The sub-steps Console paginates over. Fixed four — nothing auto-skips anymore.
export function visibleHealthSubSteps(_h: HealthState): { key: string; label: string }[] {
  return HEALTH_SUBSTEPS.map(({ key, label }) => ({ key, label }))
}

export function HealthStep({
  appId, snap, value, onChange, subStep = 0, onHealthChatDone,
}: {
  appId: number | null; snap: AppSnapshot
  value: HealthState; onChange: (s: HealthState) => void
  subStep?: number   // which visible sub-step to render (Console drives it via the footer)
  onHealthChatDone?: () => void   // gates Continue on the "healthchat" sub-step (§7)
}) {
  const set = (patch: Partial<HealthState>) => onChange({ ...value, ...patch })
  const bmi = bmiOf(value.height_cm, value.weight_kg)

  const visible = visibleHealthSubSteps(value)
  const active = visible[Math.min(subStep, visible.length - 1)]?.key ?? "health"

  // Screener skip: a bucket the AI chat already flagged+ran a thread on doesn't need
  // asking again here — the same clinical fact, asked twice, reads as broken not
  // thorough. `flagged` = triage said this condition has evidence; a THREAD existing
  // for it (regardless of `done`) means the applicant already answered questions about
  // it in the chat. Screeners with no `aiBucket` (other_ailment) are never skippable —
  // there's no matching bucket in the AI's fixed catalog to check against.
  const aiCoveredBuckets = new Set(
    (snap.health_agent?.flagged ?? [])
      .map((f) => f.bucket)
      .filter((b) => snap.health_agent?.threads?.[b])
  )
  const visibleScreeners = SCREENERS.filter((s) => !s.aiBucket || !aiCoveredBuckets.has(s.aiBucket))
  const anyYes = visibleScreeners.some((s) => value.screeners[s.id])

  const setScreener = (id: string, v: boolean) => {
    const patch: Partial<HealthState> = { screeners: { ...value.screeners, [id]: v } }
    // Answering No must retract what a Yes revealed — else the detail text persists and
    // keeps scoring even though the screener now reads No (the form ⇄ score mismatch).
    if (!v) patch.screenerDetail = { ...value.screenerDetail, [id]: "" }
    set(patch)
  }
  const setDetail = (id: string, text: string) =>
    set({ screenerDetail: { ...value.screenerDetail, [id]: text } })

  return (
    <div className="space-y-8">
      {/* ── FACE SCAN + ABHA ── the external scans, FIRST (HEALTH_AGENT_PLAN.md §7):
          the conversational deep-dive's triage needs these facts before it can run. ── */}
      {active === "facescan" && (
        <div className="space-y-8 animate-[fade-up_.2s_ease]">
          <FaceScan appId={appId} snap={snap} />
          <AbhaFetch appId={appId} snap={snap} />
          <PrescriptionUpload appId={appId} snap={snap} />
        </div>
      )}

      {/* ── CONVERSATIONAL DEEP-DIVE ── adaptive follow-up, triaged from face-scan/ABHA/
          prescription facts (HEALTH_AGENT_PLAN.md §3-§7). Runs once, right after intake. ── */}
      {active === "healthchat" && (
        <HealthChatPanel appId={appId} snap={snap} onAllDone={() => onHealthChatDone?.()} />
      )}

      {/* ── HEALTH ── 3 condition screeners; a Yes reveals its own detail line inline.
          A screener already covered in the AI follow-up chat is skipped, not re-asked. ── */}
      {active === "health" && (
        <section className="animate-[fade-up_.2s_ease]">
          <RegionHead title="Health declaration" hint="Answer for the person being insured. Everything defaults to No — a Yes asks only for the details that matter." />
          {visibleScreeners.length < SCREENERS.length && (
            <p className="mb-2.5 text-[12px] text-muted-foreground">
              {SCREENERS.length - visibleScreeners.length === 1 ? "One question" : `${SCREENERS.length - visibleScreeners.length} questions`} already covered in the follow-up chat above — not asked again here.
            </p>
          )}
          <div className="space-y-2.5">
            {visibleScreeners.map((s) => (
              <ScreenerCard key={s.id} screener={s} yes={!!value.screeners[s.id]}
                onYesNo={(v) => setScreener(s.id, v)}
                detail={value.screenerDetail[s.id] || ""} onDetail={(t) => setDetail(s.id, t)} />
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
    </div>
  )
}

// ─────────────────────────── screener card (inline reveal) ───────────────────────────

// One screener: the Yes/No question, and — on Yes — a single detail line inline.
function ScreenerCard({
  screener, yes, onYesNo, detail, onDetail,
}: {
  screener: { id: string; q: string; reveal: "detail"; detailLabel?: string }
  yes: boolean; onYesNo: (v: boolean) => void
  detail: string; onDetail: (t: string) => void
}) {
  return (
    <div className={`rounded-xl border transition-colors ${yes ? "border-primary bg-primary/[0.03]" : "border-border bg-white"}`}>
      <div className="flex items-start gap-4 p-4">
        <p className="text-[13.5px] leading-snug flex-1 min-w-0">{screener.q}</p>
        <YesNoToggle value={yes} onChange={onYesNo} />
      </div>

      {yes && (
        <div className="px-4 pb-4 animate-[fade-up_.15s_ease]">
          <input autoFocus placeholder={screener.detailLabel || "Please give details"}
            value={detail} onChange={(e) => onDetail(e.target.value)}
            className="w-full px-3 h-10 rounded-lg border border-input text-[13px] outline-none bg-white focus:border-ring focus:ring-[3px] focus:ring-ring/30" />
        </div>
      )}
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

// Mirrors the backend's mock (journey/step_routes.py _merge_mock_vitals) so Shift+D and
// the no-vendor-configured path show the same clean-vitals numbers.
const DEMO_MOCK_RPPG: Rppg = {
  status: "available", consented: true,
  vitals: { heart_rate: 74, respiratory_rate: 16, spo2: 98, bp: { systolic: 118, diastolic: 76 } },
  vitals_extra: {
    map: 90, pulse_pressure: 42, cardiac_workload: 3.2, prq: 3.4,
    hemoglobin: 14.2, hba1c: 5.4,
    stress_index: 42, stress_level: 1, stress_index_norm: 12,
    wellness_index: 7, wellness_level: 3,
    sdnn: 58, rmssd: 44, mean_rri: 812, lf_hf: 1.6, sd1: 31, sd2: 83,
    pns_index: 0.4, sns_index: -0.2, pns_zone: 2, sns_zone: 2,
    risk_high_bp: 0, risk_hba1c: 0, risk_glucose: 0, risk_cholesterol: 0, risk_low_hemoglobin: 0,
  },
}

const bpText = (bp: unknown): string => {
  if (bp && typeof bp === "object") {
    const o = bp as { systolic?: number; diastolic?: number }
    if (o.systolic != null && o.diastolic != null) return `${Math.round(o.systolic)}/${Math.round(o.diastolic)}`
  }
  return typeof bp === "string" ? bp : "—"
}
const n0 = (v?: number) => (v != null ? String(Math.round(v)) : "—")
const n1 = (v?: number) => (v != null ? (Math.round(v * 100) / 100).toString() : "—")

// Plain-language explanations, one per parameter — reworded from
// docs/Vital Signs and Health Indicators Information.pdf, no medical jargon.
const INFO: Record<string, string> = {
  heart_rate: "How many times your heart beats per minute. Normal at rest is 60-100.",
  respiratory_rate: "How many breaths you take per minute. Normal at rest is 12-20.",
  spo2: "How much oxygen your blood is carrying from your lungs. Normal is 95-100%.",
  bp: "The force of blood pushing on your artery walls — two numbers: pressure while the heart beats (top) and while it rests (bottom).",
  map: "The average blood pressure over one full heartbeat — tells us if organs are getting enough blood.",
  pulse_pressure: "The gap between your top and bottom blood pressure numbers — reflects how flexible your arteries are.",
  cardiac_workload: "How hard your heart is working right now to pump blood — lower is more efficient.",
  prq: "How well your heart and lungs are working together. Normal is about 5.",
  hemoglobin: "The protein in blood that carries oxygen around your body.",
  hba1c: "Your average blood sugar level over the last 2-3 months.",
  stress_index: "A number reflecting how your body is handling challenges right now, based on heartbeat patterns.",
  stress_level: "A simple Low-to-Very High read of your current stress, based on the stress index.",
  wellness_index: "An overall score (0-10) estimating your cardiovascular wellness from this scan alone.",
  wellness_level: "The vendor's own internal wellness reading — shown as-is; no plain-language scale confirmed yet.",
  sdnn: "How much your heartbeat timing naturally varies — higher usually means better fitness and stress resilience.",
  rmssd: "A measure of beat-to-beat heartbeat variation — higher can mean you're well-rested, lower can mean stress or fatigue.",
  mean_rri: "The average time between heartbeats, in milliseconds.",
  sd1: "A short-term heartbeat-variation measure, used to gauge your body's recovery ability.",
  sd2: "A longer-term heartbeat-variation measure, used to gauge your body's stress response.",
  lf_hf: "The balance between your body's 'rest' and 'stress' nervous systems. Normal is roughly 0.27-0.38.",
  pns_index: "How well your body can relax and recover after stress.",
  sns_index: "How ready your body is to react to a stressful or demanding situation.",
  pns_zone: "Recovery ability, in three bands: Low, Normal, or High.",
  sns_zone: "Stress response readiness, in three bands: Low, Normal, or High.",
  risk_high_bp: "Whether your blood pressure reading is above a healthy threshold.",
  risk_hba1c: "Whether your average blood sugar reading is above a healthy threshold.",
  risk_glucose: "Whether your fasting blood sugar reading is above a healthy threshold (only valid if you fasted 8-12 hours first).",
  risk_cholesterol: "Whether your estimated cholesterol is above a healthy threshold.",
  risk_low_hemoglobin: "Whether your hemoglobin reading is below a healthy threshold.",
}

function InfoTip({ text }: { text: string }) {
  return (
    <Tooltip.Provider delayDuration={200}>
      <Tooltip.Root>
        <Tooltip.Trigger asChild>
          <button type="button" onClick={(e) => e.stopPropagation()} aria-label="What is this?"
            className="text-muted-foreground/60 hover:text-primary transition-colors">
            <Info weight="regular" className="size-3" />
          </button>
        </Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Content side="top" align="start" sideOffset={6}
            className="max-w-[240px] rounded-md bg-foreground text-background text-[11.5px] leading-snug px-2.5 py-2 shadow-lg z-50">
            {text}
            <Tooltip.Arrow className="fill-foreground" />
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  )
}

// The ~10 parameters with a vendor-defined normal/abnormal band (Assessment-Guidelines.pdf).
// Everything else has no vendor-confirmed range, so it stays a plain value tile rather than
// inventing thresholds. `band(value, gender?)` returns the vendor's own wording + tone.
type Band = { word: string; tone: "ok" | "warn" | "bad" }
const RICH_BANDS: Record<string, { ref: string; band: (v: number, gender?: string) => Band | null }> = {
  heart_rate: {
    ref: "60-100 bpm",
    band: (v) => (v > 100 ? { word: "High", tone: "bad" } : v < 60 ? { word: "Low", tone: "warn" } : { word: "Normal", tone: "ok" }),
  },
  respiratory_rate: {
    ref: "12-20 br/min",
    band: (v) => (v > 20 ? { word: "High", tone: "bad" } : v < 12 ? { word: "Low", tone: "warn" } : { word: "Normal", tone: "ok" }),
  },
  spo2: {
    ref: "95-100%",
    band: (v) => (v < 95 ? { word: "Low", tone: "bad" } : { word: "Normal", tone: "ok" }),
  },
  sdnn: {
    ref: ">50 ms",
    band: (v) => (v < 50 ? { word: "Low", tone: "warn" } : { word: "Normal", tone: "ok" }),
  },
  stress_index: {
    ref: "0-80 Low · 81-150 Normal · 151-300 Mild · 301-600 High · >600 Very High",
    band: (v) =>
      v <= 80 ? { word: "Low", tone: "ok" } : v <= 150 ? { word: "Normal", tone: "ok" }
      : v <= 300 ? { word: "Mild", tone: "warn" } : v <= 600 ? { word: "High", tone: "bad" } : { word: "Very High", tone: "bad" },
  },
  pns_zone: {
    ref: "1-Low · 2-Normal · 3-High",
    band: (v) => (v <= 1 ? { word: "Low", tone: "warn" } : v === 2 ? { word: "Normal", tone: "ok" } : { word: "High", tone: "ok" }),
  },
  sns_zone: {
    ref: "1-Low · 2-Normal · 3-High",
    band: (v) => (v >= 3 ? { word: "High", tone: "bad" } : v === 2 ? { word: "Normal", tone: "ok" } : { word: "Low", tone: "ok" }),
  },
  hemoglobin: {
    ref: "Female 12-16 · Male 14-18 g/dL",
    band: (v, gender) => {
      const [lo, hi] = gender === "male" ? [14, 18] : gender === "female" ? [12, 16] : [null, null]
      if (lo == null) return null // gender unknown — no vendor band to apply
      return v < lo ? { word: "Low", tone: "bad" } : v > hi! ? { word: "High", tone: "warn" } : { word: "Normal", tone: "ok" }
    },
  },
}
// Blood pressure is banded on systolic only (Assessment-Guidelines.pdf #6).
const bpBand = (systolic?: number): Band | null =>
  systolic == null ? null
  : systolic >= 130 ? { word: "High", tone: "bad" } : systolic < 90 ? { word: "Low", tone: "warn" } : { word: "Normal", tone: "ok" }

function toneClass(tone: "ok" | "warn" | "bad") {
  return tone === "ok" ? "stat-ok" : tone === "warn" ? "stat-warn" : "stat-bad"
}

// Vendor risk flags (Assessment-Guidelines.pdf #10): EXACTLY 1-Low, 2-Medium, 3-High —
// display only, never a decision. No "0 = no risk" tier exists in the vendor spec.
const RISK_BAND: Record<number, string> = { 1: "Low", 2: "Medium", 3: "High" }
const riskTone = (v?: number): "ok" | "warn" | "bad" => (v === 1 ? "ok" : v === 2 ? "warn" : v === 3 ? "bad" : "ok")
// NuralX categorical Stress Level (1-based, vendor-confirmed wording — Vital Signs doc)
const STRESS_LABEL = ["", "Low", "Normal", "Mild", "High", "Very high"]
// Wellness Score band (wellness_index 0-10, Assessment-Guidelines / Vital Signs docs):
// Low 1-3, Medium 4-7, High 8-10. wellness_level has NO vendor-defined wording — shown
// as a plain number, never a fabricated word (there was no "Poor/Fair/Good..." scale).
const wellnessBand = (v?: number): "Low" | "Medium" | "High" | null =>
  v == null ? null : v <= 3 ? "Low" : v <= 7 ? "Medium" : "High"
const wellnessTone = (v?: number): "ok" | "warn" | "bad" => {
  const b = wellnessBand(v)
  return b === "High" ? "ok" : b === "Medium" ? "warn" : "bad"
}

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
  const [qrDataUrl, setQrDataUrl] = useState<string>("")
  const [msg, setMsg] = useState<string>("")
  const [copied, setCopied] = useState(false)
  const [expanded, setExpanded] = useState(true)
  const pollRef = useRef<number | null>(null)

  // scanUrl now points at OUR /face-scan/{token} instructions page (not NuralX's raw URL —
  // that's only issued after the applicant taps Start there). Render it as a QR so the
  // applicant can scan it with their own phone instead of the agent forwarding a link.
  useEffect(() => {
    if (!scanUrl) { setQrDataUrl(""); return }
    QRCode.toDataURL(scanUrl, { width: 176, margin: 1 }).then(setQrDataUrl).catch(() => setQrDataUrl(""))
  }, [scanUrl])

  // Demo escape hatch: Shift+D while the scan has failed fills the SAME clean-vitals mock
  // the backend uses when NURALX_BASE_URL is unset, so a live-vendor hiccup mid-demo
  // doesn't block the rest of the walkthrough. Client-side only — no network round-trip.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.shiftKey && e.key.toLowerCase() === "d" && state === "error") {
        setRppg(DEMO_MOCK_RPPG)
        setLiveness({ status: "available", liveness_pass: true, liveness_score: 0.96, face_match_score: 0.94, deepfake_flag: false })
        setState("done")
        if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [state])

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
  const gender = snap.applicant?.gender
  const livenessFailed = liveness?.liveness_pass === false || liveness?.deepfake_flag === true
  const wellnessScoreBand = wellnessBand(x.wellness_index)

  return (
    <section>
      <RegionHead title="Face scan" hint="A 60-second phone scan reads liveness and vitals." />

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
              <button type="button" onClick={() => setExpanded((e) => !e)}
                aria-label={expanded ? "Collapse results" : "Expand results"}
                className="grid place-items-center size-7 rounded-md border border-border text-muted-foreground hover:text-foreground hover:border-primary transition-colors shrink-0">
                <CaretDown weight="bold" className={`size-3.5 transition-transform ${expanded ? "" : "-rotate-90"}`} />
              </button>
            </div>

            {expanded && (
              <>
                {/* WELLNESS SCORE — the vendor's headline metric, leads the report
                    (Vital Signs doc: 0-10, Low 1-3/Medium 4-7/High 8-10). wellness_level
                    has no vendor-confirmed wording, shown as a bare number only. */}
                <div className="rounded-lg border border-border bg-[#faf9f7] px-4 py-3 flex items-center gap-4">
                  <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-[0.07em] font-semibold text-muted-foreground">
                    Wellness score <InfoTip text={INFO.wellness_index} />
                  </div>
                  <span className="text-[22px] font-bold tabular-nums">{n0(x.wellness_index)}<span className="text-[12px] text-muted-foreground font-normal">/10</span></span>
                  {wellnessScoreBand && (
                    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-semibold ${toneClass(wellnessTone(x.wellness_index))}`}>
                      {wellnessScoreBand}
                    </span>
                  )}
                  <span className="ml-auto flex items-center gap-1.5 text-[11px] text-muted-foreground">
                    Wellness level (vendor scale): <span className="font-semibold text-foreground">{n0(x.wellness_level)}</span>
                    <InfoTip text={INFO.wellness_level} />
                  </span>
                </div>

                {/* BASIC VITAL SIGNS — vendor doc order; the ~6 with a defined band get
                    the full pill+sentence+ref tile, the rest stay simple value tiles. */}
                <VitalGrid title="Basic vital signs">
                  <RichVitalTile icon={Heartbeat} label="Heart rate" infoKey="heart_rate"
                    display={n0(v.heart_rate)} unit="bpm"
                    ref={RICH_BANDS.heart_rate.ref} band={v.heart_rate != null ? RICH_BANDS.heart_rate.band(v.heart_rate) : null} />
                  <RichVitalTile icon={Wind} label="Breathing rate" infoKey="respiratory_rate"
                    display={n0(v.respiratory_rate)} unit="br/min"
                    ref={RICH_BANDS.respiratory_rate.ref} band={v.respiratory_rate != null ? RICH_BANDS.respiratory_rate.band(v.respiratory_rate) : null} />
                  <RichVitalTile icon={Drop} label="Oxygen saturation" infoKey="spo2"
                    display={n0(v.spo2)} unit="%"
                    ref={RICH_BANDS.spo2.ref} band={v.spo2 != null ? RICH_BANDS.spo2.band(v.spo2) : null} />
                  <RichVitalTile icon={Pulse} label="Blood pressure" infoKey="bp"
                    display={bpText(v.bp)} unit="mmHg"
                    ref="Sys 90-130 mmHg"
                    band={bpBand(typeof v.bp === "object" && v.bp ? (v.bp as { systolic?: number }).systolic : undefined)} />
                  <VitalTile icon={Heartbeat} label="Mean art. pr." infoKey="map" value={n0(x.map)} unit="mmHg" />
                  <VitalTile icon={Heartbeat} label="Pulse pressure" infoKey="pulse_pressure" value={n0(x.pulse_pressure)} unit="mmHg" />
                  <VitalTile icon={Heartbeat} label="Cardiac load" infoKey="cardiac_workload" value={n1(x.cardiac_workload)} unit="" />
                  <VitalTile icon={Pulse} label="PRQ" infoKey="prq" value={n1(x.prq)} unit="" />
                </VitalGrid>

                {/* BLOODLESS BLOOD TESTS — under-research per the vendor doc */}
                <VitalGrid title="Bloodless blood tests" sub="Under research · screening estimates only">
                  <RichVitalTile icon={Drop} label="Hemoglobin" infoKey="hemoglobin"
                    display={n1(x.hemoglobin)} unit="g/dL"
                    ref={RICH_BANDS.hemoglobin.ref} band={x.hemoglobin != null ? RICH_BANDS.hemoglobin.band(x.hemoglobin, gender) : null} />
                  <VitalTile icon={Pulse} label="HbA1c" infoKey="hba1c" value={n1(x.hba1c)} unit="%" />
                </VitalGrid>

                {/* RISKS — vendor screening-risk flags, shown with severity, never a decision */}
                <RiskFlags x={x} />

                {/* STRESS */}
                <VitalGrid title="Stress">
                  <RichVitalTile icon={Pulse} label="Stress index" infoKey="stress_index"
                    display={n0(x.stress_index)} unit=""
                    ref={RICH_BANDS.stress_index.ref} band={x.stress_index != null ? RICH_BANDS.stress_index.band(x.stress_index) : null} />
                  <VitalTile icon={Pulse} label="Stress level" infoKey="stress_level"
                    value={x.stress_level != null ? (STRESS_LABEL[Math.round(x.stress_level)] ?? n0(x.stress_level)) : "—"} unit="" />
                  <VitalTile icon={Pulse} label="Normalized stress" infoKey="stress_index" value={n0(x.stress_index_norm)} unit="%" />
                </VitalGrid>

                {/* HEART RATE VARIABILITY */}
                <VitalGrid title="Heart rate variability">
                  <RichVitalTile icon={Pulse} label="SDNN" infoKey="sdnn"
                    display={n0(x.sdnn)} unit="ms"
                    ref={RICH_BANDS.sdnn.ref} band={x.sdnn != null ? RICH_BANDS.sdnn.band(x.sdnn) : null} />
                  <VitalTile icon={Pulse} label="Mean RRI" infoKey="mean_rri" value={n0(x.mean_rri)} unit="ms" />
                  <VitalTile icon={Pulse} label="RMSSD" infoKey="rmssd" value={n0(x.rmssd)} unit="ms" />
                </VitalGrid>

                {/* ADVANCED HRV */}
                <VitalGrid title="Advanced heart rate variability">
                  <RichVitalTile icon={Heartbeat} label="Recovery ability (PNS zone)" infoKey="pns_zone"
                    display={x.pns_zone != null ? n0(x.pns_zone) : "—"} unit=""
                    ref={RICH_BANDS.pns_zone.ref} band={x.pns_zone != null ? RICH_BANDS.pns_zone.band(x.pns_zone) : null} />
                  <VitalTile icon={Heartbeat} label="PNS index" infoKey="pns_index" value={n1(x.pns_index)} unit="" />
                  <RichVitalTile icon={Heartbeat} label="Stress response (SNS zone)" infoKey="sns_zone"
                    display={x.sns_zone != null ? n0(x.sns_zone) : "—"} unit=""
                    ref={RICH_BANDS.sns_zone.ref} band={x.sns_zone != null ? RICH_BANDS.sns_zone.band(x.sns_zone) : null} />
                  <VitalTile icon={Heartbeat} label="SNS index" infoKey="sns_index" value={n1(x.sns_index)} unit="" />
                  <VitalTile icon={Pulse} label="SD1" infoKey="sd1" value={n0(x.sd1)} unit="ms" />
                  <VitalTile icon={Pulse} label="SD2" infoKey="sd2" value={n0(x.sd2)} unit="ms" />
                  <VitalTile icon={Pulse} label="LF/HF" infoKey="lf_hf" value={n1(x.lf_hf)} unit="" />
                </VitalGrid>

                {/* RRI DATA — the raw beat-to-beat waveform, as a sparkline; empty
                    placeholder when the scan didn't return the series. */}
                <div>
                  <div className="text-[11px] uppercase tracking-[0.07em] font-semibold text-muted-foreground mb-2">RR-interval data</div>
                  {Array.isArray(x.rri_series) && x.rri_series.length > 4 ? (
                    <Sparkline series={x.rri_series as unknown as number[]} />
                  ) : (
                    <div className="rounded-lg border border-border bg-[#faf9f7] p-3 text-[12px] text-muted-foreground">Not available for this scan</div>
                  )}
                </div>
              </>
            )}
          </div>
        ) : state === "waiting" && qrDataUrl ? (
          <div className="grid sm:grid-cols-[auto_1fr] gap-5">
            <div className="flex flex-col items-center gap-2">
              <img src={qrDataUrl} alt="Scan with your phone" className="size-[168px] rounded-lg border border-border" />
              <button type="button" onClick={async () => {
                try { await navigator.clipboard.writeText(scanUrl); setCopied(true); setTimeout(() => setCopied(false), 1800) } catch { /* clipboard unavailable */ }
              }} className="inline-flex items-center gap-1.5 rounded-md border border-border text-[12px] font-medium px-3 h-8 hover:border-primary transition-colors">
                <Copy weight="bold" className="size-3.5" /> {copied ? "Copied!" : "Copy scan link"}
              </button>
            </div>
            <div>
              <p className="text-[13px] font-semibold mb-3">Scan with your phone</p>
              <ol className="space-y-2.5">
                {[
                  ["Open your phone camera", "No app download required — your phone's built-in camera is enough."],
                  ["Point it at this QR code", "A link will appear on your phone screen. Tap it to open the scan page."],
                  ["Complete the 60-second scan", "Look directly at the front camera and stay still."],
                  ["Results appear here automatically", "This panel updates the moment the scan is done — no refresh needed."],
                ].map(([title, body], i) => (
                  <li key={title} className="flex items-start gap-2.5">
                    <span className="grid place-items-center size-5 rounded-full bg-primary text-primary-foreground text-[11px] font-bold shrink-0 mt-0.5">{i + 1}</span>
                    <div className="min-w-0">
                      <div className="text-[12.5px] font-medium">{title}</div>
                      <div className="text-[12px] text-muted-foreground leading-snug">{body}</div>
                    </div>
                  </li>
                ))}
              </ol>
              <p className="mt-3 flex items-center gap-1.5 text-[12px] text-muted-foreground">
                <Spinner weight="bold" className="size-3.5 animate-spin text-primary" /> Waiting for your phone…
              </p>
            </div>
          </div>
        ) : (
          <div className="flex items-start gap-3">
            <span className="grid place-items-center size-9 rounded-lg bg-primary/10 text-primary shrink-0"><Pulse weight="regular" className="size-[18px]" /></span>
            <div className="min-w-0 flex-1">
              <p className="text-[13px] text-muted-foreground leading-snug">
                The applicant opens the scan link on their own phone and holds still for about a minute. Liveness and rPPG vitals return here automatically.
              </p>
              {state === "error" && (
                <>
                  <p className="mt-1.5 flex items-center gap-1.5 text-[12px] text-amber-700"><Warning weight="fill" className="size-3.5 shrink-0" /> {msg}</p>
                  <p className="mt-1 text-[11px] text-muted-foreground">Demo: press <kbd className="rounded border border-border bg-[#faf9f7] px-1 py-0.5 font-mono">Shift</kbd>+<kbd className="rounded border border-border bg-[#faf9f7] px-1 py-0.5 font-mono">D</kbd> to fill mock results.</p>
                </>
              )}
              <div className="mt-2.5 flex flex-wrap items-center gap-2">
                <button type="button" disabled={state === "starting" || state === "waiting"} onClick={start}
                  className="inline-flex items-center gap-2 rounded-md bg-primary text-primary-foreground text-[13px] font-medium px-4 h-9 hover:bg-primary/90 transition-colors disabled:opacity-60">
                  {state === "starting" ? (<><Spinner weight="bold" className="size-4 animate-spin" /> Starting…</>)
                    : (<><QrCode weight="bold" className="size-4" /> Start face scan</>)}
                </button>
              </div>
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

function VitalTile({ icon: Icon, label, value, unit, infoKey }: {
  icon: React.ElementType; label: string; value: string; unit: string; infoKey?: string
}) {
  return (
    <div className="rounded-lg border border-border bg-[#faf9f7] px-3 py-2.5">
      <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.06em] font-semibold text-muted-foreground">
        <Icon weight="fill" className="size-3 text-primary shrink-0" /> <span className="truncate">{label}</span>
        {infoKey && INFO[infoKey] && <InfoTip text={INFO[infoKey]} />}
      </div>
      <div className="mt-1 flex items-baseline gap-1">
        <span className="text-[18px] font-bold tabular-nums">{value}</span>
        {unit && <span className="text-[11px] text-muted-foreground">{unit}</span>}
      </div>
    </div>
  )
}

// The rich tile (label+info, big value, status pill, plain-language sentence, Ref line) —
// only for the ~10 parameters with a vendor-defined normal/abnormal band. `band` is
// pre-computed by the caller (it needs gender for hemoglobin, systolic-only for BP, etc.).
function RichVitalTile({ icon: Icon, label, infoKey, display, unit, ref: refText, band }: {
  icon: React.ElementType; label: string; infoKey: string; display: string
  unit: string; ref: string; band: Band | null
}) {
  return (
    <div className="rounded-lg border border-border bg-[#faf9f7] px-3 py-2.5">
      <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.06em] font-semibold text-muted-foreground">
        <Icon weight="fill" className="size-3 text-primary shrink-0" /> <span className="truncate">{label}</span>
        <InfoTip text={INFO[infoKey]} />
      </div>
      <div className="mt-1 flex items-baseline gap-1">
        <span className="text-[18px] font-bold tabular-nums">{display}</span>
        {unit && <span className="text-[11px] text-muted-foreground">{unit}</span>}
      </div>
      {band ? (
        <>
          <span className={`mt-1.5 inline-flex items-center rounded-full border px-2 py-0.5 text-[10.5px] font-semibold ${toneClass(band.tone)}`}>
            {band.word}
          </span>
          <p className="mt-1.5 text-[11px] text-muted-foreground leading-snug">
            {display}{unit ? ` ${unit}` : ""} — {band.tone === "ok" ? "within" : "outside"} the normal range.
          </p>
        </>
      ) : (
        <span className="mt-1.5 inline-flex items-center rounded-full border border-border px-2 py-0.5 text-[10.5px] font-semibold text-muted-foreground">
          No data
        </span>
      )}
      <p className="mt-1 text-[10px] text-muted-foreground/70">Ref: {refText}</p>
    </div>
  )
}

// Vendor screening-risk flags (1-Low/2-Medium/3-High). Displayed as severity chips; the
// agent, not this panel, decides — these are facts we surface, never underwriting inputs
// (§1.8). 4 of the 5 are vendor-flagged "Under Research" (Vital Signs doc) — only Blood
// Pressure Risk is production-confirmed.
function RiskFlags({ x }: { x: Record<string, number> }) {
  const flags: { key: string; label: string; underResearch?: boolean }[] = [
    { key: "risk_high_bp", label: "Blood pressure" },
    { key: "risk_hba1c", label: "HbA1c", underResearch: true },
    { key: "risk_glucose", label: "Fasting glucose", underResearch: true },
    { key: "risk_cholesterol", label: "Cholesterol", underResearch: true },
    { key: "risk_low_hemoglobin", label: "Low hemoglobin", underResearch: true },
  ]
  return (
    <div>
      <div className="flex items-baseline gap-2 mb-2">
        <span className="text-[11px] uppercase tracking-[0.07em] font-semibold text-muted-foreground">Screening risk flags</span>
        <span className="text-[11px] text-muted-foreground/70">Estimates · agent decides</span>
      </div>
      <div className="flex flex-wrap gap-2">
        {flags.map((f) => {
          const val = x[f.key]
          if (val == null) {
            return (
              <span key={f.key} className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-[12px] font-semibold text-muted-foreground">
                {f.label}<span className="opacity-70">·</span>—{f.underResearch && <sup className="text-[9px] font-normal">†</sup>}
              </span>
            )
          }
          const tone = riskTone(val)
          const cls = tone === "ok" ? "stat-ok" : tone === "warn" ? "stat-warn" : "stat-bad"
          return (
            <span key={f.key} className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-[12px] font-semibold ${cls}`}>
              {f.label}<span className="opacity-70">·</span>{RISK_BAND[Math.round(val)] ?? "—"}{f.underResearch && <sup className="text-[9px] font-normal">†</sup>}
            </span>
          )
        })}
      </div>
      {flags.some((f) => f.underResearch) && (
        <p className="mt-1.5 text-[10.5px] text-muted-foreground/70">† Under research — vendor has not yet validated this indicator.</p>
      )}
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

function AbhaFetch({ appId, snap }: { appId: number | null; snap: AppSnapshot }) {
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
  const [hiTypes, setHiTypes] = useState<string[]>(["Diagnoses", "Prescriptions"])
  const [range, setRange] = useState("Last 3 years")
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState("")

  const openModal = () => { setStage("id"); setOtp(""); setMsg(""); setOpen(true) }

  async function sendOtp() {
    if (appId == null || !abhaId.trim()) return
    setBusy(true); setMsg("")
    try {
      const r = await fetch("/api/journey/abha/otp/send", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ app_id: appId, abha_id: abhaId.trim(), auth_method: authMethod }),
      })
      const d = await r.json()
      if (d.success) { setStage("otp") }
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
                  ? `${count} record${count > 1 ? "s" : ""} returned. The agent cross-checks these against your declaration later in this step.`
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

// ─────────────────────────── prescription upload (optional, Gemini OCR) ───────────────────────────
// Optional third input (HEALTH_AGENT_PLAN.md §2.1) — a photo/PDF of a prescription or MER,
// read via Gemini vision (POST /api/journey/prescription). Purely additive to triage; skip
// leaves signals.prescription_ocr absent and the agent reasons around it.

// One uploaded file's own result, tracked client-side so each document stays visible and
// attributable — the server-side record (signals.prescription_ocr) MERGES all uploads
// together for triage, which is correct for the agent but loses "which file said what"
// for the applicant/underwriter reviewing this screen.
type DocResult = { name: string; status: "reading" | "done" | "error"; drugs: string[]; note?: string }

function PrescriptionUpload({ appId, snap }: { appId: number | null; snap: AppSnapshot }) {
  const existing = snap.signals.prescription_ocr
  const [docs, setDocs] = useState<DocResult[]>(
    existing?.status === "available" && existing.drug_names?.length
      ? [{ name: "Previously uploaded", status: "done", drugs: existing.drug_names }] : [])
  const inputRef = useRef<HTMLInputElement>(null)

  // Poll for ONE upload to land, diffing against the drug list already known before this
  // upload started — so a merged record still tells us what THIS file specifically added.
  async function pollForNewDrugs(priorDrugs: string[]): Promise<{ drugs: string[]; note?: string }> {
    if (appId == null) return { drugs: [] }
    for (let i = 0; i < 30; i++) {           // ~60s max wait per file, matches OCR's own timeout
      await new Promise((r) => setTimeout(r, 2000))
      try {
        const r = await fetch(`/api/journey/app/${appId}`)
        const d = (await r.json()) as AppSnapshot
        const p = d?.signals?.prescription_ocr
        if (p?.status === "available" || p?.status === "unavailable") {
          if (p.status === "unavailable") return { drugs: [], note: "Couldn't read this file." }
          const all = p.drug_names ?? []
          // Only what THIS upload newly contributed — a doc that repeats an already-known
          // drug (e.g. a refill) legitimately adds nothing new; don't misattribute another
          // document's drugs to this one just to avoid an empty list.
          const added = all.filter((x) => !priorDrugs.includes(x))
          return { drugs: added }
        }
      } catch { /* transient — keep polling */ }
    }
    return { drugs: [], note: "Timed out reading this file." }
  }

  // Multiple files: upload + wait for each ONE AT A TIME (server merges into the same
  // signals.prescription_ocr record — see _merge_prescription_ocr), so results never race.
  async function uploadAll(files: File[]) {
    if (appId == null || !files.length) return
    const startIdx = docs.length
    setDocs((cur) => [...cur, ...files.map((f) => ({ name: f.name, status: "reading" as const, drugs: [] }))])
    let priorDrugs = docs.flatMap((d) => d.drugs)
    for (let i = 0; i < files.length; i++) {
      try {
        const body = new FormData()
        body.append("app_id", String(appId))
        body.append("file", files[i])
        const r = await fetch("/api/journey/prescription", { method: "POST", body }).then((x) => x.json())
        if (!r.success) {
          setDocs((cur) => cur.map((d, j) => j === startIdx + i ? { ...d, status: "error", note: r.message || "Upload failed." } : d))
          continue
        }
        const { drugs, note } = await pollForNewDrugs(priorDrugs)
        priorDrugs = [...priorDrugs, ...drugs]
        setDocs((cur) => cur.map((d, j) => j === startIdx + i
          ? { ...d, status: note && !drugs.length ? "error" : "done", drugs, note } : d))
      } catch {
        setDocs((cur) => cur.map((d, j) => j === startIdx + i ? { ...d, status: "error", note: "Couldn't upload — try again." } : d))
      }
    }
  }

  const busy = docs.some((d) => d.status === "reading")

  return (
    <section>
      <RegionHead title="Prescription (optional)" hint="Upload one or more prescriptions or medical reports — helps us ask fewer follow-up questions." />
      <div className="rounded-xl border border-border bg-white p-4">
        {docs.length > 0 ? (
          <div className="space-y-2">
            {docs.map((d, i) => (
              <div key={i} className="flex items-center gap-3">
                <span className={`grid place-items-center size-9 rounded-lg border shrink-0 ${
                  d.status === "done" ? "stat-ok" : d.status === "error" ? "stat-bad" : "bg-primary/10 text-primary border-transparent"}`}>
                  {d.status === "reading" ? <Spinner weight="bold" className="size-4 animate-spin" />
                    : d.status === "error" ? <Warning weight="fill" className="size-[18px]" />
                    : <FirstAid weight="fill" className="size-[18px]" />}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="text-[13.5px] font-semibold truncate">{d.name}</div>
                  <div className="text-[12px] text-muted-foreground truncate">
                    {d.status === "reading" ? "Reading…"
                      : d.status === "error" ? (d.note || "Couldn't read this file.")
                      : d.drugs.length ? `Noted: ${d.drugs.join(", ")}` : "Read — no new medication noted."}
                  </div>
                </div>
              </div>
            ))}
            <button type="button" disabled={busy} onClick={() => inputRef.current?.click()}
              className="mt-1 text-[12px] font-medium text-primary hover:underline disabled:opacity-50">
              + Add another document
            </button>
          </div>
        ) : (
          <div className="flex items-start gap-3">
            <span className="grid place-items-center size-9 rounded-lg bg-primary/10 text-primary shrink-0"><UploadSimple weight="regular" className="size-[18px]" /></span>
            <div className="min-w-0 flex-1">
              <p className="text-[13px] text-muted-foreground leading-snug">
                Clear photos or PDFs of prescriptions or reports — you can select more than one at a time. We read the medication and notes automatically.
              </p>
              <button type="button" disabled={busy}
                onClick={() => inputRef.current?.click()}
                className="mt-2.5 inline-flex items-center gap-2 rounded-md bg-primary text-primary-foreground text-[13px] font-medium px-4 h-9 hover:bg-primary/90 transition-colors disabled:opacity-60">
                <UploadSimple weight="bold" className="size-4" /> Upload prescription(s)
              </button>
            </div>
          </div>
        )}
        <input ref={inputRef} type="file" accept="image/*,.pdf" multiple className="hidden"
          onChange={(e) => { const files = Array.from(e.target.files ?? []); if (files.length) uploadAll(files); e.target.value = "" }} />
      </div>
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

