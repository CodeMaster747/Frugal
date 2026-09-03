# Frugal — Software Requirements Specification

**Product:** Frugal — The Intelligent Financial Decision Platform
**Version:** 1.0 (v1 scope = Milestones 0–8)
**Status:** Approved for implementation
**Last updated:** 2026-08-04

---

## 1. Purpose and scope

### 1.1 Problem

Personal finance tools are retrospective. They categorise transactions and render pie charts of money
already spent. They answer *where did it go*, which is a bookkeeping question, and leave the user
alone with the question that actually matters: **what should I do next?**

The decision a user actually faces — "can I afford this ₹85,000 laptop right now, and what does it
cost me if I buy it?" — requires synthesising income stability, savings rate, upcoming bills,
committed EMIs, goal timelines, and cash-flow forecast. No mainstream tool does this. Users answer it
with a gut feeling and a bank balance.

### 1.2 Product thesis

Frugal continuously maintains a model of the user's financial state and uses it to produce **explained
recommendations**. Its defining property is not the feature list — it is that every score, verdict,
and forecast the product displays can be decomposed into the inputs that produced it. A recommendation
the user cannot interrogate is a recommendation they cannot act on with confidence.

### 1.3 Scope of this document

Covers v1 (Milestones 0–8): authentication, financial core, analytics, receipt intelligence,
categorisation ML, financial health, insights, forecasting, and the Smart Purchase Advisor.

Deferred to v1.1 and specified only at the interface level here: Market Intelligence (M9), Decision
Simulator and Notification Engine (M10), production hardening (M11).

---

## 2. Users

| Persona | Description | Primary need |
|---|---|---|
| **Priya — early-career salaried** | 24, first job, irregular saver, considering her first large purchase. | "Can I afford this without wrecking my savings goal?" |
| **Arun — dual-income household** | 34, mortgage + two EMIs, multiple subscriptions, needs cash-flow visibility. | "Will I be short before payday, and what's leaking?" |
| **Meera — freelancer** | 29, irregular income, no fixed payday, over-saves out of anxiety. | "What is my real runway, and is my emergency fund enough?" |

Meera is the hardest case and the best design forcing-function: irregular income breaks naive
averaging, so any metric that assumes a monthly salary is wrong. All engines must degrade honestly
under irregular income rather than emit a confident wrong number.

---

## 3. Functional requirements

Each requirement carries a stable ID. `MUST` = v1 blocking. `SHOULD` = v1 if time permits.
`LATER` = explicitly deferred.

### FR-1 Authentication & account (M1)

| ID | Requirement | Priority |
|---|---|---|
| FR-1.1 | Register with email + password; password hashed with Argon2id. | MUST |
| FR-1.2 | Login issues a short-lived access token (15 min, in memory) and a rotating refresh token in an httpOnly, Secure, SameSite=Lax cookie. | MUST |
| FR-1.3 | Refresh token rotation with reuse detection — a replayed token invalidates the whole family. | MUST |
| FR-1.4 | Logout revokes the refresh-token family server-side. | MUST |
| FR-1.5 | Google OAuth 2.0 sign-in, linking to an existing email account when one matches. | SHOULD |
| FR-1.6 | Profile: display name, base currency, timezone, locale. | MUST |
| FR-1.7 | Rate limiting on login, register, and refresh (per IP and per account). | MUST |
| FR-1.8 | Account deletion removes or anonymises all user data, including stored receipt objects. | MUST |

### FR-2 Financial core (M2)

| ID | Requirement | Priority |
|---|---|---|
| FR-2.1 | CRUD accounts (bank, cash, credit card, wallet, loan) with opening balance and currency. | MUST |
| FR-2.2 | CRUD transactions (income, expense, transfer) with amount, date, merchant, category, account, note. | MUST |
| FR-2.3 | Transfers create a linked pair and are excluded from income/expense aggregates. | MUST |
| FR-2.4 | System category taxonomy (two levels) plus user-defined categories. | MUST |
| FR-2.5 | CSV import with column mapping, preview, and per-row validation before commit. | MUST |
| FR-2.6 | Import is idempotent — a content hash under a unique index prevents duplicate rows on re-import. | MUST |
| FR-2.7 | Budgets per category per period (monthly), with rollover option. | MUST |
| FR-2.8 | Savings goals with target amount, target date, and linked account. | MUST |
| FR-2.9 | Recurring items (salary, rent, EMI, subscriptions) with cadence and next-due date. | MUST |
| FR-2.10 | **Demo data seeder** — one click populates 12 months of realistic transactions so every engine is immediately demonstrable. | MUST |
| FR-2.11 | Soft delete with 30-day restore for transactions and accounts. | SHOULD |

> FR-2.10 is a hard v1 requirement, not polish. Every downstream engine is meaningless on an empty
> database; without seeded data the first-run experience is a blank dashboard.

### FR-3 Analytics (M3)

| ID | Requirement | Priority |
|---|---|---|
| FR-3.1 | Dashboard: net worth, month-to-date income/expense, savings rate, health score, top categories, budget status. | MUST |
| FR-3.2 | Category breakdown with drill-down to the underlying transactions. | MUST |
| FR-3.3 | Income vs. expense over time; cash-flow waterfall. | MUST |
| FR-3.4 | Net-worth and savings-rate trend lines. | MUST |
| FR-3.5 | Budget progress with pace indicator (on track / ahead / over). | MUST |
| FR-3.6 | Arbitrary date-range filter; all aggregates respect it. | MUST |
| FR-3.7 | Aggregates computed server-side in a single query per widget; the client never aggregates raw transactions. | MUST |

### FR-4 Receipt intelligence (M4)

| ID | Requirement | Priority |
|---|---|---|
| FR-4.1 | Upload receipt image (JPEG/PNG/HEIC/PDF, ≤10 MB) via a presigned object-storage URL — bytes never transit the API. | MUST |
| FR-4.2 | Async OCR pipeline: perspective correction → deskew → denoise → adaptive threshold → Tesseract. | MUST |
| FR-4.3 | Extract merchant, date, total, tax, and line items, **each with an independent confidence score**. | MUST |
| FR-4.4 | Fields below the confidence threshold are flagged and routed to a review queue. | MUST |
| FR-4.5 | A receipt is never auto-committed as a transaction unless every required field clears threshold. | MUST |
| FR-4.6 | Manual review UI: original image beside editable extracted fields. | MUST |
| FR-4.7 | Duplicate detection against existing transactions before commit. | MUST |
| FR-4.8 | Job status is observable (queued / processing / needs review / committed / failed). | MUST |

### FR-5 Categorisation ML (M5)

| ID | Requirement | Priority |
|---|---|---|
| FR-5.1 | Merchant-string normalisation (strip terminal IDs, city codes, reference numbers). | MUST |
| FR-5.2 | Deterministic rules layer evaluated first; exact merchant matches bypass the model. | MUST |
| FR-5.3 | TF-IDF (word + char n-gram) → logistic regression classifier over the category taxonomy. | MUST |
| FR-5.4 | Predictions below confidence threshold surface as "uncategorised — needs review" rather than guessing. | MUST |
| FR-5.5 | User corrections are persisted as labelled training data and create a personal rule. | MUST |
| FR-5.6 | Offline eval harness reporting accuracy, macro-F1, and per-class confusion on a held-out set. | MUST |
| FR-5.7 | Models are versioned artefacts; the version that produced each prediction is recorded. | MUST |
| FR-5.8 | Sentence-transformer embeddings. | LATER |

### FR-6 Financial health & insights (M6)

| ID | Requirement | Priority |
|---|---|---|
| FR-6.1 | Composite health score 0–100 from six sub-metrics: savings rate, emergency-fund coverage, debt-to-income, budget discipline, cash-flow stability, financial growth. | MUST |
| FR-6.2 | Every sub-metric exposes its raw value, weight, contribution, band thresholds, and plain-language meaning. | MUST |
| FR-6.3 | Score is computed from a **published, versioned rubric** — no opaque model. | MUST |
| FR-6.4 | Insufficient data yields a partial score with explicit `caveats`, never a fabricated number. | MUST |
| FR-6.5 | Health snapshots persisted over time to render a trend. | MUST |
| FR-6.6 | Rule-based insight engine detecting: category spend deltas, budget breaches, new recurring charges, subscription creep, savings-rate change, anomalous transactions, emergency-fund shortfall. | MUST |
| FR-6.7 | Insights are ranked by materiality (₹ impact × confidence), deduplicated, and rate-limited per period. | MUST |
| FR-6.8 | Insights are dismissible; dismissals suppress recurrence of that insight type for a cooling period. | SHOULD |

### FR-7 Cash-flow forecasting (M7)

| ID | Requirement | Priority |
|---|---|---|
| FR-7.1 | Forecast projected balance at 30 / 60 / 90 days with confidence intervals. | MUST |
| FR-7.2 | **Tiered method selection by data availability**: `< 60 days` → recurring-item projection; `60–180 days` → EWMA + seasonal-naive; `≥ 180 days` → Prophet. | MUST |
| FR-7.3 | Response always names the `method` used, the `data_window`, and a calibrated `confidence`. | MUST |
| FR-7.4 | Recurring-transaction detection (periodicity + amount stability) feeds known inflows/outflows into every tier. | MUST |
| FR-7.5 | Shortfall detection — flag dates where projected balance crosses a user-defined floor. | MUST |
| FR-7.6 | Forecaster sits behind a `Forecaster` port so tiers are independently testable and swappable. | MUST |
| FR-7.7 | Backtesting harness reporting MAPE per tier on held-out windows. | MUST |

### FR-8 Smart Purchase Advisor (M8) — flagship

| ID | Requirement | Priority |
|---|---|---|
| FR-8.1 | User submits a purchase intent: product (searched or free-text) plus price, or picks from the seeded catalogue. | MUST |
| FR-8.2 | Prices are retrieved through the `PriceProvider` port. v1 adapters: seeded catalogue + manual entry. | MUST |
| FR-8.3 | Compute an **Affordability Score (0–100)** from: liquid savings after purchase, emergency-fund coverage post-purchase, savings-rate impact, committed outflows in the horizon, forecast balance trough, goal delay, and existing debt ratio. | MUST |
| FR-8.4 | Emit one of four verdicts: `BUY_NOW`, `BUY_ON_EMI`, `WAIT`, `NOT_RECOMMENDED`. | MUST |
| FR-8.5 | **Purchase Impact Simulation** — before/after comparison of balance trajectory, savings rate, emergency-fund months, health score, and goal ETA. | MUST |
| FR-8.6 | On `WAIT`, estimate the date the purchase becomes affordable, derived from the forecast. | MUST |
| FR-8.7 | On `BUY_ON_EMI`, model tenure options and show total interest cost against the cash price. | MUST |
| FR-8.8 | Suggest cheaper alternatives from the catalogue when the verdict is negative. | SHOULD |
| FR-8.9 | Every verdict returns a full `Explanation` — factor list with weights and signed contributions. A verdict without its factors is a defect. | MUST |
| FR-8.10 | Opportunity cost stated in concrete terms (goal delay in days, emergency-fund months lost). | MUST |

### FR-9 Deferred to v1.1

| ID | Requirement | Milestone |
|---|---|---|
| FR-9.1 | Market Intelligence: wishlist, price history, drop alerts, lowest-recorded-price tracking. | M9 |
| FR-9.2 | **Seller Reliability Score** — observable signals only (rating, rating volume, return window, warranty type, fulfilment type, price deviation from median). Rubric published in-product. | M9 |
| FR-9.3 | Decision Simulator for arbitrary scenarios (vacation, vehicle, job change). | M10 |
| FR-9.4 | Notification engine: budget, bill, renewal, goal-milestone, forecast-shortfall alerts. | M10 |

> **Scope correction from the original brief.** "Review Authenticity" and "Scam Risk" scoring are
> removed. Fake-review detection is not honestly achievable at this data scale, and publishing a
> "scam risk" label about a named commercial seller is a defamation exposure. FR-9.2 replaces it with
> a reliability score built only on observable, defensible signals — of which an outlier-low price
> versus market median is the honest proxy for the risk the original requirement was reaching for.

---

## 4. Non-functional requirements

### NFR-1 Performance
- p95 API latency < 300 ms for reads, < 500 ms for writes, excluding async jobs.
- Dashboard first contentful paint < 1.5 s on a 4G connection.
- OCR job completes < 30 s p95 for a 5 MP image.
- Forecast generation < 3 s p95; results cached until the underlying data changes.
- Any operation exceeding 2 s runs as a Celery job with an observable status, never a blocking request.

### NFR-2 Security
- Argon2id password hashing; access tokens in memory only, refresh in httpOnly cookies (NFR rationale: localStorage tokens are readable by any XSS payload).
- **Tenant isolation enforced in a base repository class**, not by per-query discipline. Every user-owned query is scoped by `user_id` at the data-access layer; a query that bypasses it fails a test.
- Object storage private with anonymous access blocked; all access via short-lived presigned URLs,
  generated per request and never stored.
- Cloud identities scoped to least privilege; **no long-lived keys on the host** — the VM
  authenticates as a managed identity, and shared storage account keys are disabled outright.
- Secrets from environment/parameter store, never committed. `.env.example` documents shape only.
- Input validated by Pydantic at every boundary; SQLAlchemy parameterisation throughout.
- Security headers, CORS allowlist, CSRF protection on cookie-authenticated mutations.
- Dependency scanning and secret scanning in CI.

### NFR-3 Reliability & data integrity
- Money is `Decimal` / `Numeric(18,2)` end to end. Floating-point money is a defect.
- All timestamps stored UTC; timezone conversion at the presentation boundary only.
- Ingestion is idempotent by content hash.
- Celery tasks are idempotent and retry with exponential backoff; failures land in a dead-letter table with the error preserved.
- Migrations are forward-only and reversible; no destructive migration without an explicit backfill step.

### NFR-4 Observability
- Structured JSON logging with request ID and user ID on every line.
- Request ID propagated through Celery tasks so a job traces back to its originating request.
- Centralised log collection with alerts on error rate, host availability, disk, and CPU credits.
- `/health` (liveness) and `/health/ready` (DB + Redis reachability) endpoints.

### NFR-5 Quality
- Backend ≥ 80% line coverage; domain/scoring logic ≥ 95%. Coverage on scoring engines is what makes the rubrics safe to change.
- `ruff`, `mypy --strict` on `app/`, and an **import-linter contract enforcing module boundaries** — all blocking in CI.
- Integration tests run against a real Postgres and Redis (compose locally, CI `services:`), never SQLite; behaviour must match production.
- Every AI module ships an offline eval harness with fixture data and a recorded baseline metric.
- Playwright smoke test per milestone covering that milestone's user journey.

### NFR-6 Usability & accessibility
- WCAG 2.1 AA: contrast, focus visibility, keyboard operability, semantic landmarks, screen-reader labels.
- Colour is never the sole carrier of meaning — chart series and status states carry text or shape too.
- Dark and light themes, respecting `prefers-color-scheme` with a manual override.
- Responsive from 360 px; primary flows fully usable on mobile.
- Every async operation exposes loading, empty, error, and partial-data states. Empty states offer the demo seeder.

### NFR-7 Cost
- Runs at $0/month on free tiers (see §5). No architectural decision may introduce a paid dependency without an explicit ADR.

---

## 5. Constraints and assumptions

### 5.1 Technical constraints

| Constraint | Consequence |
|---|---|
| Single VM = 1 GB RAM | Postgres **cannot** be co-hosted; Neon hosts it. Redis can be, capped at 64 MB and unpersisted, because it holds only regenerable state. Refusing to put the system of record on the disposable host is what makes the free tier viable. |
| Prophet toolchain ≈ 400 MB | Prophet loads lazily, in the worker only, and only for the ≥180-day tier. It is never imported by the API process. |
| torch / sentence-transformers ≈ 2 GB | **Excluded from v1.** TF-IDF meets FR-5 requirements at a fraction of the footprint. |
| Tesseract accuracy ≈ 60–70% on thermal receipts | Human-in-the-loop review (FR-4.4–4.6) is a functional requirement, not a fallback. |
| Neon free tier pools connections | Application connects via the pooled endpoint with `asyncpg` + SSL; Alembic must use the direct endpoint. |
| Python 3.14 on host vs. 3.11 target | All execution is containerised; the host interpreter is never used for app code. |

### 5.2 Product constraints

- **No scraping of commercial retailers.** Continuous scraping of Amazon, Flipkart, Croma, or Reliance Digital violates their terms, fails against anti-bot defences, and creates liability for a commercial product. The `PriceProvider` port isolates this decision so a compliant adapter (affiliate API or licensed data provider) can be introduced later without touching product logic.
- **No LLM in the decision path.** Recommendations must be reproducible and auditable. LLMs may later phrase explanations, never compute them.

### 5.3 Assumptions

- Single base currency per user for v1 (INR default); the schema carries `currency` per row so multi-currency is additive, not a migration.
- No bank-account aggregation (Plaid/Account Aggregator) in v1 — data arrives via manual entry, CSV import, and receipts.
- Single-region deployment.

---

## 6. Out of scope for v1

Bank aggregation · investment portfolio tracking · tax filing · multi-user or shared households ·
mobile native apps · real-time collaboration · actual payment execution · credit-score integration ·
LLM chat interface.

---

## 7. Acceptance criteria for v1

v1 is done when a reviewer can, against a deployed environment:

1. Register, log in, and land on an empty dashboard offering the demo seeder.
2. Seed demo data and see a populated dashboard within 5 seconds.
3. Import a CSV, re-import the identical file, and observe **zero duplicate rows**.
4. Upload a receipt photo, watch job status transition, correct a low-confidence field in the review queue, and commit it as a transaction.
5. See auto-categorised transactions, correct one, and see the correction persist as a rule.
6. Read a health score where every one of the six sub-metrics displays its value, weight, and contribution.
7. Read at least three ranked, non-duplicated insights.
8. Generate a 90-day forecast that names its method, data window, and confidence.
9. Ask the advisor about a large purchase and receive a verdict with a **complete, non-empty factor list**, an impact simulation, and — if `WAIT` — a dated affordability estimate.
10. Toggle dark/light, navigate the primary flow by keyboard alone, and use it at 360 px width.

**Global invariant:** any engine response returning a verdict or score with an empty `factors` array
is a bug, regardless of whether the number itself is correct.

---

## 8. Traceability

| Module | Requirements | Milestone | Verified by |
|---|---|---|---|
| Auth | FR-1 | M1 | Unit + integration + Playwright login flow |
| Financial core | FR-2 | M2 | Integration + idempotency test + seeder test |
| Analytics | FR-3 | M3 | Aggregation unit tests + visual smoke |
| Receipts | FR-4 | M4 | Pipeline unit tests + labelled-fixture eval |
| Categorisation | FR-5 | M5 | Offline eval harness (accuracy / macro-F1) |
| Health & insights | FR-6 | M6 | Rubric unit tests + golden-file explanations |
| Forecasting | FR-7 | M7 | Backtest harness (MAPE per tier) |
| Advisor | FR-8 | M8 | Scenario-matrix tests + explanation completeness assertion |

---

*Next: [02-architecture.md](02-architecture.md)*
