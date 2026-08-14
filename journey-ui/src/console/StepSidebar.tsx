import { Check } from "@phosphor-icons/react"

// Desktop LEFT step nav. TWO stacked cards:
//   Card 1 (StepSidebar): header "Application progress" + "N of 7" meter, then the 7 steps
//           (sub-steps reveal as dots ONLY under the active step, mirroring the strip).
//   Card 2 (AppCard): the Application context card, a SEPARATE card below the nav.
const STEPS = ["Identity & KYC", "Product & Cover", "Financial", "Health", "Decision", "Nominee", "Payment"]
const TOTAL = 7

export function StepSidebar({
  active, appId, startedAt, maxReached = active, onJump,
}: {
  active: number; appId?: string; startedAt?: string
  maxReached?: number; onJump?: (n: number) => void
}) {
  const doneCount = active - 1
  const pct = Math.round((doneCount / TOTAL) * 100)

  return (
    <nav className="hidden lg:block w-80 xl:w-96 shrink-0 p-5 self-start sticky top-14 space-y-4" aria-label="Application steps">
      {/* Card 1 — progress + steps */}
      <div className="rounded-2xl elev-card p-4">
        <div className="px-1 pb-4 mb-3 border-b">
          <div className="text-[11px] uppercase tracking-[0.08em] text-muted-foreground font-semibold">Application progress</div>
          <div className="mt-2 flex items-center gap-2.5">
            <div className="flex-1 h-1.5 rounded-full bg-muted overflow-hidden">
              <div className="h-full rounded-full bg-primary transition-[width] duration-500 ease-out" style={{ width: `${pct}%` }} />
            </div>
            <span className="text-[11px] font-semibold text-muted-foreground tabular-nums whitespace-nowrap">{doneCount} of {TOTAL}</span>
          </div>
        </div>

        <ol>
          {STEPS.map((label, i) => {
            const n = i + 1
            const done = n < active
            const isActive = n === active
            const reachable = n <= maxReached && !!onJump
            return (
              <li key={label} className="relative flex gap-3 pb-5 last:pb-0">
                {n < STEPS.length && (
                  <span className={`absolute left-[13px] top-7 h-[calc(100%-16px)] w-0.5 ${done ? "bg-primary" : "bg-border"}`} />
                )}
                <button type="button" disabled={!reachable} onClick={() => reachable && onJump!(n)}
                  className={`relative flex gap-3 w-full text-left ${reachable ? "cursor-pointer group" : "cursor-default"}`}>
                  <span className={`relative z-10 grid place-items-center size-7 rounded-full text-[12px] font-bold shrink-0
                    ${done || isActive ? "bg-primary text-primary-foreground" : "border-2 border-border text-muted-foreground bg-white"}`}>
                    {done ? <Check weight="bold" className="size-4" /> : n}
                  </span>
                  <div className="min-w-0 pt-0.5 flex-1">
                    <div className="text-[11px] text-muted-foreground">Step {n}/{TOTAL}</div>
                    <div className={`text-[13.5px] leading-tight ${isActive ? "font-semibold text-primary" : done ? "font-medium text-foreground group-hover:text-primary" : "text-muted-foreground"}`}>
                      {label}
                    </div>
                  </div>
                </button>
              </li>
            )
          })}
        </ol>
      </div>

      {/* Card 2 — application context (SEPARATE card) */}
      <div className="rounded-2xl elev-card p-4">
        <div className="text-[10px] uppercase tracking-[0.08em] text-muted-foreground/80 font-semibold mb-2.5">Application</div>
        <div className="space-y-2">
          <Row k="ID" v={appId || "—"} mono />
          <Row k="Started" v={startedAt || "Today"} />
        </div>
      </div>
    </nav>
  )
}

function Row({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className="text-[12px] text-muted-foreground">{k}</span>
      <span className={`text-[12px] font-semibold text-foreground truncate ${mono ? "font-mono" : ""}`}>{v}</span>
    </div>
  )
}
