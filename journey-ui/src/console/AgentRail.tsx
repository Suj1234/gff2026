import { useEffect, useState } from "react"
import type { AppSnapshot, Rail, RailGroup } from "./useJourney"
import { Pulse, Scales, Warning, SealCheck } from "@phosphor-icons/react"

// Right rail — Option A: ONE outer "Agent Read" card containing the gauge + source ROWS
// (rows, not nested cards, so it stays a card-with-rows and avoids card-in-card stacking).
// The BODY is shared between the desktop rail card and the mobile bottom sheet (RailSheet).

const SEV = {
  ok: { ring: "text-emerald-500", dot: "bg-emerald-500", text: "text-emerald-700" },
  warn: { ring: "text-amber-500", dot: "bg-amber-500", text: "text-amber-700" },
  bad: { ring: "text-red-500", dot: "bg-red-500", text: "text-red-700" },
  idle: { ring: "text-muted-foreground/30", dot: "bg-muted-foreground/30", text: "text-muted-foreground" },
} as const

export type Tone = keyof typeof SEV

export function railTone(band?: string | null): Tone {
  return band === "Low Risk" ? "ok" : band === "Moderate Risk" ? "warn" : band ? "bad" : "idle"
}

export function railRows(rail: Rail | null): RailGroup[] {
  // Two distinct empty states:
  //  - rail === null       → not loaded yet: show a neutral placeholder set so the card
  //                          isn't blank on first paint.
  //  - rail.groups === []  → loaded and legitimately empty (e.g. Step-4 Health sub-step
  //                          with nothing declared): render NOTHING, not the placeholder.
  //                          Previously this fell through to the placeholder and showed the
  //                          Step-1 chip list on Step 4 (the bug).
  const rows = rail == null
    ? ["Identity / KYC", "Occupation", "Contactability"].map((label) => ({
        key: label, label, sub_score: 0, severity: "idle" as const, why: "awaiting source",
      }))
    : rail.groups
  // UI-only: hide the Fraud chip while it's still idle (its input, email intel, arrives on
  // Continue). The engine still computes + uses fraud for the decision — this only suppresses
  // the "awaiting source" placeholder chip. Once fraud has real data it renders normally.
  return rows.filter((g) => !(g.key === "fraud_check" && g.severity === "idle"))
}

export function Gauge({ value, tone, size = 84 }: { value: number | null; tone: Tone; size?: number }) {
  // sweep the ring from 0 up to the value on mount (motion: justified, ~600ms)
  const target = value == null ? 0 : Math.max(0, Math.min(100, value))
  const [pct, setPct] = useState(0)
  useEffect(() => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches
    if (reduce) { setPct(target); return }
    const id = window.requestAnimationFrame(() => setPct(target))
    return () => window.cancelAnimationFrame(id)
  }, [target])
  const r = 32, c = 2 * Math.PI * r
  const fs = size >= 72 ? "text-xl" : "text-sm"
  return (
    <div className="relative grid place-items-center shrink-0" style={{ width: size, height: size }}>
      <svg viewBox="0 0 80 80" className="-rotate-90" style={{ width: size, height: size }}>
        <circle cx="40" cy="40" r={r} fill="none" strokeWidth="6" className="stroke-muted" />
        <circle cx="40" cy="40" r={r} fill="none" strokeWidth="6" strokeLinecap="round"
          className={SEV[tone].ring} stroke="currentColor"
          strokeDasharray={c} strokeDashoffset={c * (1 - pct / 100)}
          style={{ transition: "stroke-dashoffset .6s ease" }} />
      </svg>
      <span className={`absolute ${fs} font-bold tabular-nums`}>{value != null ? Math.round(value) : "—"}</span>
    </div>
  )
}

// Litigation is a NAME-BASED match against court/FIR records — an unreliable identifier
// (common names collide) — so it is informational only, never scored into the Safety
// Score or the underwriting decision (see underwriting/config.py SAFETY_SCORE_WEIGHTS).
// A SEPARATE card, not a row inside Agent Read — it isn't one of the scored groups.
export function LitigationCard({ snap }: { snap: AppSnapshot | null }) {
  const lit = snap?.signals.litigation_fir
  if (!lit || lit.status !== "available") return null
  const cases = lit.cases || []
  const totalCases = lit.total_cases ?? cases.length
  const criminal = lit.criminal_cases ?? cases.filter((c) => c.civil_criminal === "criminal").length
  const firs = lit.firs_registered || 0
  const clean = criminal === 0 && firs === 0 && !(lit.pending_cases || 0)

  return (
    <div className="rounded-2xl border bg-white shadow-[0_1px_2px_rgba(0,0,0,0.04)] overflow-hidden">
      <div className="px-5 py-4 flex items-center justify-between gap-2 border-b bg-[#fbfaf8]">
        <div className="flex items-center gap-2 min-w-0">
          <Scales weight="bold" className={`size-4 shrink-0 ${clean ? "text-emerald-500" : "text-amber-500"}`} />
          <span className="text-[13px] font-semibold truncate">Litigation</span>
        </div>
        <span className={`inline-flex items-center gap-1 text-[11px] font-semibold ${clean ? "text-emerald-700" : "text-amber-700"}`}>
          {clean
            ? <><SealCheck weight="fill" className="size-3" /> Clean</>
            : <><Warning weight="fill" className="size-3" /> On record</>}
        </span>
      </div>
      <div className="px-5 py-3 text-[12px] text-muted-foreground leading-snug">
        {totalCases} case{totalCases === 1 ? "" : "s"}
        {criminal > 0 ? `, ${criminal} criminal` : ""}
        {firs > 0 ? `, ${firs} FIR${firs === 1 ? "" : "s"}` : ""}
      </div>
    </div>
  )
}

// The card interior — gauge header + source rows + footer. Shared by rail card and sheet.
export function RailBody({ rail }: { rail: Rail | null }) {
  const score = rail?.safety_score ?? null
  const band = rail?.band
  const assessed = rail?.assessed_count ?? 0
  const total = rail?.total_count ?? 0
  // The score is PROVISIONAL until enough sources have returned — early on (e.g. only KYC in
  // at Step 1) a "100 / Low Risk" is not a verdict, just what's been checked so far. Below the
  // threshold we mute the band to "Provisional" so it isn't read as final. (threshold: ~half.)
  const provisional = total > 0 && assessed < Math.ceil(total / 2)
  const tone = provisional ? "idle" : railTone(band)
  const rows = railRows(rail)
  return (
    <>
      <div className="p-5 border-b bg-[#fbfaf8]">
        <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-muted-foreground font-semibold">
          <Pulse weight="bold" className="size-3.5 text-primary" /> Agent read
        </div>
        <div className="mt-3 flex items-center gap-4">
          <Gauge value={score} tone={tone} />
          <div>
            <div className="text-xs text-muted-foreground">Safety Score</div>
            <div className={`text-sm font-bold ${SEV[tone].text}`}>
              {provisional ? "Provisional" : (band || "accumulating")}
            </div>
            <div className="text-[11px] text-muted-foreground mt-0.5">
              {total > 0 ? `${assessed} of ${total} sources in — updates as more return` : "updates as sources return"}
            </div>
          </div>
        </div>
      </div>

      <ul className="divide-y">
        {rows.length === 0 && (
          <li className="px-5 py-4 text-[12px] text-muted-foreground">
            Nothing to check on this step yet — answer the questions and the agent updates here.
          </li>
        )}
        {rows.map((g, i) => {
          const s = SEV[g.severity]
          return (
            <li key={g.key} className="px-5 py-3 animate-fade-up" style={{ animationDelay: `${i * 50}ms` }}>
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2 min-w-0">
                  <span className={`size-2 rounded-full shrink-0 ${s.dot}`} />
                  <span className={`text-[13px] font-semibold truncate ${g.severity === "idle" ? "text-muted-foreground" : ""}`}>{g.label}</span>
                </div>
                <span className="text-[13px] font-bold tabular-nums text-muted-foreground shrink-0">
                  {g.severity === "idle" || g.gate ? "—" : Math.round(g.sub_score)}
                </span>
              </div>
              {/* One flag per line (scorer joins reasons with "; ") — a wall of text reads as noise. */}
              {g.severity === "idle" || !g.why?.includes("; ") ? (
                <p className="mt-1 text-[12px] text-muted-foreground leading-snug pl-4">{g.why}</p>
              ) : (
                <ul className="mt-1 pl-4 space-y-0.5">
                  {g.why.split("; ").map((line, j) => (
                    <li key={j} className="flex gap-1.5 text-[12px] text-muted-foreground leading-snug">
                      <span className={`mt-[3px] size-1 rounded-full shrink-0 ${s.dot}`} />
                      <span>{line}</span>
                    </li>
                  ))}
                </ul>
              )}
              {g.context && g.context.length > 0 && (
                <dl className="mt-2 pl-4 space-y-1">
                  {g.context.map((c) => (
                    <div key={c.label} className="flex items-baseline justify-between gap-3">
                      <dt className="text-[11px] text-muted-foreground">{c.label}</dt>
                      <dd className={`text-[11px] font-medium tabular-nums text-right ${c.value ? "text-foreground" : "text-muted-foreground/60"}`}>
                        {c.value || "—"}
                      </dd>
                    </div>
                  ))}
                </dl>
              )}
            </li>
          )
        })}
      </ul>

      <div className="px-5 py-2.5 border-t bg-[#fbfaf8] flex items-center justify-between text-[11px] text-muted-foreground">
        <span>{rows.length} sources</span>
        <span className="flex items-center gap-1.5"><span className="size-1.5 rounded-full bg-emerald-500 animate-pulse" /> live</span>
      </div>
    </>
  )
}

// Desktop / tablet rail column. Shown lg+ (desktop) and md (tablet) via the caller's grid.
export function AgentRail({ rail, snap, className = "" }: { rail: Rail | null; snap?: AppSnapshot | null; className?: string }) {
  return (
    <aside className={`shrink-0 self-start sticky top-14 space-y-4 ${className}`} aria-label="What the agent sees">
      <div className="rounded-2xl border bg-white shadow-[0_1px_2px_rgba(0,0,0,0.04)] overflow-hidden">
        <RailBody rail={rail} />
      </div>
      <LitigationCard snap={snap ?? null} />
    </aside>
  )
}
