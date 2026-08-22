import { useEffect, useRef, useState } from "react"
import { ShieldCheck, Camera, CheckCircle, XCircle, Spinner } from "@phosphor-icons/react"

// The page a phone opens after scanning the QR / tapping the link shown on the agent's
// desktop (docs/vendor_apis.md PART B). Session-less — no login, keyed only by the
// {token} in the URL. Flow: instructions -> tap "Start Scan" -> /begin (device-gates,
// calls NuralX) -> opens the real NuralX scan_url in a NEW TAB -> this tab keeps polling
// and flips to "done" the moment the webhook lands, with Retry on ERROR/TIMEOUT/EXPIRED.
//
// Uses the SAME design tokens as the rest of journey-ui (bg-background/text-foreground/
// bg-primary/border-border/stat-ok/stat-bad — see index.css) — NOT the design-tokens.css
// system (that belongs to the separate server-rendered journey/templates pages).
type Phase = "loading" | "instructions" | "starting" | "waiting" | "done" | "error" | "expired" | "not_found"

export function FaceScanMobile({ token }: { token: string }) {
  const [phase, setPhase] = useState<Phase>("loading")
  const pollRef = useRef<number | null>(null)

  async function checkStatus() {
    try {
      const r = await fetch(`/api/journey/face-scan/${token}/status`)
      const d = await r.json()
      if (!d.success) { setPhase("not_found"); return }
      if (d.status === "COMPLETED") { setPhase("done"); stopPolling(); return }
      if (d.status === "ERROR" || d.status === "TIMEOUT") { setPhase("error"); stopPolling(); return }
      if (d.status === "EXPIRED") { setPhase("expired"); stopPolling(); return }
      if (d.status === "IN_PROGRESS") { setPhase("waiting"); return }
      setPhase("instructions")
    } catch { /* transient — keep polling */ }
  }

  function stopPolling() {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
  }

  useEffect(() => {
    checkStatus()
    return stopPolling
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function startScan() {
    setPhase("starting")
    try {
      const r = await fetch(`/api/journey/face-scan/${token}/begin`, { method: "POST" })
      const d = await r.json()
      if (!d.success) {
        setPhase(d.message === "expired" ? "expired" : "error")
        return
      }
      if (d.scan_url) window.open(d.scan_url, "_blank")
      setPhase("waiting")
      pollRef.current = window.setInterval(checkStatus, 3000)
    } catch {
      setPhase("error")
    }
  }

  return (
    <div className="min-h-[100dvh] flex flex-col bg-background">
      <header className="flex items-center gap-2.5 px-5 py-4 border-b border-border">
        <span className="grid place-items-center size-8 rounded-lg bg-primary text-primary-foreground">
          <ShieldCheck weight="fill" className="size-4" />
        </span>
        <span className="text-[15px] font-extrabold tracking-tight">Acme Life Insurance</span>
      </header>

      <main className="flex-1 grid place-items-center p-6">
        <div className="w-full max-w-[380px] rounded-xl border border-border bg-white p-6 text-center">
          {phase === "loading" && <StatusBlock icon={<Spinner weight="bold" className="animate-spin" size={28} />} title="Loading…" />}

          {phase === "instructions" && (
            <>
              <span className="mx-auto mb-4 grid place-items-center size-11 rounded-full bg-primary/10 text-primary">
                <Camera weight="fill" size={20} />
              </span>
              <h1 className="text-[18px] font-bold tracking-tight">Face &amp; vitals scan</h1>
              <p className="mt-2 text-[13px] text-muted-foreground leading-relaxed">
                For an accurate reading, wait 30–60 minutes after exercise, caffeine, or
                smoking. Hold your phone at eye level, close to your face, with good front
                lighting and no shadows. Then stay still for about 60 seconds.
              </p>
              <button onClick={startScan}
                className="mt-5 w-full flex items-center justify-center gap-2 h-11 rounded-md bg-primary text-primary-foreground text-[14px] font-medium hover:bg-primary/90 transition-colors">
                <Camera weight="bold" size={17} /> Start scan
              </button>
            </>
          )}

          {phase === "starting" && <StatusBlock icon={<Spinner weight="bold" className="animate-spin" size={28} />} title="Starting the scan…" />}

          {phase === "waiting" && (
            <StatusBlock icon={<Spinner weight="bold" className="animate-spin" size={28} />} title="Analyzing your scan…"
              body="This takes about a minute. Keep this tab open — the result appears here automatically." />
          )}

          {phase === "done" && (
            <StatusBlock icon={<CheckCircle weight="fill" size={30} />} tone="stat-ok" title="Scan complete"
              body="You can close this window and return to the agent." />
          )}

          {(phase === "error" || phase === "expired") && (
            <>
              <StatusBlock icon={<XCircle weight="fill" size={30} />} tone="stat-bad"
                title={phase === "expired" ? "This link has expired" : "Scan failed"}
                body={phase === "expired"
                  ? "Ask the agent to show a new QR code."
                  : "Something went wrong during the scan."} />
              {phase === "error" && (
                <button onClick={startScan}
                  className="mt-5 w-full flex items-center justify-center gap-2 h-11 rounded-md border border-border text-[14px] font-medium hover:border-primary transition-colors">
                  Retry
                </button>
              )}
            </>
          )}

          {phase === "not_found" && (
            <StatusBlock icon={<XCircle weight="fill" size={30} />} tone="stat-bad" title="Link not found"
              body="Ask the agent to show a new QR code." />
          )}
        </div>
      </main>
    </div>
  )
}

function StatusBlock({ icon, title, body, tone }: {
  icon: React.ReactNode; title: string; body?: string; tone?: "stat-ok" | "stat-bad"
}) {
  return (
    <div className="flex flex-col items-center gap-3">
      <span className={`grid place-items-center size-11 rounded-full ${tone ? `border ${tone}` : "text-muted-foreground"}`}>{icon}</span>
      <h1 className="text-[17px] font-bold tracking-tight">{title}</h1>
      {body && <p className="text-[13px] text-muted-foreground leading-relaxed">{body}</p>}
    </div>
  )
}
