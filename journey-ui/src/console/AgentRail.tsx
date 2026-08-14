import { useEffect, useState } from "react"
import type { Rail, RailGroup } from "./useJourney"
import { Pulse } from "@phosphor-icons/react"

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
  return rail?.groups?.length
    ? rail.groups
    : ["Identity / KYC", "Occupation", "Fraud", "Litigation", "Contactability"].map((label) => ({
        key: label, label, sub_score: 0, severity: "idle" as const, why: "awaiting source",
      }))
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

// The card interior — gauge header + source rows + footer. Shared by rail card and sheet.
export function RailBody({ rail }: { rail: Rail | null }) {
  const score = rail?.safety_score ?? null
  const band = rail?.band
  const tone = railTone(band)
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
            <div className={`text-sm font-bold ${SEV[tone].text}`}>{band || "accumulating"}</div>
            <div className="text-[11px] text-muted-foreground mt-0.5">updates as sources return</div>
          </div>
        </div>
      </div>

      <ul className="divide-y">
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
                  {g.severity === "idle" ? "—" : Math.round(g.sub_score)}
                </span>
              </div>
              <p className="mt-1 text-[12px] text-muted-foreground leading-snug pl-4">{g.why}</p>
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
export function AgentRail({ rail, className = "" }: { rail: Rail | null; className?: string }) {
  return (
    <aside className={`shrink-0 self-start sticky top-14 ${className}`} aria-label="What the agent sees">
      <div className="rounded-2xl border bg-white shadow-[0_1px_2px_rgba(0,0,0,0.04)] overflow-hidden">
        <RailBody rail={rail} />
      </div>
    </aside>
  )
}
