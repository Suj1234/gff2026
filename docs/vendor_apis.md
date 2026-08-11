# Vendor / Enrichment APIs — reference for the onboarding journey

Real request/response shapes for the data-fetch calls the demo journey makes, and how
each maps into the engine's internal `signals` contract (`underwriting/schemas.py`).
The engine consumes **facts only** (`IMPLEMENTATION_PLAN.md §1.8`) — verdict-looking
fields in these payloads (`fraudRisk`, `severity`, `riskTags`, `activeAlerts`) are the
VENDOR's labels; we may read them as facts but WE produce the final judgment.

Adapter seam: a raw vendor payload → internal shape is mapped in `underwriting/sources/`
(one `@adapter("<source_key>")` fn per source). Unregistered sources pass through
unchanged. See `sources/__init__.py`.

---

## 1. Mobile → PAN + full profile  (primary identity+enrichment call)

One call from a mobile number returns mobile intelligence, PAN, identity (name/DOB/
gender/address), employment (EPFO/UAN), litigation, sole-proprietor+GST, director
profile. This is the journey's Step-1 fetch — **PAN and everything else pre-fill from
the mobile number.**

### Request
```json
{
  "mobile": "9739780007",
  "includeLitigationdetails": true,
  "includeSoleProprietordetails": true,
  "SoleProprietoralerts/charges": true,
  "includeDirectordetails": true,
  "Directoralerts/charges": true
}
```

### Response (self-employed example — Paulson Mathew, high litigation)
```json
{
  "success": true,
  "data": {
    "mobile": "9739780007",
    "mobileIntelligence": {
      "isPorted": "Yes", "mobileAge": "18 to 19 Years", "status": "Active",
      "numberValid": "Yes", "currentRegion": "Karnataka",
      "currentServiceProvider": "Airtel", "originalRegion": "Karnataka",
      "originalServiceProvider": "Vodafone", "roamingStatus": "No"
    },
    "pan": "BHYPM4927Q",
    "identity": {
      "name": "PAULSON MATHEW", "firstName": "PAULSON", "middleName": "", "lastName": "MATHEW",
      "fatherName": "KOOTTIYANI MATHEW PAULSON", "dob": "1992-05-20", "gender": "male",
      "panStatus": "Active", "aadhaarLinked": true,
      "isSalaried": null, "isDirector": false, "isSoleProp": true,
      "address": { "buildingName": "47/1B", "locality": "", "streetName": "VADUTHALA S.O",
                   "city": "CHERANALLUR", "state": "KERALA", "pincode": "682023" }
    },
    "employment": null,
    "litigation": {
      "filter": { "district": "Ernakulam", "state": "Kerala", "pincode_matched": true },
      "totalCases": 10, "pendingCases": 1, "disposedCases": 9,
      "criminalCases": 10, "civilCases": 0, "highSeverityCases": 10,
      "statistics": {
        "asPetitioner": { "total": 0, "civil": 0, "criminal": 0 },
        "asRespondent": { "total": 10, "civil": 0, "criminal": 10 }
      },
      "cases": [
        {
          "cino": "KLER460009992026", "caseNumber": "ST/636/2026", "type": "Criminal",
          "status": "Pending", "filingDate": "2026-05-07", "nextHearingDate": "2026-08-17",
          "decisionDate": null, "court": "Judicial First Class Magistrate Court-2, Kothamangalam",
          "district": "Ernakulam", "state": "Kerala",
          "acts": ["bharatiya nyaya sanhita", "Motor Vehicles Act"], "sections": ["281", "185"],
          "partyRole": "respondent", "petitioners": ["State of Kerala (Police)"],
          "respondents": ["PAULSON MATHEW"], "severity": "high",
          "firDetails": [{ "policeStation": "Oonnukal", "firYear": "2026", "firNo": "328" }],
          "riskTags": ["Criminal"]
        }
        // ... 9 more (disposed); several NI Act §138 cheque-bounce, IPC 188/269 epidemic,
        //     riskTags include "Financial Liability, Criminal, Cheque bounce"
      ]
    },
    "soleProprietor": {
      "businessProfile": {
        "tradeName": "BREWCHA", "type": "SOLE_PROPRIETOR", "constitutionOfBusiness": "PROPRIETORSHIP",
        "registeredAddress": "26, VEMEE SADHANA, 1st MAIN, LAKSHMIPURAM, ULSOOR, Bengaluru Urban, Karnataka, 560008",
        "dateOfIncorporation": "08-02-2020",
        "natureOfBusiness": ["Retail Business", "Wholesale Business", "Factory / Manufacturing"],
        "entityId": "BHYPM4927Q", "pan": "BHYPM4927Q"
      },
      "gst": [
        { "gstin": "32BHYPM4927Q1ZC", "status": "CANCELLED ON APPLICATION OF TAXPAYER",
          "legalName": "PAULSON MATHEW", "coreActivity": null,
          "turnovers": [{ "turnover": "Slab: Rs.0 to 40 lakhs", "financialYear": "2024-2025" }] },
        { "gstin": "29BHYPM4927Q1ZZ", "status": "ACTIVE", "legalName": "PAULSON MATHEW",
          "coreActivity": "TRADER - RETAILER",
          "turnovers": [{ "turnover": "Slab: Rs.0 to 40 lakhs", "financialYear": "2024-2025" }] }
      ],
      "activeAlerts": [
        { "key": "isGstCancelled", "severity": "medium", "source": ["gst"],
          "label": ["ids"], "value": ["32BHYPM4927Q1ZC"] },
        { "key": "isGstTransactionDelay", "severity": "medium", "source": ["gst"],
          "label": ["ids", "latestMonthOfDelay"],
          "value": ["29BHYPM4927Q1ZZ", "32BHYPM4927Q2ZB", "202512", "202512"] }
      ],
      "meta": { "entityName": "BREWCHA", "pan": "BHYPM4927Q" }
    },
    "directorProfile": null
  }
}
```

### Response (salaried example — Sabarish, clean: 0 litigation, has EPFO employment)
Same shape; key differences: `identity.isSalaried: true`, `employment` populated
(UAN + `currentEmployer: "OPEN FINANCIAL TECHNOLOGIES PRIVATE LIMITED"`),
`litigation.totalCases: 0`, GST `activeAlerts: []`. (mobile `8884609090`, PAN `EKOPS9572K`.)

---

## 2. PAN → same profile  (fallback when mobile does not resolve a PAN)

When Step-1 mobile lookup returns no PAN, ask the user to type their PAN and call this.
Returns the **same `data` shape minus `mobileIntelligence`**.

### Request
```json
{
  "pan": "EKOPS9572K",
  "includeLitigationdetails": true,
  "includeSoleProprietordetails": true,
  "SoleProprietoralerts/charges": true,
  "includeDirectordetails": true,
  "Directoralerts/charges": true
}
```
Response: identical to §1 `data` block for that PAN (identity + employment + litigation +
soleProprietor + directorProfile), no `mobileIntelligence`.

---

## 3. Email intelligence

### Request
```json
{ "email": "sujeet.kr2496@gmail.com" }
```

### Response
```json
{
  "success": true,
  "data": {
    "email": "sujeet.kr2496@gmail.com",
    "verification": {
      "validity": { "isDisposable": false, "isWebmail": true, "hasValidFormat": true,
        "hasMxRecords": true, "smtpReachable": true, "result": "valid",
        "isBlocked": false, "reason": "user_exist", "isGeneric": false },
      "summary": { "isValid": true, "overallResult": "invalid",
        "orgDomainMatch": false, "indvFlag": false },
      "individualMatch": [{ "name": "sujeet kr", "match": false, "score": 0 }],
      "spamRecord": { "isSpam": false, "reportCount": 0, "isIpBlacklisted": false },
      "whoisInfo": { "ageYear": 0, "expired": false }
    },
    "domainDetails": { "domainName": "gmail.com", "company": "Google", "category": "Webmail",
      "isCorporate": false, "domainAge": "1995-08-13 12:30:00", "domainCreationDays": "11320" },
    "fraud": {
      "risk": { "score": 83, "fraudRisk": "Very Low", "advice": "Lower Fraud Risk",
        "reason": "Email Created at least 7.4 Years Ago" },
      "domain": { "riskLevel": "Moderate" },
      "validation": { "status": "Verified", "emailExists": true, "domainExists": true,
        "firstVerificationDate": "2019-04-02 17:18:09", "firstSeenDays": "2687" }
    }
  }
}
```
Note the vendor's `fraud.risk.score` is **1–100 where HIGHER = SAFER** ("Fraud Score 1 to
100", 83 = "Very Low" risk) — the OPPOSITE polarity of the engine's 0–1 `ml_scores`
(higher = riskier). Any adapter MUST invert.

---

## 4. Bank-statement analysis — iAdore (Perfios)  [READY — real]

Replaces the Account Aggregator pull for income corroboration. Used in the STEP_UP
income-gather cycle (`request_additional_document(bank_statement)`): the applicant
uploads a PDF statement; iAdore analyses it; the report becomes the
`follow_up_observations.bank_statement` the re-judge reads.

Reference client shipped: `bank_statement.py` (repo root) — `analyze("statement.pdf")`
returns the analysis dict. Env keys in `.env` (`IADORE_*`). Only new dep: `requests`.

### The 3 calls (in order)
| Step | Method & URL | Notes |
|---|---|---|
| Submit | `POST /api/v1/iadore/acme-india/consolidatedProcess` | multipart; **file field name = `bank`**; `processType=FINANCIAL` |
| Poll | `GET /api/v1/iadore/acme-india/{tx_id}/status` | repeat until `status=COMPLETED` |
| Report | `GET /iadore/api/v1/acme-india/{tx_id}/json/report` | ⚠️ path order flips — `iadore` BEFORE `api/v1` |

- Base URL: `https://iadore-poc.ins.perfios.com` · Org: `acme-india`

### Auth headers (every call)
```
signature:         Base64(HMAC-SHA256(key=IADORE_ORGANISATION_KEY,
                          msg="PERFIOS-HMACSHA256 " + IADORE_PASSPHRASE))
x-secure-id:       <IADORE_X_SECURE_ID>
x-secure-cred:     <IADORE_X_SECURE_CRED>
x-organization-id: <IADORE_X_ORG_ID>
accept:            application/json
```
Secrets live in `.env` (`IADORE_ORGANISATION_KEY`, `IADORE_PASSPHRASE`,
`IADORE_X_SECURE_ID`, `IADORE_X_SECURE_CRED`, `IADORE_X_ORG_ID`, `IADORE_CALLBACK_URL`).

### The 3 gotchas that break integrations
1. File field name must be **`bank`** (not `file`/`statement`), with `processType=FINANCIAL`.
2. Report URL path order is `/iadore/api/v1/...`, unlike submit/poll's `/api/v1/iadore/...`.
3. Signature = HMAC-SHA256 with **key = ORGANISATION_KEY, message = "PERFIOS-HMACSHA256 " +
   passphrase**, Base64-encoded (message first, key second — CryptoJS argument order).

### → engine mapping
The iAdore JSON report → internal `account_aggregator` shape (the source key the BRE
already reads for R-007/R-008) OR the `follow_up_observations.bank_statement` shape the
re-judge reads: `{verified_annual_income, salary_credit_monthly, avg_monthly_balance,
corroborates_declared_income}`. Verdict fields the vendor may include are dropped (§1.8).

---

## 5. Face scan — NuralX  [READY — real; agent-only, async webhook]

Supplies liveness + contactless vitals. **Results are agent/underwriter-only — the
applicant's device NEVER shows vitals.** Shipped in this repo: `underwriting/nuralx.py`
(client), `underwriting/nuralx_routes.py` (routes + webhook), `underwriting/NURALX_INTEGRATION.md`.

### PART A — vendor contract (4-call flow)
5 env vars: `NURALX_BASE_URL` (MUST end with `/`), `NURALX_EMAIL`, `NURALX_PASSWORD`,
`NURALX_CALLBACK_SECRET` (you choose; echoed back as `?key=`), `PUBLIC_API_URL`.
**No static API key** — auth is email+password → client creds → bearer token.

```
1. POST {base}generate-credentials  {email,password}          → client_id, client_secret
2. POST {base}token                 {client_id,client_secret} → access_token, expires_in
3. POST {base}patient-data          {patient + client_transaction_ID + call_back_URL}
                                                              → scan_access_url
4. NuralX → POST {your callback}?key=<secret>  {results}      ← vitals (async, minutes later)
```
- Call 2's `access_token` **already contains the literal "Bearer "** — use verbatim in a
  **lowercase** `authorization` header; do NOT prepend a second "Bearer".
- Call 3 `client_transaction_ID` = your UUID, echoed back in the webhook. `scan_access_url`
  is what the applicant's phone opens to run the scan.
- Webhook success body:
```json
{ "status": "completed", "client_transaction_ID": "<uuid>",
  "results": {
    "pulseRate":       { "value": 72, "confidenceLevel": 0.91 },
    "respirationRate": { "value": 16, "confidenceLevel": 0.87 },
    "bloodPressure":   { "value": { "systolic": 118, "diastolic": 76 }, "confidenceLevel": 0.82 },
    "stressIndex": {"value":120}, "wellnessIndex": {"value":78},
    "sdnn": 45, "rmssd": 38, "oxygenSaturation": 97 } }
```
  Failure: `{ "status": "error"|"timeout", "client_transaction_ID": "..." }` — no results.

### PART A gotchas (each broke the original build)
1. `NURALX_BASE_URL` must end with `/` (endpoints are string-concatenated).
2. Token already has "Bearer "; lowercase `authorization` header; don't double-prefix.
3. Read the webhook body **raw then JSON-parse yourself** — NuralX sends an inconsistent
   Content-Type; framework auto-parsers silently drop the body (real callback looks empty).
4. Verify `?key=` == `NURALX_CALLBACK_SECRET` before trusting a webhook (only auth on it).
   ACK 200 immediately, process async.
5. Cache the token (refresh at `expires_in` − 60s); don't re-run calls 1–2 per scan.
6. Vitals `results` is inconsistent: some fields `{value, confidenceLevel}`, some plain
   numbers; BP `value` is nested `{systolic, diastolic}`. Unwrap: if object has `value`,
   take `.value`, else the field as-is.

### PART B — product (session state machine; the other project builds the UI)
Session: `PENDING →(applicant taps Start)→ IN_PROGRESS →(webhook ok)→ COMPLETED`;
`EXPIRED` on either TTL, `ERROR/TIMEOUT` on failure webhook. Two TTLs: **primary**
(PENDING — QR 20 min, Email 48 h) + **secondary** (IN_PROGRESS — 30 min, catches
abandonment). Delivery channels: **QR** (in-person) or **email link**, both pointing to
YOUR instructions page (`{frontend}/face-scan/{token}`), NOT the raw NuralX scan_url —
you redirect to scan_url only after "Start" (lets you device-gate + log SCAN_STARTED).
Multiple webhooks per session are legitimate (retry; a later PASS supersedes a FAIL).
Persist sessions in a real store. Callback URL to register:
`{PUBLIC_API_URL}/api/v1/face-scan/callback?key={NURALX_CALLBACK_SECRET}`.

**For THIS demo (insurer-only):** vitals render in the agent's right-rail live panel; the
applicant only ever sees "scan complete". Disclaimer required: "algorithmic estimates,
not a medical diagnosis; must not be used for underwriting decisions."

### → engine mapping
- Liveness/deepfake/facematch → `signals.liveness_facematch.{liveness_pass, liveness_score,
  face_match_score, deepfake_flag}` — the **R-003 DECLINE hard gate**.
- Contactless vitals (`pulseRate`, `respirationRate`, `oxygenSaturation`, BP) →
  `signals.rppg_scan.vitals.{heart_rate, respiratory_rate, spo2, bp}` — **R-017 step-up**.
- BMI/smoking estimate (if returned) → `signals.facial_bmi_smoking`.
NuralX vitals are wellness estimates — per the vendor disclaimer they feed step-up
triage (R-017), never a standalone loading/decline.

---

## 6. DigiLocker Aadhaar e-KYC — Perfios KYC  [READY — real; keyed-mock fallback]

The consent-mandatory Aadhaar path (Aadhaar Act; `India_Health_Insurance_Data_Sources.md`).
Used in Step 1 (Identity & KYC). We do **not** capture a raw Aadhaar number — DigiLocker
never exposes it; it returns verified e-KYC *fields* (name/DOB/gender/address/photo) + PAN.
Env keys in `.env` (`DIGILOCKER_*`). If `DIGILOCKER_API_KEY` is blank, the journey falls
back to a keyed mock (like ABHA) so the demo never blocks.

### The 3 calls (in order — all must run in succession)
| Step | Method & URL | Purpose |
|---|---|---|
| Link | `POST {base}/kyc/api/v1/digilocker/link` | returns `result.link` — the DigiLocker consent URL the applicant opens |
| List | `POST {base}/kyc/api/v1/digilocker/documents` | after user grants: lists available docs (`ADHAR`, `PANCR`, …) with their `uri`s |
| Download | `POST {base}/kyc/api/v1/digilocker/download` | pulls the parsed data for chosen `uri`s (ADHAR + PANCR) |

- Base URL: `https://api-in-uat.perfios.com` (UAT). Header on every call: `x-api-key: <token>`, `Content-Type: application/json`.
- `caseId` (under `clientData`) correlates the 3 calls = our `proposal_id`.
- `oAuthState` (link call) is a CSRF/correlation nonce we generate; `redirectUrl` = our `DIGILOCKER_REDIRECT_URL`.
- `accessRequestId` (returned by /link as `requestId`) is passed into /documents and /download.
- Flags on /link: `aadhaarFlowRequired: true`, `pinlessAuth: true`, `customDocList: "ADHAR,PANCR"`, `consent: "Y"`.

### Link — request / response (abridged)
```json
// POST /kyc/api/v1/digilocker/link
{ "redirectUrl": "<DIGILOCKER_REDIRECT_URL>", "oAuthState": "<nonce>",
  "aadhaarFlowRequired": true, "pinlessAuth": true,
  "customDocList": "ADHAR,PANCR", "consent": "Y",
  "clientData": { "caseId": "<proposal_id>" } }
// →
{ "requestId": "<accessRequestId>", "result": { "link": "https://api.digitallocker.gov.in/.../authorize?..." },
  "statusCode": 101, "clientData": { "caseId": "<proposal_id>" } }
```

### Download — response shape the adapter reads (Aadhaar `issuedTo` parsed data)
`result[].parsedFile.data.issuedTo`:
`{ name, dob, gender, maritalStatus, photo:{content(b64), format}, address:{house,locality,vtc,district,pin,state,country}, uid }`
and the PAN doc's `result[].parsedFile.data`: `{ number (PAN), status ("A"=active), issuedTo.name }`.
`parsedFile.xmlSignatureVerified: true` = the UIDAI signature validated (an authenticity FACT).

### → engine mapping
DigiLocker parsed Aadhaar → internal `signals.aadhaar_ekyc.{name, dob, address, photo}`
(+ `gender`), consumed by **R-015 identity consistency** (name/DOB/address cross-source).
The PAN doc corroborates `pan_verify.{pan, pan_status}`. Verdict-looking fields the vendor
may add are dropped per §1.8. A DigiLocker consent entry is appended to `consents[]`
(`{type: aadhaar_ekyc, framework: Aadhaar_Act, granted: true}`).

---

## Field → internal `signals` mapping (what the engine reads today vs. gaps)

| Vendor field | Internal `signals.<key>.<field>` | Engine use today |
|---|---|---|
| `identity.panStatus` ("Active") | `pan_verify.pan_status` → "valid" (adapter `ACTIVE→valid`) | **R-002 hard gate** |
| `identity.{name,dob,gender,address}` | `pan_verify.*` + prefill `application.applicant` | R-015 consistency, form prefill |
| `identity.aadhaarLinked` | `pan_verify.aadhaar_seeded` | scoring |
| `mobileIntelligence.*` | `mobile_intel.{provider,vintage_months,ported_recently}` | consistency only |
| `employment.{uan,currentEmployer}` | `epfo.{employer,date_of_joining,name}` | income/occupation sub-score |
| `soleProprietor.gst[]` | `gst.{gstin,turnover_slab,firm_type}` | financial/occupation |
| `soleProprietor.activeAlerts` (isGstCancelled/isGstTransactionDelay) | **not modeled** (pass-through) | **GAP — no rule reads it** |
| `litigation.{criminalCases,cases[],firDetails}` | `litigation_fir` (pass-through) | **GAP — scorer expects `cases[].civil_criminal` + `firs_registered`; this shape scores clean** |
| `identity.isDirector` / `directorProfile` | `mca_director.director_default` | R-012 moral-hazard + fraud sub-score |
| Email `fraud.risk.score` / `isDisposable` / `individualMatch` | `email_intel` (**not modeled**) | **GAP — no rule reads it; polarity inverted** |
| iAdore bank-statement report (§4) | `account_aggregator` / `follow_up_observations.bank_statement` | R-007 / R-008 + STEP_UP income re-judge |
| NuralX face scan (§5) | `liveness_facematch` / `rppg_scan` / `facial_bmi_smoking` | R-003 (DECLINE) / R-017 (step-up) / lifestyle |

**Gaps to close before these payloads drive a decision** (data only — no engine rewrite):
1. **Litigation adapter** — map `litigation` → `litigation_fir` shape the scorer reads
   (`firs_registered` from `cases[].firDetails[]`; `cases[].civil_criminal` from `type`).
   Without it Paulson's 10 criminal cases score as "no adverse litigation".
2. **GST activeAlerts** — a rule/penalty for `isGstCancelled` / `isGstTransactionDelay`
   (the plan's canonical `gst_transaction_delay` verdict, `IMPLEMENTATION_PLAN.md`).
3. **Email intel** — decide if it feeds the fraud sub-score; invert the 1–100 polarity.
