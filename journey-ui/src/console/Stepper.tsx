import { Check } from "@phosphor-icons/react"

// Main 7-step stepper. Active step expands into a pill showing its sub-steps inline.
const STEPS = ["Identity", "Product", "Financial", "Health", "Decision", "Nominee", "Payment"]

export function Stepper({ active, sub, subActive, teal = false }:
  { active: number; sub: string[]; subActive: number; teal?: boolean }) {
  const c = teal ? {
    pill: "bg-primary-foreground/10 ring-1 ring-primary-foreground/20",
    done: "bg-primary-foreground text-primary",
    activeC: "bg-primary-foreground/20 text-primary-foreground ring-2 ring-primary-foreground",
    upC: "text-primary-foreground/50 ring-1 ring-primary-foreground/25",
    activeL: "text-primary-foreground", doneL: "text-primary-foreground/80", upL: "text-primary-foreground/50",
    line: "bg-primary-foreground/25", lineDone: "bg-primary-foreground/70",
    subOn: "text-primary-foreground", subOff: "text-primary-foreground/55",
    dotOn: "bg-primary-foreground", dotOff: "bg-primary-foreground/35", div: "bg-primary-foreground/25",
  } : {
    pill: "bg-primary/8 ring-1 ring-primary/15",
    done: "bg-primary text-primary-foreground",
    activeC: "bg-primary/10 text-primary ring-2 ring-primary",
    upC: "text-muted-foreground ring-1 ring-border",
    activeL: "text-foreground", doneL: "text-foreground/75", upL: "text-muted-foreground",
    line: "bg-border", lineDone: "bg-primary/60",
    subOn: "text-primary", subOff: "text-muted-foreground",
    dotOn: "bg-primary", dotOff: "bg-muted-foreground/30", div: "bg-primary/20",
  }

  return (
    <nav className="px-6 lg:px-8 pb-4 pt-4 overflow-x-auto" aria-label="Onboarding steps">
      <ol className="flex items-center min-w-max">
        {STEPS.map((label, i) => {
          const n = i + 1
          const done = n < active
          const isActive = n === active
          return (
            <li key={label} className="flex items-center">
              <div className={`flex items-center gap-2.5 rounded-full py-1.5 ${isActive ? `pl-2 pr-3.5 ${c.pill}` : "px-1"}`}>
                <span className={`grid place-items-center size-7 rounded-full text-[12px] font-bold shrink-0
                  ${done ? c.done : isActive ? c.activeC : c.upC}`}>
                  {done ? <Check weight="bold" className="size-4" /> : n}
                </span>
                <span className={`text-[13.5px] font-medium ${isActive ? c.activeL : done ? c.doneL : c.upL}`}>{label}</span>
                {isActive && (
                  <span className={`flex items-center gap-2 ml-1.5 pl-3 border-l ${c.div}`}>
                    {sub.map((s, si) => (
                      <span key={s} className={`text-[12px] flex items-center gap-1.5 ${si === subActive ? `font-semibold ${c.subOn}` : c.subOff}`}>
                        <span className={`size-1.5 rounded-full ${si <= subActive ? c.dotOn : c.dotOff}`} />
                        {s}
                      </span>
                    ))}
                  </span>
                )}
              </div>
              {n < STEPS.length && <span className={`h-0.5 w-6 rounded-full mx-1 shrink-0 ${done ? c.lineDone : c.line}`} />}
            </li>
          )
        })}
      </ol>
    </nav>
  )
}
