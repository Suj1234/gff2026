import { useEffect, useState } from "react"
import { ShieldCheck, Check, CircleNotch } from "@phosphor-icons/react"

// Research-correct loading escalation:
//  0-3s   -> SKELETON of the incoming console (anticipate layout, feels fast)
//  3-10s  -> determinate STEP-PROGRESS (long wait needs meaningful feedback)
// The wait is normally ~1.5s (skeleton only); escalates only if the backend is slow.
const STEPS = ["Mobile number verified", "Fetching identity profile", "Assembling your application"]

const Bar = ({ w, h = "h-3" }: { w: string; h?: string }) =>
  <div className={`${h} ${w} rounded-md bg-black/[0.06] animate-pulse`} />

function Skeleton() {
  return (
    <div className="min-h-[100dvh] flex bg-[#f4f3f0]">
      <div className="hidden lg:block w-80 xl:w-96 shrink-0 p-5">
        <div className="rounded-2xl border bg-white p-4 space-y-5">
          {Array.from({ length: 7 }).map((_, i) => (
            <div key={i} className="flex gap-3">
              <div className="size-7 rounded-full bg-black/[0.06] animate-pulse shrink-0" />
              <div className="flex-1 space-y-1.5 pt-0.5"><Bar w="w-10" h="h-2" /><Bar w="w-28" /></div>
            </div>
          ))}
        </div>
      </div>
      <div className="flex-1 flex flex-col min-w-0">
        <div className="flex items-center gap-3 px-8 h-14 border-b bg-white">
          <span className="grid place-items-center size-7 rounded-lg bg-primary text-primary-foreground"><ShieldCheck weight="fill" className="size-4" /></span>
          <span className="text-[15px] font-extrabold tracking-tight">Acme Life Insurance</span>
          <span className="ml-auto text-xs text-muted-foreground animate-pulse">Preparing your application…</span>
        </div>
        <div className="grid lg:grid-cols-[minmax(0,1fr)_360px] xl:grid-cols-[minmax(0,1fr)_400px] flex-1">
          <div className="p-5">
            <div className="rounded-2xl border bg-white p-6 space-y-4">
              <Bar w="w-40" h="h-5" />
              <div className="rounded-xl bg-[#faf9f7] p-4 space-y-3">
                <Bar w="w-24" />
                <div className="grid grid-cols-3 gap-2.5">{Array.from({ length: 6 }).map((_, i) => <div key={i} className="h-14 rounded-lg bg-black/[0.04] animate-pulse" />)}</div>
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div className="h-32 rounded-xl bg-[#faf9f7] animate-pulse" />
                <div className="h-32 rounded-xl bg-[#faf9f7] animate-pulse" />
              </div>
            </div>
          </div>
          <div className="hidden lg:block p-5">
            <div className="rounded-2xl border bg-white p-5 space-y-4">
              <div className="size-[84px] rounded-full bg-black/[0.06] animate-pulse" />
              {Array.from({ length: 5 }).map((_, i) => <Bar key={i} w="w-full" h="h-8" />)}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function StepProgress() {
  const [done, setDone] = useState(0)
  useEffect(() => {
    const t1 = window.setTimeout(() => setDone(1), 600)
    const t2 = window.setTimeout(() => setDone(2), 1400)
    return () => { window.clearTimeout(t1); window.clearTimeout(t2) }
  }, [])
  const pct = Math.min(100, ((done + 0.5) / STEPS.length) * 100)
  return (
    <div className="min-h-[100dvh] grid place-items-center bg-[#f4f3f0]">
      <div className="w-full max-w-sm rounded-2xl border bg-white p-8 shadow-[0_8px_40px_rgba(0,0,0,0.06)]">
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
    </div>
  )
}

export function TransitionLoader({ variant }: { variant: "teal" | "light" }) {
  void variant
  const [longWait, setLongWait] = useState(false)
  useEffect(() => {
    const t = window.setTimeout(() => setLongWait(true), 3000)  // escalate skeleton -> progress at 3s
    return () => window.clearTimeout(t)
  }, [])
  return longWait ? <StepProgress /> : <Skeleton />
}
