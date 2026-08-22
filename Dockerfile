# GFF 2026 — one image: React UI + journey backend + underwriting agent.
# Served by uvicorn at :8899, behind the gateway at /demo/life.

# ── Stage 1: build the React journey-ui into static files ──────────────────────
FROM node:22-alpine AS ui
WORKDIR /ui

COPY journey-ui/package.json journey-ui/package-lock.json* ./
RUN npm install

COPY journey-ui/ ./
# base=/demo/life/ is set in vite.config.ts so assets resolve behind the gateway.
RUN npm run build

# ── Stage 2: the Python app ───────────────────────────────────────────────────
FROM python:3.13-slim
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# The full app: agent + journey backend.
COPY underwriting ./underwriting
COPY journey ./journey
# Repo-root vendor clients — imported by journey/step_routes.py (`import bank_statement`,
# `from prescription_ocr import extract`). Both live at the root, NOT inside a package, so
# each must be copied explicitly or its upload dies on prod with "No module named '...'".
COPY bank_statement.py ./
COPY prescription_ocr.py ./

# The built React UI, served by FastAPI (see underwriting/api.py).
COPY --from=ui /ui/dist ./journey-ui/dist

EXPOSE 8899
# 1 worker is plenty for a demo; grey-zone calls are LLM-bound, not CPU-bound.
CMD ["uvicorn", "underwriting.api:app", "--host", "0.0.0.0", "--port", "8899"]
