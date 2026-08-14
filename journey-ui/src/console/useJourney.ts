import { useEffect, useState, useCallback } from "react"

// Data hooks for the console: fetch the app snapshot + poll the live rail.
// Falls back to a clearly-labeled seeded profile when the API returns an empty bundle
// (common for test numbers), so the demo always shows a populated Step 1.

export type Applicant = {
  name?: string; dob?: string; gender?: string; age?: number
  address?: string; pincode?: string; mobile?: string
}
export type Signals = {
  pan_verify?: { pan?: string; pan_status?: string }
  mobile_intel?: { provider?: string; ported_recently?: boolean }
  epfo?: { uan?: string; employer?: string; employment_type?: string }
  gst?: { gstin?: string; turnover_slab?: string }
  litigation_fir?: Record<string, unknown>
  mca_director?: { director_default?: boolean }
  email_intel?: { email?: string }
  aadhaar_ekyc?: { status?: string; name?: string }
}
export type AppSnapshot = {
  success: boolean
  application_number?: string
  current_step?: number
  applicant: Applicant
  signals: Signals
  seeded?: boolean
}

export type RailGroup = {
  key: string; label: string; sub_score: number
  severity: "ok" | "warn" | "bad" | "idle"; why: string
}
export type Rail = { safety_score: number | null; band: string | null; groups: RailGroup[] }

const SEED: AppSnapshot = {
  success: true,
  application_number: "GFF-DEMO01",
  current_step: 1,
  seeded: true,
  applicant: {
    name: "Rajesh Kumar Menon", dob: "1986-04-12", gender: "male", age: 39,
    address: "14, Whitefield Main Road, Bengaluru, Karnataka", pincode: "560066",
    mobile: "9554259281",
  },
  signals: {
    pan_verify: { pan: "EKOPS9572K", pan_status: "valid" },
    mobile_intel: { provider: "Airtel", ported_recently: false },
    epfo: { uan: "100234567890", employer: "Infosys Ltd", employment_type: "salaried" },
    gst: {},
    litigation_fir: {},
    mca_director: { director_default: false },
    email_intel: { email: "" },
    aadhaar_ekyc: { status: "pending" },
  },
}

function isEmpty(s: AppSnapshot): boolean {
  return !s.applicant?.name && !s.signals?.pan_verify?.pan
}

export function useAppSnapshot(appId: number | null) {
  const [snap, setSnap] = useState<AppSnapshot | null>(null)
  const load = useCallback(async () => {
    if (appId == null) { setSnap(SEED); return }        // no app yet -> demo seed
    try {
      const r = await fetch(`/api/journey/app/${appId}`)
      const d = (await r.json()) as AppSnapshot
      if (!d.success || isEmpty(d)) setSnap({ ...SEED, seeded: true })
      else setSnap(d)
    } catch { setSnap({ ...SEED, seeded: true }) }
  }, [appId])
  useEffect(() => { load() }, [load])
  return { snap, reload: load }
}

export function useRail(appId: number | null, step: number) {
  const [rail, setRail] = useState<Rail | null>(null)
  useEffect(() => {
    if (appId == null) return
    let live = true
    const poll = async () => {
      try {
        const r = await fetch(`/api/journey/rail/${appId}?step=${step}`, { headers: { "Cache-Control": "no-cache" } })
        const d = await r.json()
        if (live && d.success) setRail({ safety_score: d.safety_score, band: d.band, groups: d.groups || [] })
      } catch { /* transient */ }
    }
    poll()
    const t = setInterval(poll, 4000)
    return () => { live = false; clearInterval(t) }
  }, [appId, step])
  return rail
}
