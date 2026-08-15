import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

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

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
