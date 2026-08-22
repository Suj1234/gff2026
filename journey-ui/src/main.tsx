import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { FaceScanMobile } from './pages/FaceScanMobile.tsx'

// The app is served under a base path (/demo/life/ in prod) behind the gateway.
// All fetch() calls use absolute "/api/..." paths; prefix them with the base once
// here so we don't touch ~21 call sites. Dev base is "/", so this is a no-op locally.
const BASE = import.meta.env.BASE_URL.replace(/\/$/, "")
if (BASE) {
  const origFetch = window.fetch.bind(window)
  window.fetch = (input, init) => {
    if (typeof input === "string" && input.startsWith("/api/")) {
      input = BASE + input
    }
    return origFetch(input, init)
  }
}

// /face-scan/{token} is a session-less public page the APPLICANT'S PHONE opens (from the
// QR shown on the agent's desktop) — it never goes through App's login/console flow.
const faceScanMatch = window.location.pathname.replace(BASE, "").match(/^\/face-scan\/([^/]+)\/?$/)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {faceScanMatch ? <FaceScanMobile token={faceScanMatch[1]} /> : <App />}
  </StrictMode>,
)
