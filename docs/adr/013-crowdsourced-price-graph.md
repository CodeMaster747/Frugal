# ADR-013 — A crowdsourced price graph, and what may cross the line from a receipt

**Status:** Accepted · **Date:** 2026-08-26

## Context

ADR-008 chose SerpAPI as the only surviving source of live retail prices, and recorded its own
verdict on the result: *"~200 searches a month across all users is roughly eight a day. At any real
adoption it is exhausted in hours on day one."* That is not a price feature; it is a demo of one.

Meanwhile the product already receives, every day, exactly the data a price feature needs, and
throws almost all of it away. A committed receipt is a **verified, dated, located record of what a
real person actually paid at a real shop.** It is better than a search API on every axis that
matters — it is current, it is local, it includes the small stores no API indexes, and it is not
metered.

The reason it was thrown away is a good one. **Receipts are PII.** They record who bought what,
where, when, and in what combination. A price graph built carelessly from them is a surveillance
database with a shopping feature attached, and "we only publish the prices" is not a defence: item
co-occurrence on one receipt is a fingerprint, and a price nobody else confirmed is a statement
about the one person who bought it.

So the question this ADR answers is not "should we use receipts" but **exactly what may cross the
line, and what structurally cannot.**

## Decision

**Build a shared price graph from verified receipts and local contributions. Draw the line at the
basket, enforce it with the schema rather than with discipline, and pay people for contributing.**

### 1. What crosses, and what never does

Stays with the uploader, permanently, in tenant-scoped tables:

> the image and its object key · `raw_text` · every bounding box · the payment method · the receipt
> total, tax and subtotal · the receipt id · the time of day · **and the basket**

Crosses into `price_observations`:

> canonical item · store · unit price · currency · pack size and unit · `observed_on` **as a DATE,
> never a timestamp** · source · confidence · a contributor hash

The basket is the load-bearing exclusion. Publishing which items appeared on one receipt together
re-identifies people with no name attached anywhere, and it is the exclusion most easily lost to a
later "it would be useful to know what people buy together". So the shared table carries no
`receipt_id`, no line number and no timestamp, and the test asserts it against the schema rather
than against the code.

### 2. Pseudonymous, and said so

`contributor_hash = HMAC(pepper, user_id | store_id | observed_on)[:16]`, under
`UNIQUE (canonical_item_id, store_id, observed_on, contributor_hash)`.

The store and the date are *inside* the HMAC, so the value is not linkable across shops or across
days — an attacker holding the whole table cannot assemble one person's purchase history from it.
The pepper lives in `Settings` and never in the database, so a dump alone links nothing.

**This is pseudonymisation, not anonymisation.** An operator holding the pepper can test "did user X
buy item Y at store Z on date D". Writing "anonymous" in the docs would be false, so it is not
written. Erasure nulls the hash, and *then* the surviving row is genuinely anonymous.

The uniqueness constraint is the other half of the same value: one contribution per person per item
per shop per day, enforced by Postgres rather than by a check somebody can forget to write at a new
call site.

### 3. k-anonymity on read

A store-level price is served only once at least `min_contributors_for_display` (default 2, prefer
3) distinct contributors have reported it inside a 30-day window.

A single observation says *"exactly one person shopped here and bought this"*. That is a sentence
about a person, and no aggregation over one row makes it otherwise.

The floor costs the uploader nothing they had: they always see their own observation through their
own tenant-scoped receipt, so the feature is useful on day one against an empty graph.

### 4. Item identity without an LLM

ADR-005 forbids an LLM in the decision path, and the TF-IDF classifier is the wrong tool anyway: it
maps a string onto one of 23 *fixed* categories, while this is open-vocabulary clustering over a
label set that grows with every new product. (It *is* the right tool for assigning a category once a
canonical item exists, and that is where it is used.)

Four layers, each running only when the one before fails: deterministic rules → exact alias →
pg_trgm similarity *within a block* → a human.

**The rule that keeps the graph honest is the blocking.** "AMUL TAAZA 500ML" and "AMUL TAAZA 1L"
have trigram similarity around 0.93 and are a different product at a different price. Blocking on
(brand, pack size, pack unit) happens **before** similarity is computed, never after. And because
trigram similarity is not transitive — chaining A~B and B~C yields groups where A and C are
unrelated — auto-merge may only ever attach an *alias* to an existing canonical. Canonical-to-
canonical always waits for a human.

### 5. The promotion rule, and the filter that does the work

Eight conditions, run in a worker, never in the request. The fourth is the strongest and costs
nothing:

> **the line items must sum to the receipt total within tolerance.**

A receipt whose arithmetic does not work had a bad read — and *which* line was misread is exactly
what is unknown, so the whole receipt is refused rather than the one line that looks wrong.

The confidence bar to publish is 0.80, deliberately above OCR's 0.75: showing a user their own
reading is a lower-stakes act than telling other people what a shop charges.

### 6. Cross-user duplicate detection

Points on crowdsourced data invite the oldest attack available: upload one receipt from several
accounts. Per-user deduplication cannot see it, because each upload is that user's first.

Two hashes, because one is not enough. `content_hash` — a digest of the facts on the paper, with the
line totals **sorted**, because OCR line order varies between reads of one receipt — does the
detecting. A 64-bit DCT `phash` of the *preprocessed* image corroborates, and is never used alone:
binarising is also what makes two *different* receipts from one shop look alike, which is the case a
price graph sees most often.

`receipt_fingerprints` is global and names nobody. The only link to a person runs the other way,
from a tenant-scoped `receipts.fingerprint_id` the asking user cannot read. **That asymmetry is the
privacy property.**

On a hit, the second uploader's receipt still commits into their own ledger — a shared household
bill is a legitimate duplicate. What is refused is promotion and points. The message says *"already
counted"*, never *"someone else uploaded this"*: the second phrasing is both an accusation and a
leak.

### 7. Points, append-only

An award is reversed by a compensating row, never by an update or a delete. Points are earned for
exactly the kind of thing people farm, so a reversal has to be possible without destroying the
evidence of what was reversed — and a balance that is a `SUM` over immutable rows cannot drift out
of step with its own history.

Awards fire only after server-side verification, only from a worker, and only under a per-user daily
cap on *awards* rather than on points — a cap on points would make the cheapest contribution the
most efficient way to farm, which is precisely backwards.

Redemption returns **501** and says so. The catalogue exists anyway, because a number that goes up
and buys nothing, with no indication of what it might ever buy, is a counter rather than a reward.

### 8. Two rights, two endpoints

- **Erasure anonymises.** Deleting an account nulls the contributor hash. The price survives,
  because a price at a shop on a day is a fact about the shop, and deleting it would silently
  degrade what every other user sees and make the graph a function of churn.
- **Retraction removes.** `DELETE /pricegraph/contributions` sets `retracted_at`.

These are genuinely different rights and the product names both, rather than offering one and hoping
nobody wants the other.

## Consequences

### Good

- A price source that is current, local, unmetered, and gets better with use — the opposite of
  ADR-008's, which is stale, national, capped, and gets worse with use.
- Small shops are representable at all. No commercial API indexes the corner store, and it is the
  corner store a household's weekly spend actually goes through.
- Every privacy claim is a schema property or a test, not a convention: no `user_id` on the shared
  table, a unique constraint for one-per-day, a `HAVING` clause for the floor, an asymmetric link
  for the fingerprint.

### Bad, and accepted

- **The graph is empty on day one** and useless until several people shop in the same place. The
  k-anonymity floor makes that worse, deliberately. Mitigated only by the uploader always seeing
  their own data.
- **Pseudonymous is not anonymous.** An operator with the pepper can test a specific guess. Stated
  rather than glossed; the mitigation is that the pepper is never in the database and is nulled on
  erasure.
- **The pepper cannot be rotated casually.** Rotation silently invalidates the one-per-day
  constraint for every existing row. `pepper_version` makes a rotation *representable*; it does not
  make it cheap.
- **An absent quantity is read as one unit.** Receipts state a quantity only sometimes, and
  requiring one skipped almost every line on almost every receipt — which is how the rule was
  written first, and why nothing reached the graph until it was caught. The assumption is
  documented, and a median over several contributors absorbs the shopper who bought two.
- **Item normalisation will be wrong sometimes.** The brand table is hand-maintained, and a brand it
  does not know becomes part of the head noun. The merge-candidate queue is the release valve, and
  it needs a human.
- **`receipt_promotions` is a fourth place a receipt is referenced.** Deleting a receipt now
  cascades further than it did.

## Alternatives rejected

| Option | Why not |
|---|---|
| Publish every observation, no floor | A price one person reported is a statement about that person. No aggregation over a single row fixes that. |
| Store `user_id` on the observation and filter on read | Puts a per-person purchase history one `SELECT` away, protected by application code. The whole point is that it should not be expressible. |
| Hash the user id alone as the contributor key | Constant per user across every shop and every day, so the table itself reconstructs a purchase history. Putting the store and date inside the HMAC is what prevents that. |
| Delete contributions on account deletion | Silently degrades the price every other user sees, and makes the graph a function of churn rather than of what shops charge. Anonymise instead, and offer retraction separately. |
| Geocode receipt addresses at upload | A metered external call on the path of every receipt — the thing ADR-008 spent a milestone establishing not to build. Printed Indian retail addresses also geocode poorly. Offline OSM import plus a user pin instead. |
| PostGIS | A bounding box on two indexed NUMERIC columns plus Haversine in Python answers the only question asked (a map viewport). `cube` + `earthdistance` is the upgrade path if that stops being true. |
| An LLM to normalise item names | ADR-005. It would also be a metered call per receipt line, which is the worst possible shape for this workload. |
