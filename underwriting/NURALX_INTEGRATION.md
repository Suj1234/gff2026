# NuralX Face-Vitals Integration (GFF 2026)

Ported from the Kenya Life platform (TypeScript/Express) to this project's stack
(Python / FastAPI / httpx). Two files, no database required for the demo.

| File | What it is |
|---|---|
| `nuralx.py` | The API client: 4-step auth flow (`generate-credentials` → `token` → `patient-data`), token caching, and webhook vitals mapping. Uses `httpx` (already in requirements). |
| `nuralx_routes.py` | FastAPI router: create session, poll result, receive the webhook. Sessions held in an in-memory dict (swap for a real store in prod). |

## Wire it into the app (1 line)

In `underwriting/api.py`:

```python
from .nuralx_routes import router as nuralx_router
app.include_router(nuralx_router)
```

## Env vars — add to `.env`

```
NURALX_BASE_URL=          # MUST end with a trailing slash /
NURALX_EMAIL=             # NuralX account email
NURALX_PASSWORD=          # NuralX account password
NURALX_CALLBACK_SECRET=   # any random string you pick; NuralX echoes it as ?key=<secret>
PUBLIC_API_URL=           # public URL of THIS service (used to build the callback URL)
```

The webhook URL NuralX will call is assembled as:
`${PUBLIC_API_URL}/nuralx/callback?key=${NURALX_CALLBACK_SECRET}`

## Flow at runtime

```
POST /nuralx/sessions {name,email,phone}  → { token, scan_url }   (open scan_url on device / send to customer)
   customer completes the scan in NuralX's widget
NuralX POSTs vitals → /nuralx/callback?key=<secret>               (session → COMPLETED)
GET  /nuralx/sessions/{token}             → { status, vitals, raw_results }
```

## Three gotchas already handled (do not "fix")

1. `NURALX_BASE_URL` **must end with `/`** — endpoints are concatenated directly.
2. The token's `access_token` **already contains `"Bearer "`** — sent as-is in a
   **lowercase `authorization`** header. Don't prepend "Bearer" again.
3. The webhook body is read **raw + `json.loads`d** — NuralX may send an off Content-Type.
