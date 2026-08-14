# dev.ps1 — start the journey app locally (backend + frontend).
# Backend: FastAPI (underwriting.api:app) on :8899  — mounts /api/auth, /api/journey, underwriting engine.
# Frontend: Vite dev server on :5173 — proxies /api/* to the backend.
# Usage:  .\dev.ps1        (opens two windows, then the browser)
#         .\dev.ps1 -NoBrowser
param([switch]$NoBrowser)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

Write-Host "Starting backend (FastAPI :8899)..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList @(
  "-NoExit","-Command",
  "cd '$root'; .\.venv\Scripts\Activate.ps1; uvicorn underwriting.api:app --host 127.0.0.1 --port 8899 --reload"
)

Write-Host "Starting frontend (Vite :5173)..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList @(
  "-NoExit","-Command",
  "cd '$root\journey-ui'; npm run dev"
)

if (-not $NoBrowser) {
  Start-Sleep -Seconds 4
  Start-Process "http://localhost:5173"
}

Write-Host ""
Write-Host "Backend:  http://127.0.0.1:8899  (API docs at /docs)" -ForegroundColor Green
Write-Host "Frontend: http://localhost:5173" -ForegroundColor Green
Write-Host "Tip: set UW_DEBUG_OTP=1 in .env to read the OTP from the backend window." -ForegroundColor Yellow
