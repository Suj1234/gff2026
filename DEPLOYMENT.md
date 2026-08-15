# GFF 2026 — Deployment Guide

Everything needed to deploy this project and to redeploy it in the future. If you
are new to this project, read this top to bottom once — it explains **what** the
app is, **where** it runs, **how** a deploy happens, and **what secrets** live on
the server.

> Deployment mechanism mirrors the `india-health-platform` project (Harbor + a
> self-hosted GitHub Actions runner on the same VPS). This project sits **beside**
> india-health and kenya on the same server and network — it does not touch them.

---

## 1. What this project is (one app, not three)

GFF 2026 is a **single FastAPI application** that bundles three things into one
process:

| Part | Folder | What it does |
|---|---|---|
| Underwriting agent | `underwriting/` | Rules + scoring + LLM grey-zone judge. The "brain". |
| Journey backend | `journey/` | The customer flow: OTP, identity, financial, health, decision, payment. DB-backed. |
| Journey frontend | `journey-ui/` | The React (Vite) UI the customer sees. Built to static files, served by the backend. |

The journey calls the agent **in-process** (a direct Python function call —
`run_and_report()` in `journey/step_routes.py`), NOT over HTTP. That is why they
ship as **one container**, never split. The whole app runs as:

```
uvicorn underwriting.api:app --host 0.0.0.0 --port 8899
```

---

## 2. Where it runs (the live picture)

**The REAL request path (verified live — do not trust older diagrams):**

```
Browser
  │  https://iadore-onboarding-poc.ins.perfios.com/demo/life
  ▼
Cloudflare  (terminates HTTPS/443 for *.ins.perfios.com — perfios.com CF account)
  │  ALL /demo/* traffic goes to ONE origin door: port 5009
  ▼
india-health-onboarding container  (Next.js, port 5009)  ← the ONLY public door
  │  next.config.mjs rewrites()  /demo/life/*  ->  http://gff2026:8899/demo/life/*
  ▼  (internal, over docker network "demo-net")
gff2026 container   (THIS project — FastAPI, port 8899)
  │
  ├── serves the React journey-ui + /api/* backend + the underwriting agent
  └── DATABASE_URL -> Neon (managed Postgres, external)
```

> ⚠️ **THE ONE RULE: everything public goes through port 5009. There is NO other
> public port.** This app's `8899` is INTERNAL only — never whitelisted, never
> reachable from the browser directly. Do NOT try to expose `8899`, add a new port,
> or whitelist a new URL. Every demo app on this server (india-health, facescan,
> kenya, life) shares the single `/demo/*` door on 5009 and is told apart by PATH.
>
> There is **no nginx gateway** and **no `/opt/gateway/gateway.conf`** in the live
> path (an old note claimed there was — it is WRONG and cost hours of debugging).
> Host **Apache** on `:80` has a leftover `/demo/life -> 8899` ProxyPass line that
> is **dead** (Cloudflare never hits Apache). Ignore it.

| Thing | Value |
|---|---|
| **Public URL** | `https://iadore-onboarding-poc.ins.perfios.com/demo/life` |
| **Public door** | Cloudflare → **port 5009** (india-health). ALWAYS 5009. Never another port. |
| **This app's port** | `8899` — **internal only**, on `demo-net`. Not published publicly, not whitelisted. |
| **Base path baked into app** | `/demo/life` — see `BASE_PATH` env in `deploy.yml`; the FastAPI app is *mounted* under it (`underwriting/api.py`). Do NOT rely on `root_path` — it does not strip/route, only documents. |
| **How it's reached** | india-health's `next.config.mjs` `rewrites()` forwards `/demo/life/*` → `http://gff2026:8899/demo/life/*` |
| **VPS** | `172.17.4.99`, SSH port `1729`, user `sujeetk` (VPN required) |
| **Harbor image** | `harbor.hinagro.com/insurance/gff2026:latest` |
| **Container name** | `gff2026` |
| **Docker network** | `demo-net` (shared with india-health, kenya) — this app AND india-health must both be on it |
| **Env file on VPS** | `/opt/gff2026/.env` |
| **Database** | Neon (Postgres) — connection string in the env file |

Neighbours sharing the single 5009 door (do **not** touch):
- `/demo/acme-insurance`, `/demo/facescan`, … → routes **inside** india-health itself (its own slugs)
- `/demo/life/*` → forwarded to THIS app (the only "separate app" behind the door)

> **Consequence for daily work:** normal code changes here need NOTHING in
> india-health — just `git push` this repo; the runner rebuilds `gff2026`. You only
> touch india-health's rewrite if THIS app's **URL path (`/demo/life`)**, **container
> name (`gff2026`)**, or **internal port (`8899`)** changes. The two repos are
> otherwise fully independent (separate GitHub repos, separate images, separate
> deploys).

> **Gotcha — bare URL trailing slash:** the FastAPI app is *mounted* at `/demo/life`,
> so it answers at `/demo/life/` (slashed) but NOT bare `/demo/life`. Browsers strip
> the trailing slash, so users always hit the bare form. This is handled in
> india-health's rewrite: bare `/life` → `http://gff2026:8899/demo/life/` (slashed
> destination). If you ever change the base path, keep that slash-forward or the bare
> URL 404s with `{"detail":"Not Found"}`.

---

## 3. How a deploy happens (automatic — the normal case)

Once set up (Part A + B below are done once), **every deploy is just a git push**:

```bash
git add .
git commit -m "your change"
git push origin main
```

A **self-hosted GitHub Actions runner** (running ON the VPS) picks it up and:

1. Builds the Docker image (React UI build + Python app) — see `Dockerfile`.
2. Logs in to Harbor and pushes `harbor.hinagro.com/insurance/gff2026:latest`.
3. Stops + removes the old `gff2026` container.
4. Runs the new image with `--env-file /opt/gff2026/.env` on `--network demo-net`.

No SSH, no manual docker commands. Watch progress at: GitHub repo → **Actions** tab.

The workflow lives at `.github/workflows/deploy.yml`.

---

## 4. Callback / redirect URLs (important)

External vendors (NuralX, DigiLocker, iAdore, Razorpay) call BACK into this app.
Because the app lives under `/demo/life`, **every callback URL must include that
prefix**. The app builds most of them automatically from a single env var,
`PUBLIC_API_URL`, so set that correctly and the rest follow.

Set on the server (in `/opt/gff2026/.env`):

```env
PUBLIC_API_URL=https://iadore-onboarding-poc.ins.perfios.com/demo/life
```

Resulting callback URLs (verified against the code):

| Vendor | Full callback / redirect URL | Set where |
|---|---|---|
| **NuralX** (face scan) | `https://iadore-onboarding-poc.ins.perfios.com/demo/life/api/journey/face-scan/callback?key=<NURALX_CALLBACK_SECRET>` | Built automatically from `PUBLIC_API_URL` + `NURALX_CALLBACK_SECRET`. Nothing extra to set. |
| **DigiLocker** | `https://iadore-onboarding-poc.ins.perfios.com/demo/life/digilocker/callback` | `DIGILOCKER_REDIRECT_URL` in env **and** registered in the DigiLocker partner portal — must match exactly. |
| **iAdore** | `https://iadore-onboarding-poc.ins.perfios.com/demo/life/api/journey/...` | `IADORE_CALLBACK_URL` in env. Confirm the exact path with iAdore before go-live. |
| **Razorpay** (payment webhook) | `https://iadore-onboarding-poc.ins.perfios.com/demo/life/api/journey/payment/verify` | Registered in the **Razorpay dashboard**, not the env file. |

> Rule of thumb: any callback = `PUBLIC_API_URL` + the route path. Get
> `PUBLIC_API_URL` right and the app does the rest.

---

## 5. What goes in the server env file

**File:** `/opt/gff2026/.env` on the VPS. **Never committed to git** (`.env` is
gitignored). This is the ONLY place real secrets live. The container reads it at
runtime via `--env-file`.

Fill every value with real credentials (this is a template — do not commit real
values anywhere):

```env
# ── LLM (grey-zone judge) ────────────────────────────────
LLM_MODEL=openai/gpt-4o
OPENAI_API_KEY=              # real key
LLM_BASE_URL=               # company gateway base URL

# ── DATABASE (Neon Postgres) ─────────────────────────────
DATABASE_URL=postgresql://<user>:<password>@<host>.neon.tech/<db>?sslmode=require

# ── APP / PUBLIC URL (drives all callbacks) ──────────────
PUBLIC_API_URL=https://iadore-onboarding-poc.ins.perfios.com/demo/life
SESSION_SECRET=             # 32+ random chars (signs the login cookie)
UW_DEBUG_OTP=false          # true only for demo/testing (exposes OTP)

# ── iAdore ───────────────────────────────────────────────
IADORE_BASE_URL=
IADORE_ORG=
IADORE_ORGANISATION_KEY=
IADORE_PASSPHRASE=
IADORE_X_SECURE_ID=
IADORE_X_SECURE_CRED=
IADORE_X_ORG_ID=
IADORE_CALLBACK_URL=https://iadore-onboarding-poc.ins.perfios.com/demo/life/api/journey/iadore/callback

# ── DigiLocker ───────────────────────────────────────────
DIGILOCKER_BASE_URL=
DIGILOCKER_API_KEY=
DIGILOCKER_REDIRECT_URL=https://iadore-onboarding-poc.ins.perfios.com/demo/life/digilocker/callback

# ── Mobile -> PAN prefill ────────────────────────────────
MOBILE_PAN_BASE_URL=
MOBILE_PAN_API_KEY=
MOBILE_PAN_ENDPOINT=

# ── NuralX (face scan / vitals) ──────────────────────────
NURALX_BASE_URL=
NURALX_EMAIL=
NURALX_PASSWORD=
NURALX_CALLBACK_SECRET=     # shared ?key= secret protecting the callback

# ── Email (AWS SES) ──────────────────────────────────────
AWS_SES_ACCESS_KEY=
AWS_SES_SECRET_KEY=
AWS_SES_REGION=
AWS_SES_FROM_ID=
AWS_SES_FROM_NAME=

# ── Payment (Razorpay) ───────────────────────────────────
RAZORPAY_MODE=test          # test | live
RAZORPAY_TEST_KEY_ID=
RAZORPAY_TEST_KEY_SECRET=
RAZORPAY_WEBHOOK_SECRET=
```

To edit later:
```bash
sudo nano /opt/gff2026/.env      # change values, save (Ctrl+X -> Y -> Enter)
sudo docker restart gff2026      # picks up the new values
```

---

## PART A — First-time setup: the repo (once)

These files must exist in the repo for auto-deploy to work. (If they are already
present, skip.)

1. **`Dockerfile`** — 2-stage build: (1) `node` stage builds `journey-ui` into
   static files; (2) `python:3.13-slim` stage installs `requirements.txt`, copies
   `underwriting/` + `journey/` + the built UI, runs uvicorn on 8899.
2. **`.dockerignore`** — excludes `.venv/`, `node_modules/`, `__pycache__/`,
   `*.pdf`, `files.zip`, `journey.db`, docs — so the image stays small and builds fast.
3. **`.github/workflows/deploy.yml`** — the auto-deploy workflow (Part 3 above).

**GitHub secrets** (repo → Settings → Secrets and variables → Actions):

| Secret | Value |
|---|---|
| `HARBOR_USERNAME` | your Harbor username (e.g. `sujeet.kumar`) |
| `HARBOR_PASSWORD` | your Harbor password |

> The runner runs ON the VPS, so it deploys locally — no `VPS_SSH_KEY` needed.

**Self-hosted runner** (repo → Settings → Actions → Runners): one runner must
show **Idle** (green). If offline, restart the runner service on the VPS. Without
it, `git push` builds nothing.

---

## PART B — First-time setup: the server (once)

Do these on the VPS (VPN connected):

```bash
ssh sujeetk@172.17.4.99 -p 1729
```

### B1. Create the app folder + env file
```bash
sudo mkdir -p /opt/gff2026
sudo nano /opt/gff2026/.env        # paste the full env from section 5, save
```

### B2. Log in to Harbor on the VPS (so it can pull the image)
```bash
sudo docker login harbor.hinagro.com     # username + password
```

### B3. Add the gateway route
Edit `/opt/gateway/gateway.conf` and add this block **above** the `location / {`
catch-all (nginx matches most-specific first, so `/demo/life` reaches this app
while everything else still goes to india-health):

```nginx
    location /demo/life/ {
        proxy_pass http://gff2026:8899;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
```

Then reload nginx (the gateway container):
```bash
# find the nginx/gateway container name, then:
sudo docker exec <gateway-container> nginx -s reload
```

> Do NOT edit the kenya or `/` blocks. Only add the one new block.

---

## PART C — First deploy

```bash
git add .
git commit -m "deploy: full GFF 2026 (UI + backend + agent)"
git push origin main
```

Watch GitHub → **Actions** until green (~3–5 min). Then verify:

```bash
# on the VPS
sudo docker ps | grep gff2026                 # Up, healthy
sudo docker logs --tail 50 gff2026            # no errors, uvicorn running on 8899
```

Open in a browser: **https://iadore-onboarding-poc.ins.perfios.com/demo/life**

---

## PART D — Future deployments (the everyday case)

Just:
```bash
git push origin main
```
GitHub Actions rebuilds + redeploys automatically. Nothing else.

To change a secret / callback URL (no code change): edit `/opt/gff2026/.env` on
the VPS, then `sudo docker restart gff2026`.

---

## PART E — Useful commands on the VPS

```bash
sudo docker ps | grep gff2026                 # is it running?
sudo docker logs -f gff2026                    # live logs
sudo docker logs --tail 100 gff2026            # recent logs
sudo docker restart gff2026                     # restart (e.g. after env change)
sudo docker inspect gff2026 | grep -A40 '"Env"' # verify env loaded
sudo docker network inspect demo-net            # confirm it's on the network with nginx
```

---

## PART F — Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `/demo/life` shows india-health, not GFF | gateway block missing or below `location /` | Add the `/demo/life/` block ABOVE the catch-all, reload nginx (B3) |
| CSS/JS 404, blank page | app not built for the `/demo/life` base path | Rebuild — the base path is baked at build time (Dockerfile) |
| Callbacks (NuralX/DigiLocker) never arrive | `PUBLIC_API_URL` wrong or missing `/demo/life` | Fix `PUBLIC_API_URL` in `/opt/gff2026/.env`, restart |
| DB errors on startup | `DATABASE_URL` wrong / Neon unreachable | Check the Neon string + that the VPS can reach Neon |
| `git push` does nothing | self-hosted runner offline | Restart the runner on the VPS (Part A) |
| Harbor push fails in Actions | missing/wrong `HARBOR_*` GitHub secrets | Re-add them (Part A) |
| SSH `Connection timed out` | VPN not connected | Connect the corporate VPN first |
| 502 from gateway | container down or wrong port | `docker ps`/`docker logs gff2026`; confirm port 8899 + `demo-net` |

---

## Appendix — what is deliberately NOT here

- **No secrets in git.** All live in `/opt/gff2026/.env` on the VPS only.
- **No separate agent container.** The agent is a Python package inside this one app.
- **No local DB.** Persistence is Neon (managed Postgres).
- **India-health / kenya untouched.** We only add one nginx block beside theirs.
