import { useState } from "react"
import type { AppSnapshot } from "./useJourney"
import type { PremiumSummary } from "./ProductStep"
import { ShieldCheck, CheckCircle, Lock, Copy, Check, Spinner } from "@phosphor-icons/react"

// STEP 7 — Payment -> issuance -> free-look (TERM LIFE, single life).
// REAL Razorpay (test mode) via hosted Checkout.js: "Pay" POSTs /api/journey/payment/order
// (server creates a real order), opens Razorpay's own checkout modal, and on the success
// handler POSTs /api/journey/payment/verify — the server HMAC-verifies the signature BEFORE
// issuing (never trusts the client's success claim) and returns the policy_number. Demo mode
// (no appId) falls back to the mocked /payment endpoint. On success the center swaps to the
// policy-issued confirmation with §64VB + free-look copy. §64VB: cover starts on payment success.

const inr = (n: number) => "₹" + n.toLocaleString("en-IN")

// Load Razorpay's hosted Checkout.js once (CDN, per their integration guide).
const RZP_SRC = "https://checkout.razorpay.com/v1/checkout.js"
function loadRazorpay(): Promise<boolean> {
  return new Promise((resolve) => {
    if ((window as any).Razorpay) return resolve(true)
    const s = document.createElement("script")
    s.src = RZP_SRC
    s.onload = () => resolve(true)
    s.onerror = () => resolve(false)
    document.body.appendChild(s)
  })
}

type Mode = "upi" | "card" | "netbanking" | "razorpay"

// Amount due: prefer the live Step-2 premium; fall back to the persisted bundle value
// (?start=7 / refresh, where Step 2 was never walked this session).
function amountDue(premium: PremiumSummary | null, snap: AppSnapshot): number | null {
  if (premium) return premium.total_annual
  const p = snap.product?.premium
  return typeof p === "number" && p > 0 ? p : null
}

export function PaymentStep({
  appId, snap, premium,
}: {
  appId: number | null; snap: AppSnapshot; premium: PremiumSummary | null
}) {
  const amount = amountDue(premium, snap)
  // If the app is already issued (revisit), show the confirmation straight away.
  const already = snap.status === "issued" ? (snap.product?.payment_mode as Mode) || "upi" : null
  const [paying, setPaying] = useState(false)
  const [issued, setIssued] = useState<{ policy_no: string; mode: Mode } | null>(null)
  const [err, setErr] = useState("")

  // Real Razorpay checkout: create order -> open hosted modal -> verify signature -> issue.
  async function pay() {
    setErr("")
    // Demo mode (no logged-in app) keeps the old mocked success so a walk-through still works.
    if (appId == null) { setIssued({ policy_no: "POL-DEMO0001", mode: "upi" }); return }
    setPaying(true)
    try {
      const ok = await loadRazorpay()
      if (!ok) { setErr("Could not load the payment gateway. Check your connection and retry."); setPaying(false); return }
      const ord = await fetch("/api/journey/payment/order", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ app_id: appId }),
      }).then((r) => r.json())
      if (!ord.success) { setErr(ord.message || "Could not start payment. Try again."); setPaying(false); return }

      const rzp = new (window as any).Razorpay({
        key: ord.key_id,
        order_id: ord.order_id,
        amount: ord.amount,
        currency: ord.currency,
        name: "Acme Life Insurance",
        description: "Term life premium",
        prefill: { name: snap.applicant.name || "" },
        theme: { color: "#0f766e" },
        modal: { ondismiss: () => setPaying(false) },
        handler: async (resp: any) => {
          try {
            const v = await fetch("/api/journey/payment/verify", {
              method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ app_id: appId, ...resp }),
            }).then((r) => r.json())
            if (!v.success) { setErr(v.message || "Payment verification failed."); setPaying(false); return }
            setIssued({ policy_no: v.policy_number, mode: "razorpay" as Mode })
          } catch { setErr("Could not verify the payment. If money was debited, contact support."); setPaying(false) }
        },
      })
      rzp.on("payment.failed", (e: any) => { setErr(e?.error?.description || "Payment failed. Try again."); setPaying(false) })
      rzp.open()
    } catch { setErr("Could not reach the payment service. Try again."); setPaying(false) }
  }

  if (issued) return <Issued policyNo={issued.policy_no} mode={issued.mode} name={snap.applicant.name} />
  if (already) return <RevisitIssued snap={snap} mode={already} />

  return (
    <div className="space-y-8">
      <section>
        <RegionHead title="Premium payment"
          hint="Complete the premium payment to issue the policy. Risk cover starts only once payment succeeds (Section 64VB)." />

        {/* Amount-due card */}
        <div className="rounded-2xl elev-card p-5 sm:p-6">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div>
              <div className="text-[10px] uppercase tracking-[0.08em] font-semibold text-muted-foreground">Amount due · annual premium</div>
              <div className="text-[34px] font-bold tracking-tight tabular-nums leading-none mt-1.5">
                {amount != null ? inr(amount) : "—"}
                <span className="text-[15px] font-medium text-muted-foreground"> /yr</span>
              </div>
              <div className="text-[12px] text-muted-foreground mt-2">
                {snap.product?.sum_assured ? `${inrShort(snap.product.sum_assured)} cover` : "Term Life"}
                {snap.product?.tenure_years ? `  ·  ${snap.product.tenure_years} yr term` : ""}
                {"  ·  "}{snap.applicant.name || "Applicant"}
              </div>
            </div>
            <button
              onClick={pay}
              disabled={amount == null || paying}
              className="inline-flex items-center gap-2 rounded-md bg-primary text-primary-foreground text-[15px] font-semibold px-5 h-12 hover:bg-primary/90 transition-colors disabled:opacity-60">
              {paying
                ? <><Spinner weight="bold" className="size-4 animate-spin" /> Processing…</>
                : <><Lock weight="fill" className="size-4" /> Pay &amp; issue policy</>}
            </button>
          </div>
        </div>

        {err && <p className="mt-3 text-[13px] text-red-600 font-medium">{err}</p>}

        <p className="mt-4 flex items-start gap-2 text-[11.5px] text-muted-foreground leading-snug">
          <ShieldCheck weight="fill" className="size-4 shrink-0 mt-px text-primary/70" />
          Razorpay test mode — use a test card (e.g. 4111 1111 1111 1111, any future expiry &amp; CVV)
          or test UPI <span className="font-mono">success@razorpay</span>. No real money moves.
        </p>
      </section>
    </div>
  )
}

// ---- Policy-issued confirmation (inline, swaps the center) -----------------------------
const MODE_LABEL: Record<Mode, string> = {
  upi: "UPI", card: "Card", netbanking: "Netbanking", razorpay: "Razorpay",
}
function Issued({ policyNo, mode, name }: { policyNo: string; mode: Mode; name?: string }) {
  const [copied, setCopied] = useState(false)
  const copy = () => { navigator.clipboard?.writeText(policyNo).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1600) }) }
  const modeLabel = MODE_LABEL[mode] || mode

  return (
    <div className="space-y-6 animate-fade-up">
      {/* success banner */}
      <section className="rounded-2xl border border-emerald-200 bg-emerald-50 p-6 sm:p-7 text-center">
        <span className="inline-grid place-items-center size-14 rounded-2xl bg-emerald-100 text-emerald-600 mb-3">
          <CheckCircle weight="fill" className="size-8" />
        </span>
        <div className="font-mono text-[11px] uppercase tracking-wider text-emerald-700/80">Policy issued</div>
        <h2 className="text-[24px] font-bold tracking-tight text-emerald-900 mt-1">
          {name ? `${name.split(" ")[0]}, you're covered` : "You're covered"}
        </h2>
        <p className="text-[13px] text-emerald-800/90 mt-1.5 max-w-md mx-auto">
          Premium received via {modeLabel}. Your term life cover is now in force.
        </p>

        <div className="mt-5 inline-flex items-center gap-2.5 rounded-xl bg-white border border-emerald-200 px-4 py-2.5">
          <div className="text-left">
            <div className="text-[10px] uppercase tracking-wide text-muted-foreground">Policy number</div>
            <div className="font-mono text-[16px] font-bold tracking-tight">{policyNo}</div>
          </div>
          <button onClick={copy} title="Copy policy number"
            className="grid place-items-center size-8 rounded-lg border border-border bg-secondary/50 hover:border-muted-foreground/30 transition-colors">
            {copied ? <Check weight="bold" className="size-4 text-emerald-600" /> : <Copy weight="regular" className="size-4 text-muted-foreground" />}
          </button>
        </div>
      </section>

      <RegulatoryCopy />
    </div>
  )
}

// Revisiting Step 7 after issuance (no fresh policy number in state) — same confirmation,
// number read from the bundle if present.
function RevisitIssued({ snap, mode }: { snap: AppSnapshot; mode: Mode }) {
  // policy_number lives under _journey in the bundle; the snapshot doesn't echo it, so on a
  // pure revisit we show the issued state without re-fetching (the number was shown at issue).
  return <Issued policyNo="issued" mode={mode} name={snap.applicant.name} />
}

// §64VB + 30-day free-look (fuller regulatory detail, per the chosen wording).
function RegulatoryCopy() {
  return (
    <section className="grid sm:grid-cols-2 gap-4">
      <div className="rounded-xl border border-border bg-white p-4">
        <div className="flex items-center gap-2 mb-2">
          <span className="inline-flex items-center rounded-md bg-secondary text-foreground px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide">§64VB</span>
          <h3 className="text-[13px] font-semibold">Risk cover in force</h3>
        </div>
        <p className="text-[12.5px] text-muted-foreground leading-relaxed">
          Under Section 64VB of the Insurance Act, risk cover begins only once the premium is
          received. Your payment has been received, so cover is now active from today.
        </p>
      </div>
      <div className="rounded-xl border border-border bg-white p-4">
        <div className="flex items-center gap-2 mb-2">
          <span className="inline-flex items-center rounded-md bg-secondary text-foreground px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide">Free-look</span>
          <h3 className="text-[13px] font-semibold">30 days to review</h3>
        </div>
        <p className="text-[12.5px] text-muted-foreground leading-relaxed">
          You have 30 days from receipt of the policy to review the terms and cancel. On
          cancellation, the premium is refunded less the proportionate risk premium for the
          period on cover, stamp duty, and any medical-examination cost (IRDAI Protection of
          Policyholders' Interests Regulations, 2024).
        </p>
      </div>
    </section>
  )
}

// ---- shared bits (match the step idiom) ------------------------------------------------
const inrShort = (n: number) =>
  n >= 10_000_000 ? `₹${(n / 10_000_000).toFixed(n % 10_000_000 ? 1 : 0)} Cr`
  : n >= 100_000 ? `₹${(n / 100_000).toFixed(n % 100_000 ? 1 : 0)} L`
  : "₹" + n.toLocaleString("en-IN")

function RegionHead({ title, hint }: { title: string; hint: string }) {
  return (
    <div className="mb-3">
      <h2 className="text-[15px] font-bold tracking-tight">{title}</h2>
      <p className="text-[12px] text-muted-foreground mt-0.5">{hint}</p>
    </div>
  )
}
