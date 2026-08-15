import { useEffect, useState } from "react"
import { ShieldCheck, Check, CircleNotch } from "@phosphor-icons/react"
import { ConsoleShell } from "./Console"

// Loading escalation:
//  0-2s  -> the real Console SHELL as a skeleton (anticipates the exact layout, no shift)
//  2s+   -> the shell stays, with a determinate step-progress card OVERLAID on top
// The wait is normally ~1.5s (skeleton only); the overlay only appears if the backend is slow.
const STEPS = ["Mobile number verified", "Fetching identity profile", "Assembling your application"]

function StepProgressCard() {
  const [done, setDone] = useState(0)
  useEffect(() => {
    const t1 = window.setTimeout(() => setDone(1), 600)
    const t2 = window.setTimeout(() => setDone(2), 1400)
    return () => { window.clearTimeout(t1); window.clearTimeout(t2) }
  }, [])
  const pct = Math.min(100, ((done + 0.5) / STEPS.length) * 100)
  return (
    <div className="w-full max-w-sm rounded-2xl border bg-white p-8 shadow-[0_8px_40px_rgba(0,0,0,0.12)]">
      <div className="flex items-center gap-3">
        <span className="grid place-items-center size-11 rounded-xl bg-primary text-primary-foreground"><ShieldCheck weight="fill" className="size-6" /></span>
        <div>
          <div className="text-[15px] font-bold tracking-tight">Acme Life Insurance</div>
          <div className="text-xs text-muted-foreground">This is taking a moment…</div>
        </div>
      </div>
      <div className="mt-6 h-1.5 rounded-full overflow-hidden bg-muted">
        <div className="h-full rounded-full bg-primary transition-[width] duration-700 ease-out" style={{ width: `${pct}%` }} />
      </div>
      <ul className="mt-6 space-y-3.5">
        {STEPS.map((s, i) => {
          const isDone = i < done, isActive = i === done
          return (
            <li key={s} className={`flex items-center gap-3 text-sm ${isDone || isActive ? "" : "opacity-45"}`}>
              <span className={`grid place-items-center size-5 rounded-full shrink-0 ${isDone ? "bg-primary text-primary-foreground" : isActive ? "" : "border border-muted-foreground/30"}`}>
                {isDone ? <Check weight="bold" className="size-3" /> : isActive ? <CircleNotch weight="bold" className="size-5 animate-spin text-primary" /> : null}
              </span>
              <span className={isActive ? "font-medium" : ""}>{s}</span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

export function TransitionLoader({ variant }: { variant: "teal" | "light" }) {
  void variant
  const [longWait, setLongWait] = useState(false)
  useEffect(() => {
    const t = window.setTimeout(() => setLongWait(true), 2000)  // overlay the progress card at 2s
    return () => window.clearTimeout(t)
  }, [])
  return (
    <div className="relative">
      <ConsoleShell loading />
      {longWait && (
        <div className="fixed inset-0 z-40 grid place-items-center bg-background/60 backdrop-blur-[2px]">
          <StepProgressCard />
        </div>
      )}
    </div>
  )
}
