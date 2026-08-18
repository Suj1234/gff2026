# Right-Rail Underwriter Plan — use EVERY data point

**Status: PLAN ONLY. No code changed.** This is the design for how the right-hand
"Agent Read" rail should use every data point the vendors return, thinking like an
underwriter, across the three persona types (salaried / self-employed / both).

The guiding principle: **an underwriter looks at different things for a salaried
employee than for a business owner.** The rail must (a) capture every field we
currently drop, (b) turn each into a *flag* with underwriter meaning, and (c) show a
persona-appropriate set of groups — GST/business for the self-employed, EPFO/tenure
for the salaried.

---

## 0. The three personas (what actually arrives)

| | Sujeet | Sabarish | Paulson (Mathew) |
|---|---|---|---|
| Type | Salaried | Salaried **+** self-employed | Self-employed only |
| `isSalaried` | true | true | null |
| `isSoleProp` | ? | true | true |
| `isDirector` | ? | false | false |
| EPFO/UAN | ✅ | ✅ | null |
| GST | — | 1 (ACTIVE) | 2 (1 CANCELLED + 1 ACTIVE) + alerts |
| Litigation | 0 | 0 | 10 criminal + FIRs |
| Mobile | ported, ? age | ported, 11-12y | ported, 18-19y |

**Rule of thumb for the rail:** which income-verification group is authoritative
follows the persona — EPFO for salaried, GST+bank-statement for self-employed, both
weighed for the hybrid. An underwriter never treats a business owner's "no EPFO" as a
red flag; they pivot to GST/ITR/bank statement.

---

## 1. THE BUGS — fix regardless of any scoring decision

These are silent misses (same class as the litigation bug already caught). They are
not "features" — the data arrives and we drop it, and in some cases a **rule already
exists** that never fires.

| # | Bug | Effect | Fix |
|---|---|---|---|
| B1 | `identity.isDirector` / `directorProfile` never mapped | The −35 director-defaulter rule (R-012 / scoring `_s_occupation`) can NEVER fire; a director applicant scores clean on a group built to catch them | Map `isDirector` + `directorProfile` → `signals.mca_director {available, is_director, director_default}` |
| B2 | Only `gst[0]` captured | Paulson has 2 GSTINs (one **CANCELLED**); if ACTIVE is index 0, the cancelled status is invisible → R-019 under-fires | Capture ALL gst[] entries; derive "any cancelled" across the list |
| B3 | `dateOfJoining` read from wrong path | Always null (vendor nests it under `history[].dateOfJoining`) → job-tenure signal always empty | Read from `employment.history[]` |
| B4 | `activeAlerts` only read when `gst` present | If alerts arrive without a gst array they're dropped | Read alerts independent of the gst[] presence |

**These four should be done first and are not "scoring" changes** — they make existing
logic receive the data it was written for.

---

## 2. EVERY data point → underwriter meaning → flag → score → rail

Legend for **Score**: 🔴 new penalty proposed (magnitude = underwriter-calibration,
parked) · 🟢 already scored · ⚪ display-only (no score, context for the human).

### Block A — Mobile Intelligence
| Field | Underwriter reads it as | Flag | Score | Rail |
|---|---|---|---|---|
| `mobileAge` | **Genuineness signal.** 18y-old number = strong real-identity; <6-month number = classic fraud/synthetic-identity | `mobile_recent_number` (age < threshold) | 🔴 fraud + contactability | Show age; young → amber |
| `isPorted` + recency | Recent port near application = SIM-swap / takeover risk. Old port = benign | `recent_port` (only if recency known) | 🔴 fraud (mild) | The "Ported" chip should carry weight, not be decoration |
| `numberValid` = No | Dead/invalid number → contactability failure | `mobile_invalid` | 🔴 contactability (hard) | Red |
| `status` (Active) | Live number confirmation | — | 🟢 assessed=true basis | ⚪ |
| `currentRegion` vs `originalRegion` | Region jump can indicate relocation or mule number | `mobile_region_shift` | 🔴 fraud (mild) | ⚪ show both |
| `roamingStatus` | Minor context | — | — | ⚪ |

> Today: only `provider` + `ported_recently` captured, and **neither is scored**. The
> "Ported" badge you see does nothing. Mobile age — the single best genuineness signal
> here — is dropped entirely.

### Block B — Identity
| Field | Underwriter reads it as | Flag | Score | Rail |
|---|---|---|---|---|
| `panStatus` | Valid identity gate | R-002 (hard decline if not valid) | 🟢 | 🟢 |
| `aadhaarLinked` | KYC completeness; unlinked = weaker KYC | `aadhaar_not_seeded` | 🔴 identity (mild) | ⚪ chip sub-line |
| `isDirector` / `directorProfile` | **Moral hazard.** A director (esp. of a defaulting company) is a known adverse-selection signal | `director_default` (existing rule!) | 🔴 occupation (−35 exists) | Show "Director" + company status |
| `isSoleProp` | Tells the rail this is a business owner → **switch to the self-employed view** | (routing, not a flag) | — | Drives persona layout |
| `fatherName` | Name-consistency / KYC match input | feeds `identity_mismatch` | 🟢 (once wired to match) | ⚪ |
| `address` region | Feeds geography group (hotspot/morbidity) | R-geo | 🟢 (geography) | 🟢 |

### Block C — Employment (EPFO) — **salaried authority**
| Field | Underwriter reads it as | Flag | Score | Rail |
|---|---|---|---|---|
| `currentEmployer` + `uan` | Employment verified → income stability | — | 🟢 occupation | 🟢 "EPFO verified" |
| `history[].dateOfJoining` | **Job tenure.** <6-12 months = income-stability risk; 5y = strong | `short_tenure` | 🔴 financial/occupation (mild) | ⚪ "Tenure: N yrs" |
| `history[]` (job hops) | Frequent switching = instability | `job_instability` | 🔴 (mild) | ⚪ |
| absent (Paulson) | **NOT a red flag for a business owner** — pivot to GST/ITR | (persona routing) | — | Hide EPFO group for pure self-employed |

### Block D — Sole Proprietor / GST — **self-employed authority**
| Field | Underwriter reads it as | Flag | Score | Rail |
|---|---|---|---|---|
| `gst[].status = CANCELLED` | **Business wound down / struck off** — material adverse | `gst_alert` (cancelled → high) | 🟢 R-019 | 🔴 chip red |
| `activeAlerts isGstTransactionDelay` | Filing/cash-flow stress | `gst_alert` (delay → moderate) | 🟢 R-019 | amber |
| `gst[].turnovers` (slab) | **Income corroboration** for self-employed (replaces salary) | feeds financial (income cross-check) | 🔴 financial | ⚪ "GST turnover: slab" |
| `businessProfile.dateOfIncorporation` | **Business age.** Incorporated last month (Sabarish: Apr-2026) = thin income history, higher risk | `new_business` (age < threshold) | 🔴 financial | ⚪ "Business age: N mo" |
| `businessProfile.natureOfBusiness` | Occupation hazard class input (manufacturing/factory vs services) | feeds occupation hazard | 🔴 occupation (maps to hazard modifier) | ⚪ show nature |
| `constitutionOfBusiness` | Entity risk context | — | — | ⚪ |
| multiple GSTINs | Multi-entity = complexity/velocity context | — | ⚪ | ⚪ show count |

> **This is your explicit ask:** self-employed and salaried+self-employed MUST show a
> **GST group** on the rail (status + turnover + alerts + business age). Today GST is
> only shown as a sub-item under Financial context and its cancelled-status can be
> hidden by the gst[0] bug.

### Block E — Email intelligence (on Continue)
| Field | Underwriter reads it as | Flag | Score | Rail |
|---|---|---|---|---|
| `isDisposable` | Throwaway email = fraud/contactability | `disposable_email` | 🟢 fraud | 🟢 |
| `isSpam` | Reputation | 🟢 fraud | 🟢 | 🟢 |
| `fraud.risk.score` (inverted) | Overall email fraud | 🟢 fraud | 🟢 | 🟢 |
| `individualMatch.match` | **Email-to-name match** — mismatch = identity concern | `email_name_mismatch` | 🔴 fraud/identity | ⚪ |
| `domainAge` / `whoisInfo.ageYear` | Old corporate domain = trust; brand-new = risk | `young_email_domain` | 🔴 (mild) | ⚪ |
| `isCorporate` / `orgDomainMatch` | Corp email matching employer = strong corroboration | `email_corroborates_employer` (positive) | ⚪ | ⚪ |

---

## 3. The adaptive rail — what shows per persona

Groups rendered on the right rail, **scoped to the applicant type** (your decision:
adapt, not fixed):

**Salaried (Sujeet):**
`Identity/KYC · Employment(EPFO+tenure) · Fraud · Contactability(mobile age/port) ·
Litigation · Geography`
→ No GST/business group (N/A). Income authority = EPFO.

**Self-employed only (Paulson):**
`Identity/KYC · Business & GST(status/turnover/alerts/age) · Fraud ·
Contactability · Litigation(prominent) · Geography · Director(if applicable)`
→ No EPFO group. Income authority = GST + bank statement.

**Salaried + self-employed (Sabarish):**
`Identity/KYC · Employment(EPFO) · Business & GST · Fraud · Contactability ·
Litigation · Geography`
→ **Both** income groups shown. Underwriter weighs salaried stability *and* business
health. This is the richest view.

**New group needed:** a **"Business & GST"** rail group (self-employed personas). It
does not exist today — GST is buried as Financial context. It should be a first-class
chip with its own sub-score reading: GST status, turnover slab, active alerts, business
incorporation age, nature-of-business hazard.

**Director** surfaces as a chip/sub-line only when `isDirector` is true.

---

## 4. New flags this introduces (all feed the existing grey-zone + cluster machinery)

Proposed additions to `SOFT_FLAG`/cluster set (magnitudes parked for underwriter
calibration; the *existence and routing* is the design):

- `mobile_recent_number`, `recent_port`, `mobile_invalid`, `mobile_region_shift`
- `aadhaar_not_seeded`, `email_name_mismatch`, `young_email_domain`
- `short_tenure`, `job_instability`
- `new_business`, (nature-of-business → existing hazard modifier)
- `director_default` — **already exists, just needs the data wired (B1)**

Several of these belong in `CLUSTER_FLAG_TYPES` so that 2+ mild signals together
(e.g. young number + disposable email + brand-new business) escalate to grey-zone —
exactly how an underwriter treats a cluster of weak signals as one strong concern.

---

## 5. Sequencing (what to build, in order)

1. **B1–B4 bug fixes** (map isDirector, all GST entries, tenure path, alerts). Pure
   data-wiring; makes existing rules work. Low risk. **Do first.**
2. **Capture the dropped fields** into the bundle (mobile age/region/validity, business
   age/nature, email domain age, tenure) — capture ≠ score. Enables display + future
   scoring. Low risk.
3. **Adaptive rail + "Business & GST" group** — UI + rail endpoint. Shows the richer,
   persona-correct view. No engine change if we display captured facts.
4. **New flags + scoring** — wire the flags above into the BRE and sub-scorers.
   Magnitudes are `# TODO(underwriting-manual)` — set them WITH the underwriter, per
   CLAUDE.md (do not guess penalty knobs).

Steps 1–3 are safe and high-value (honesty + completeness). Step 4 is the deliberate,
signed-off scoring work.

---

## 6. Open questions for you

- **Q1 — thresholds:** "young number", "new business", "short tenure" all need a cutoff
  (e.g. number < 6 months, business < 12 months, tenure < 6 months). Set now, or park
  with the other underwriter knobs?
- **Q2 — nature-of-business → hazard:** map `natureOfBusiness` strings ("Factory /
  Manufacturing") onto the existing hazard classes? That auto-loads hazardous trades —
  needs the underwriter's trade→class mapping.
- **Q3 — positive signals:** do we let *good* facts (18y-old number, corp email matching
  employer, long tenure) visibly REDUCE risk / raise the score, or only ever penalize?
  Underwriters do give credit; the current engine only penalizes.
