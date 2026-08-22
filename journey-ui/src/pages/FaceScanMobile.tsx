import { useEffect, useRef, useState } from "react"
import { ShieldCheck, Camera, CheckCircle2, XCircle, Loader2 } from "lucide-react"

// The page a phone opens after scanning the QR / tapping the link shown on the agent's
// desktop (docs/vendor_apis.md PART B). Session-less — no login, keyed only by the
// {token} in the URL. Flow: instructions -> tap "Start Scan" -> /begin (device-gates,
// calls NuralX) -> redirect to the real NuralX scan_url -> back here to poll our own
// status until COMPLETED, with Retry on ERROR/TIMEOUT/EXPIRED.
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
    <div className="min-h-screen grid place-items-center bg-[var(--color-surface-2)] p-6">
      <div className="w-full max-w-[380px] rounded-2xl bg-surface border border-[var(--color-line)] p-8 text-center">
        <span className="mx-auto mb-5 grid place-items-center w-11 h-11 rounded-full bg-brand/10 text-brand">
          <ShieldCheck size={22} strokeWidth={1.75} />
        </span>

        {phase === "loading" && <StatusBlock icon={<Loader2 className="animate-spin" size={28} />} title="Loading…" />}

        {phase === "instructions" && (
          <>
            <h1 className="display text-[22px] text-ink leading-tight">Face &amp; vitals scan</h1>
            <p className="mt-2 text-[13.5px] text-ink-2 leading-relaxed">
              Hold your phone at arm's length, look at the camera, and stay still for about
              60 seconds. We'll read your liveness and a few wellness vitals — this is a
              screening estimate, not a medical diagnosis.
            </p>
            <button onClick={startScan}
              className="mt-6 w-full flex items-center justify-center gap-2 py-3 rounded-[var(--radius-sm)] bg-brand text-white text-[14.5px] font-medium hover:bg-[var(--color-brand-hover)] transition-colors">
              <Camera size={17} /> Start scan
            </button>
          </>
        )}

        {phase === "starting" && <StatusBlock icon={<Loader2 className="animate-spin" size={28} />} title="Starting the scan…" />}

        {phase === "waiting" && (
          <StatusBlock icon={<Loader2 className="animate-spin" size={28} />} title="Analyzing your scan…"
            body="This takes about a minute. Keep this tab open — the result appears here automatically." />
        )}

        {phase === "done" && (
          <StatusBlock icon={<CheckCircle2 size={30} />} tone="stat-ok" title="Scan complete"
            body="You can close this window and return to the agent." />
        )}

        {(phase === "error" || phase === "expired") && (
          <>
            <StatusBlock icon={<XCircle size={30} />} tone="stat-bad"
              title={phase === "expired" ? "This link has expired" : "Scan failed"}
              body={phase === "expired"
                ? "Ask the agent to show a new QR code."
                : "Something went wrong during the scan."} />
            {phase === "error" && (
              <button onClick={startScan}
                className="mt-5 w-full flex items-center justify-center gap-2 py-3 rounded-[var(--radius-sm)] border border-[var(--color-line-2)] text-[14.5px] font-medium hover:border-brand transition-colors">
                Retry
              </button>
            )}
          </>
        )}

        {phase === "not_found" && (
          <StatusBlock icon={<XCircle size={30} />} tone="stat-bad" title="Link not found"
            body="Ask the agent to show a new QR code." />
        )}
      </div>
    </div>
  )
}

function StatusBlock({ icon, title, body, tone }: {
  icon: React.ReactNode; title: string; body?: string; tone?: "stat-ok" | "stat-bad"
}) {
  return (
    <div className="flex flex-col items-center gap-3">
      <span className={`grid place-items-center size-11 rounded-full ${tone ? `border ${tone}` : "text-ink-2"}`}>{icon}</span>
      <h1 className="display text-[19px] text-ink leading-tight">{title}</h1>
      {body && <p className="text-[13.5px] text-ink-2 leading-relaxed">{body}</p>}
    </div>
  )
}
