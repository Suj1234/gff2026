import { useAppSnapshot, useRail } from "./useJourney"
import { StepSidebar } from "./StepSidebar"
import { TabletStepBar } from "./TabletStepBar"
import { SubStepStrip } from "./SubStepStrip"
import { MobileStepHeader } from "./MobileStepHeader"
import { IdentityCenter } from "./IdentityCenter"
import { AgentRail } from "./AgentRail"
import { RailSheet } from "./RailSheet"
import { ShieldCheck, Question } from "@phosphor-icons/react"

// Step-1 console. Three responsive tiers:
//   desktop (lg+)  : left step sidebar | content card | right rail card
//   tablet (md..lg): top step bar; content card | right rail card (side by side)
//   mobile (<md)   : mobile step header; single content card; rail as bottom sheet
// A shared Continue footer lives INSIDE the content card so it renders on every tier.
type Variant = "teal" | "light"
const SUB_STEPS = ["Profile", "Aadhaar", "Consent"]
const STEP_NO = 1
const STEP_TITLE = "Identity & KYC"
const STEP_DESC = "Confirm the applicant's identity, add an email, and verify Aadhaar."

export function Console({ appId, variant }: { appId: number | null; variant: Variant }) {
  const { snap } = useAppSnapshot(appId)
  const rail = useRail(appId, 1)
  void variant   // reserved: hero/console are now single premium look; kept for future theming

  if (!snap) {
    return <div className="min-h-[100dvh] grid place-items-center text-muted-foreground">Loading application…</div>
  }

  return (
    <div className="min-h-[100dvh] flex flex-col bg-[#f4f3f0]">
      {/* HEADER — full width, sticky */}
      <header className="flex items-center gap-3 px-6 lg:px-8 h-14 bg-white border-b shrink-0 sticky top-0 z-30">
        <span className="grid place-items-center size-7 rounded-lg bg-primary text-primary-foreground">
          <ShieldCheck weight="fill" className="size-4" />
        </span>
        <span className="text-[15px] font-extrabold tracking-tight">Acme Life Insurance</span>
        <span className="ml-auto flex items-center gap-3">
          {snap.seeded && <span className="rounded-full bg-amber-100 text-amber-700 px-2 py-0.5 text-[11px] font-semibold">demo data</span>}
          <span className="hidden sm:flex items-center gap-2 rounded-full px-2.5 py-1 bg-muted">
            <span className="grid place-items-center size-5 rounded-full bg-primary/15 text-primary text-[10px] font-bold">
              {(snap.applicant.name || "?").trim().charAt(0).toUpperCase()}
            </span>
            <span className="text-[12px] font-semibold max-w-[150px] truncate">{snap.applicant.name || "New applicant"}</span>
            <span className="font-mono text-[11px] text-muted-foreground">{snap.application_number}</span>
          </span>
          <button className="text-muted-foreground hover:text-foreground"><Question className="size-5" /></button>
        </span>
      </header>

      {/* MOBILE (<md) step header  &  TABLET (md..lg) top step bar */}
      <MobileStepHeader step={STEP_NO} title={STEP_TITLE} description={STEP_DESC} subSteps={SUB_STEPS} subActive={0} />
      <TabletStepBar active={STEP_NO} />

      {/* CANVAS */}
      <div className="flex-1 flex min-h-0">
        {/* desktop-only left step sidebar */}
        <StepSidebar active={STEP_NO} appId={snap.application_number} startedAt="Today" />

        {/* content + rail: single col on mobile, 2-col (form | rail) on tablet+ */}
        <div className="flex-1 min-w-0 grid grid-cols-1 md:grid-cols-[minmax(0,1fr)_340px] xl:grid-cols-[minmax(0,1fr)_400px] gap-0">
          <main className="min-w-0 p-4 sm:p-5 lg:pl-0 pb-24 md:pb-5">
            <div className="rounded-2xl border bg-white shadow-[0_1px_2px_rgba(0,0,0,0.04)] overflow-hidden">
              {/* content-card header: title + sub-step strip (desktop/tablet; mobile has its own header) */}
              <div className="hidden md:block px-6 lg:px-8 pt-5">
                <h1 className="text-xl font-bold tracking-tight">{STEP_TITLE}</h1>
                <p className="text-sm text-muted-foreground mt-0.5">{STEP_DESC}</p>
                <div className="mt-4 border-b"><SubStepStrip steps={SUB_STEPS} active={0} /></div>
              </div>
              <div className="px-4 sm:px-6 lg:px-8 py-6">
                <IdentityCenter snap={snap} />
              </div>
              {/* shared footer — renders on EVERY tier so mobile can advance too */}
              <div className="border-t px-4 sm:px-6 lg:px-8 py-3.5 flex items-center justify-between gap-3">
                <button className="text-sm text-muted-foreground hover:text-foreground disabled:opacity-40" disabled>‹ Back</button>
                <button className="rounded-md bg-primary text-primary-foreground text-sm font-medium px-5 h-10 hover:bg-primary/90 transition-colors">
                  Continue to Product ›
                </button>
              </div>
            </div>
          </main>

          {/* rail column: tablet + desktop (side by side). Hidden < md — mobile uses the sheet. */}
          <AgentRail rail={rail} className="hidden md:block" />
        </div>
      </div>

      {/* mobile-only collapsible bottom sheet */}
      <RailSheet rail={rail} />
    </div>
  )
}
