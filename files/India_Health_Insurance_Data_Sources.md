# India Health Insurance Underwriting — Full Data Source List, Use Cases & Verdicts
## Everything in one file — 41 sources, 25 agent-role scenarios, and a verdict on each

**One clarification, stated once:** retail/PSU banks do not underwrite health insurance — they distribute it as bancassurance agents. The underwriting decision belongs to the insurer or the insurtech platform underwriting on their behalf (Artivatic AUSIS, confirmed live at ICICI Prudential Life and Aditya Birla Sun Life).

Every claim is either **[CONFIRMED]** — a direct, named, technical or statistical source found — or **[STANDARD PRACTICE]** — real and in use, but no explicit published figure found, stated honestly.

---

# PART 1 — The 41 Data Sources: Where, Format, Consent, Coverage

**Consent, in brief:** five frameworks impose a mandatory, dedicated consent gate that cannot be satisfied by PAN or general application consent — **Aadhaar** (Aadhaar Act), **CKYC** (now OTP-mandatory per recent circular — corrected from an earlier looser assumption), **all four credit bureaus** (CICRA 2005), **Account Aggregator** (RBI's consent-first framework), and **ABHA/ABDM** (HIE-CM). Everything else runs on general application-stage consent or is public-domain data needing none.

## A. Identity & KYC (8 sources)

| # | Data Source | Where You Get It | Format | Consent? | Coverage & Quality |
|---|---|---|---|---|---|
| 1 | PAN | NSDL/Protean, via KYC vendor | JSON **[CONFIRMED]** | No | **~80 crore PANs issued, ~66 crore Aadhaar-linked [CONFIRMED]**. Near-universal for likely applicants. |
| 2 | Aadhaar e-KYC | UIDAI, via DigiLocker/vendor | JSON+XML+photo **[CONFIRMED]** | **Yes, mandatory** | **~1.4 billion cumulative [CONFIRMED]** — near-total resident saturation, genuinely populated data. |
| 3 | CKYC record | CERSAI | JSON **[STANDARD PRACTICE]** | **Yes, OTP-mandatory** | No aggregate count found. Mandatory only since 2017(individuals)/2021(entities) — partial, skews to recent formal-finance users. |
| 4–6 | Voter ID/Passport/DL | ECI/Passport Seva/Sarathi, via vendor | JSON **[STANDARD PRACTICE]** | No | Fallback documents; no aggregate underwriting-specific coverage figures found. |
| 7 | Liveness+face-match | KYC vendor | JSON+media **[CONFIRMED]** | Yes | 100% — generated fresh at every application, not looked up. |
| 8 | Video KYC | KYC vendor | Video+JSON **[STANDARD PRACTICE]** | Yes | Same as row 7. |

## B. Financial (8 sources)

| # | Data Source | Where You Get It | Format | Consent? | Coverage & Quality |
|---|---|---|---|---|---|
| 9 | EPFO UAN passbook | EPFO | JSON/PDF **[STANDARD PRACTICE]** | Yes | ~29.88 crore cumulative accounts (includes inactive) **[CONFIRMED]**; **formal sector only** — a minority of India's 400M+ workforce. Empty for informal-sector applicants. |
| 10 | GST returns | GSTN | JSON **[STANDARD PRACTICE]** | Yes | Only GST-registered businesses above threshold — minority of self-employed. |
| 11 | ITR/Form 26AS | IT e-filing | PDF/XML **[STANDARD PRACTICE]** | Yes | Only return-filers — smaller than PAN-holder population. |
| 12 | Bank statements via AA | Anumati/CAMSFinserv/OneMoney/Finvu/NADL/Setu/SurakshAA | JSON, Sahamati FI Schema **[CONFIRMED]** | Yes, mandatory | **2.88 billion accounts "enabled," but only ~284.6 million actually linked — under 10% [CONFIRMED]**. |
| 13–16 | Credit reports (CIBIL/Experian/Equifax/CRIF) | The bureaus, via API vendor | JSON+PDF **[CONFIRMED]** | Yes, mandatory | **~420M have a score (~30% of population), only ~170–200M "credit active" (~12%) [CONFIRMED, TransUnion CIBIL]**. |

## C. Health (8 sources)

| # | Data Source | Where You Get It | Format | Consent? | Coverage & Quality |
|---|---|---|---|---|---|
| 17 | ABHA-linked health records | ABDM/HIE-CM | FHIR R4 JSON **[CONFIRMED]** | Yes, mandatory | **~79.71 crore ABHA created (Jul 2025, Lok Sabha) — but person-level linkage across published government health programs runs 15% (NCD, best case) down to ~0.28% (RCH) and ~0.3% (Sickle Cell) [CONFIRMED, Parliamentary written replies]. "Records linked" (65.09 crore) is a record-count, not a people-count — cannot be read as a coverage %.** |
| 18 | NHCX claims data | NHCX/ABDM | FHIR R4 JSON **[CONFIRMED]** | Yes | Same HIP-enrollment gap as row 17. |
| 19–21 | Pharmacy/discharge/lab records | ABDM-linked HIPs | FHIR R4 JSON **[CONFIRMED]** | Yes | Same gap — strong at large chains (Apollo, Fortis, Manipal, Dr. Lal PathLabs), weak/absent at small clinics and most tier-2/3 practitioners. |
| 22 | rPPG facial-scan vitals | rPPG vendor | JSON+media **[CONFIRMED]** | Yes | Generated fresh, but only if offered/consented — uptake not independently confirmed. |
| 23 | Facial BMI/smoking estimation | CV vendor | JSON **[CONFIRMED]** | Yes | Generated fresh at every application, like row 7. |
| 42 | **Pre-policy medical examination** (insurer/TPA-commissioned) | An empanelled diagnostic center or hospital, ordered directly by the insurer or a TPA (e.g. Paramount Health Services, MedAssist, Vidal Health, FHPL, Health India TPA) — **not routed through ABDM at all** | PDF medical report (blood work, ECG, sometimes chest X-ray); some newer TPA platforms also return structured JSON alongside the PDF **[STANDARD PRACTICE]** | Yes, explicit — the applicant must physically attend | **Structurally different from every row above it, and worth reading carefully: this isn't a population-linkage statistic like ABHA, because it's not something an applicant "has" in advance — it's ordered on demand by the insurer for a specific applicant, typically triggered by higher sum-insured or older-age cases. Once ordered, completion is high because it's a mandatory condition of issuance, not a voluntary lookup — this is, in practice, the single most reliable source of real medical data on the entire list, precisely because it doesn't depend on any prior digital record existing at all.** |
| 43 | **Wearable/fitness-tracker data** (Fitbit, Apple Watch, Google Fit/Health Connect, Samsung Health) | Each platform's own developer API — Fitbit Web API, Apple HealthKit (only readable via the insurer's own app on-device, not a direct server pull), Google Fit/Health Connect — typically mediated through an insurer-branded app | JSON via each platform's API **[STANDARD PRACTICE]** | Yes, explicit opt-in via the wearable-linked app | **Confirmed real and live in India** — Aditya Birla Health's "Activ Yuva" (2026) explicitly pulls Apple Watch, Fitbit, Cult, and HealthifyMe data; Niva Bupa, Bajaj Allianz, and Manipal Cigna run comparable programs. **But every confirmed use, across every source found — Indian and global — is for wellness rewards and renewal premium discounts, not the onboarding underwriting decision itself.** RGA and the International Insurance Society both state plainly that the industry broadly has not yet applied wearable data to underwriting despite it being technically available; Munich Re's own 27-carrier US survey found only 7% of carriers use it at all, calling it "largely rejected." One analyst source flags the reason it stays limited: adverse selection, since only the already-fitness-conscious opt in, skewing the population before any analysis happens. **For this project specifically: treat this as a wellness/retention-layer signal for later policy stages, not a source the onboarding agent should weigh — that's not a coverage gap like the others on this list, it's that the industry itself hasn't validated this use case yet, anywhere.** |


## D–H. Cross-Industry, Fraud/Device, Legal, Geography, Employer (16 sources — condensed, unchanged from prior research)

| # | Data Source | Coverage & Quality (brief) |
|---|---|---|
| 24 | IIB motor query | Comprehensive for motor (mandatory insurer reporting); health/life granularity **not screen-confirmed**. |
| 25 | IIB health/life fraud services | Category confirmed, specific output detail **not confirmed**. |
| 26–29 | Mobile vintage/fraud/device/IMEI | High availability (India's mobile penetration is very high); no specific underwriting-use % published. |
| 30 | Digital footprint (RiskSeal) | Lower for older/rural/low-digital-usage applicants — inherently uneven. |
| 31 | Sanchar Saathi/TAFCOP | High — tracks registered mobile connections. |
| 32 | MCA21 directorship | Small minority — only company directors. |
| 33 | Litigation/court records | Uneven — depends on court digitization progress by state/level. |
| 34 | PEP/sanctions | Comprehensive for who's on the list; naturally rare hit-rate. |
| 35 | Defaulter list | Same population as rows 13–16. |
| 36–39 | Occupation/geo/AQI/disease data | Locational, not individual — denser in urban areas. |
| 40 | Employer verification | Same formal-sector-only gap as row 9. |
| 41 | Salary slip/Form 16 | 100% for anyone who has one — but that itself requires formal employment. |

---

# PART 2 — Where the Agent Plays a Role: 25 Scenarios, in Full Detail, Sources Named

Every source below is named in plain language, matching the exact row it comes from in Part 1's table, so nothing needs cross-referencing back to a number. Each scenario states: what a deterministic rule alone can detect, what specifically requires the agent's judgment, and the verdict on real-world readiness — data availability, data quality when present, and an overall call.

## Group 1 — Pre-Existing Disease (PED) Non-Disclosure

**1. Declared "no conditions" vs. an ABHA-linked pharmacy record showing a chronic-condition prescription.** A rule can detect that the mismatch exists the instant both facts are present. What it cannot do is tell whether this is an active, ongoing course of treatment or a one-off prescription from years ago that shouldn't count as a current condition at all — that reading requires the agent. *Availability:* Low. ABHA person-level linkage to real program data runs at best ~15% (the NCD program, the best-performing one found) and falls to well under 1% for others — so this fires only for the minority of applicants who are both ABHA-linked *and* have a populated pharmacy record from an ABDM-enrolled pharmacy. *Quality when present:* High — this arrives as structured HL7 FHIR R4 data through a confirmed government API, not a scanned document. *Verdict:* real and worth building, but size its expected catch-rate against the ABHA-linked minority, not against every applicant who walks through the door.

**2. Declared "non-smoker" vs. the same ABHA pharmacy record showing nicotine-replacement therapy.** Same source, same mechanics: the rule flags the contradiction, the agent decides whether it reflects current concealment or a quit-years-ago record that's simply stale. *Availability, quality, verdict:* identical profile to scenario 1 — low availability, high quality when it does fire.

**3. Declared "no conditions" vs. an ABHA-linked hospital discharge summary showing a recent hospitalization.** The rule detects the presence of an undeclared hospitalization; the agent judges how material it is given the timing relative to the policy purchase. *Availability:* Low, and for a specific, confirmed reason — ABDM hospital enrollment is strong at large private chains (Apollo, Fortis, Manipal, Narayana, Max) but explicitly weak or absent at small clinics and most tier-2/tier-3 hospitals, so a real hospitalization outside a big city may simply never surface here at all. *Quality when present:* High, same FHIR-structured basis. *Verdict:* same as scenarios 1–2.

**4. Declared height/weight vs. a live facial BMI/smoking/gender estimation.** This one is structurally different from the rest of this group. A rule flags a material divergence between what was declared and what the computer-vision estimate returns; the agent judges whether that gap reflects genuine misrepresentation or ordinary measurement variance or a recent real change in weight. *Availability:* High — this is generated fresh, live, during the application itself, the same way a selfie or liveness check is, not looked up from a registry that may or may not have data on this person. Effectively every applicant who completes onboarding produces this signal. *Quality:* Moderate — the underlying accuracy of computer-vision BMI/smoking estimation is its own still-maturing question, separate from whether the data exists at all. *Verdict:* this is the one scenario in the entire PED group genuinely usable at scale today, precisely because it doesn't depend on ABHA linkage at all.

**5. Declared family history "none" vs. an ABHA-linked diagnostic lab report showing a hereditary-marker test on file.** Same mechanics and the same ABHA-dependent low-availability profile as scenarios 1–3.

## Group 2 — Adverse Selection / Timing

**6. The Insurance Information Bureau shows a near-identical-sum-insured application recently declined at another insurer.** The rule would flag the pattern if the data is there; the agent judges whether it reflects routine comparison-shopping or an undisclosed event driving repeated attempts. *Availability:* Uncertain, and I want to be precise about why — IIB's motor-line query mechanism is confirmed real (a named "IIB Query" screen exists), but I never found the same screen-level confirmation for health/life showing sum-insured bands and decline status at this level of detail. The fraud-trigger *category* is confirmed to exist for health/life; this specific *output* is not. *Verdict:* don't build against this level of detail with confidence until the actual vendor or IIB documentation confirms the field exists — the underlying idea is sound, the specific data point is unverified.

**7. An application follows shortly after a competing policy lapsed, per the Insurance Information Bureau.** Same IIB-based uncertainty as scenario 6 — the agent's job (benign non-payment lapse vs. a health-related decline prompting the switch) is real, but the data feeding it isn't confirmed at this granularity.

**8. Cover is purchased during a local disease-outbreak spike, cross-referenced against Air Quality Index data (CPCB/SAFAR) and disease-surveillance data (IDSP/state health departments).** *Availability:* High — this is locational, not individual, data, so it doesn't depend on the applicant having any particular document; it's simply denser in monitored urban areas and patchier in rural ones. *Quality:* Moderate — some of this arrives as PDF bulletins rather than a clean API. *Verdict:* technically available today, but this specific correlation is a genuinely rare real-world pattern — worth having as a rule the agent can reason over, but expect it to fire occasionally, not often.

## Group 3 — Cover-Stacking / Aggregate Exposure

**9. Requested sum insured plus existing in-force cover found via the Insurance Information Bureau exceeds what verified income (EPFO, GST returns, or Income Tax records) supports.** This compounds two separate weak points at once: the same unconfirmed IIB granularity as scenario 6, plus income data that only exists for the formal-sector-employed, GST-registered, or return-filing minority of applicants. *Verdict:* the weakest-grounded scenario in the entire list — real in principle, resting on two things I cannot confirm are both reliably available for the same applicant.

**10. Multiple recent applications detected via velocity signals — mobile number vintage/porting, device fingerprinting, and IMEI-to-SIM pairing.** *Availability:* High — these draw on mobile-network and device data, and India's mobile penetration is very high, so this population coverage is strong regardless of formal financial inclusion. *Verdict:* this is the strongest cover-stacking scenario precisely because it sidesteps the IIB gap entirely — usable at scale today.

**11. Declared "no existing cover" vs. the Insurance Information Bureau showing undeclared policies.** Same IIB-granularity caveat as scenarios 6, 7, and 9.

## Group 4 — Identity / Synthetic Fraud

**12. A mobile-to-PAN mismatch, plus a recently-ported mobile number, plus IMEI-to-SIM pairing showing the same physical device linked to multiple different identities.** The underlying pairing-detection mechanism is confirmed real — both through patent literature on telecom fraud detection and through TMT ID's "Verify" product, built specifically for insurance fraud. The mobile/porting signals themselves have high population coverage. What is *not* independently confirmed is whether a vendor actually exposes "which other identities this device has been used under" as a clean output field — the detection capability is real, that specific granular detail is my extrapolation. *Verdict:* build the detection logic with confidence; don't assume a vendor will hand you a ready-made list of linked identities without confirming that exact field exists in whichever API you integrate.

**13. A CKYC record shows a different address or date of birth than what was declared.** *Availability:* Moderate — CKYC only covers people who've completed a formal KYC with a regulated entity since 2017 (individuals) or 2021 (legal entities), so this skews toward people already active in formal finance. *Quality:* Moderate-to-High when present, since the recent OTP-mandatory-consent rule should keep records reasonably current. *Verdict:* usable for the banked population, simply empty for everyone else.

**14. A borderline (not clean pass/fail) liveness/face-match score, combined with a thin or anomalous digital-footprint score from a vendor like RiskSeal.** The liveness leg is generated fresh at every single application — effectively 100% available. The digital-footprint leg is inherently weaker for older, rural, or otherwise low-digital-usage applicants, which is exactly why RiskSeal-class data was flagged earlier as the legally greyest source on the whole list. *Verdict:* the liveness half is fully solid on its own; the footprint half should only ever serve as corroborating context, never a standalone basis for suspicion — a thin digital footprint is very often just an honest description of someone's life, not evidence of fraud.

## Group 5 — Financial / Affordability Mismatch

**15. Declared income (EPFO, GST, or ITR) plus existing debt load from the credit bureaus (TransUnion CIBIL, Experian, Equifax, CRIF High Mark), measured against the requested sum insured.** *Availability:* Low-to-Moderate — this only has real ground to stand on for applicants who are both formally employed (a minority of India's workforce) and credit-active (roughly 12% of the population, per TransUnion CIBIL's own published data). *Verdict:* a real, well-grounded scenario for a specific, identifiable segment of applicants — not a general-purpose check that applies to most people who'll actually apply.

**16. A large deposit in the applicant's bank statement, via Account Aggregator, shortly before applying for a large policy.** *Availability:* Low — the Account Aggregator framework has 2.88 billion accounts "enabled" nationally, but only about 284.6 million are actually linked, under 10% real conversion. *Quality when present:* High — Sahamati publishes an actual open schema for this data. *Verdict:* not usable at scale today; this is the clearest case in the whole list where sound underwriting logic is bottlenecked purely by real-world adoption, not by anything wrong with the design.

**17. A spike in recent credit inquiries, again via the four credit bureaus.** Same ~30%-have-a-score / ~12%-are-credit-active ceiling as scenario 15. *Verdict:* usable for the credit-active minority, simply not applicable to the rest since there's no data to check.

**18. Declared GST/ITR income versus actual bank inflow patterns seen through Account Aggregator.** This needs both a GST/ITR footprint (itself a minority of the self-employed population) *and* a linked Account Aggregator connection (under 10%) to be present at the same time, for the same applicant. *Verdict:* the weakest financial scenario in the list — it depends on two independently uncommon things both being true simultaneously.

## Group 6 — Occupation / Moral Hazard

**19. Company-directorship records from MCA21 show involvement in a hazardous-class business, while the applicant declared a low-hazard occupation.** *Availability:* Low — MCA21 only covers company directors, a small fraction of any applicant pool. *Quality when present:* High — this is a real government filing, not an inference. *Verdict:* rare, but reliable and directly actionable whenever it does surface.

**20. Litigation records, via the eCourts National Judicial Data Grid, show a case tied to workplace injury or negligence in a hazardous trade, inconsistent with the declared occupation.** *Availability:* Low-to-Moderate — court-record digitization is uneven across states and court levels. *Verdict:* same shape as scenario 19 — a small, real signal when it appears, not something to expect often.

**21. A PEP or sanctions-list hit (RBI caution list, UN/OFAC lists) lands on the applicant's nominee, rather than the applicant themselves.** *Availability:* High for the check itself — these lists are comprehensive for whoever is actually on them, and the check is cheap to always run against every single applicant. The hit-rate, though, is naturally very low by construction. *Verdict:* this isn't a coverage gap at all — it's a rare-by-nature signal that's still worth always checking, since it costs nothing to run and is unambiguous when it fires.

## Group 7 — Application-Form Internal Consistency

**22. A declared nominee relationship that looks implausible against an unusually high sum-insured-to-income ratio, with income again coming from EPFO/GST/ITR.** Same formal-sector-only availability ceiling as the other income-dependent scenarios above. *Verdict:* usable mainly for the formally-employed segment.

**23. A stated purpose-of-cover narrative — e.g., "family protection" — that's inconsistent with the applicant's actual financial and dependent profile, cross-checked against the same income sources.** Same availability profile as scenario 22.

## Group 8 — Multi-Source Corroboration (the case type a single rule structurally cannot catch)

**24. rPPG facial-scan readings showing elevated stress or blood-pressure-adjacent signals, corroborated by an ABHA-linked pharmacy record for an anti-hypertensive medication — where neither signal alone would be strong enough to matter, but together they might.** This is the cleanest illustration of the whole design principle: a rule built on either signal in isolation would either miss this entirely or over-flag constantly; only weighing the two together, in context, tells you anything. *Availability:* Low — the rPPG leg is generated fresh whenever it's offered and accepted, but the pharmacy leg carries the same ~15%-or-lower ABHA linkage ceiling as Group 1, and this scenario needs *both* legs present for the same person. *Verdict:* the strongest proof of why the agent's design is right, and honestly also the scenario most likely to fire least often in practice today, precisely because it needs two independently uncommon things to both be true at once.

**25. A borderline-abnormal diagnostic lab value — not yet at a formal diagnosis threshold — against a declared "no conditions."** Same ABHA-dependent lab-record availability ceiling as scenario 5.

---

# PART 3 — What This Means, Stated Plainly

**Usable at real scale today, not gated by a coverage gap:** Scenarios 4, 8, 10, 12 (detection half), 21. That's roughly **5 of 25** — the ones built on identity/behavioral signals (mobile, liveness, facial estimation, public lists) that are either generated fresh at every application or have near-universal population coverage.

**Real, valuable, but only for a defined minority segment (formally-employed, credit-active, or already ABHA-linked applicants):** Scenarios 1, 2, 3, 5, 13, 15, 17, 19, 20, 22, 23, 25 — roughly **12 of 25**. These aren't wrong to build — they're exactly right for the segment that has the data — but sizing their expected impact against 100% of applicants would overstate what they'll actually catch.

**Weak or unconfirmed today, either because the underlying detail was never independently confirmed (IIB granularity, IMEI's exact output field) or because they require two independently low-coverage sources to both be present:** Scenarios 6, 7, 9, 11, 14 (footprint leg), 16, 18, 24 — roughly **8 of 25**.

This is the honest shape of it: the design pattern (deterministic flagging, agent interprets the tension) is sound and validated across everything researched — but **its real-world hit rate today is bounded hard by India's actual data-linkage rates**, and those rates, especially for ABHA and Account Aggregator, are genuinely low — not "1-2%" across the board as you guessed, but for the ABHA-dependent scenarios specifically, that guess is closer to right than wrong.
