# ADR-008 — Metered external dependencies pay nothing, or they stop

**Status:** Accepted · **Date:** 2026-08-17

## Context

Frugal runs at $0/month on free tiers, and that has until now been achieved by not depending on
anything metered. The offer-search feature breaks that: finding where a product is actually cheapest
requires live retail data, and every source of it is priced per call.

The survey, as of August 2026:

| Source | Status |
|---|---|
| Google Custom Search JSON API | Closed to new customers; hard shutdown 2027-01-01 |
| Brave Search API | Free tier withdrawn February 2026; card required, auto-bills |
| Bing Search API | Retired August 2025 |
| Amazon Product Advertising API | Switched off 2026-05-15; successor requires existing sales |
| Scraping retailers | Rejected by ADR-004 |
| Scraping Google | Worse than ADR-004's case on every axis |
| **SerpAPI `google_shopping`** | **Permanent free plan, no credit card, hard-stops** |

"Free tier" is a promise about volume, not a wall. Exceeding one either bills the account or kills
the service, and the first is worse — a surprise invoice on a hobby project is the failure mode that
ends it.

NFR-7 requires an ADR before a paid dependency. SerpAPI's free plan is not paid, so strictly none is
needed. This one is written anyway, because the decision being recorded is not "use SerpAPI" — it is
**how this codebase permits a metered dependency at all, and what guarantees stop it becoming a
bill**. That outlives the vendor, and the next person reaching for a metered API needs to find it.

## Decision

**Zero recurring cost is an architectural invariant, enforced by a ledger, not by care.**

A metered provider may be integrated only if all five hold:

1. **It has a free tier that hard-stops rather than auto-billing.** A provider that bills on overage
   is out, regardless of how carefully we intend to use it.
2. **No credit card is on file.** The strongest guarantee is structural: a provider that cannot
   charge us will not.
3. **Calls pass through `app/core/quota.py` before any network access**, and the gate lives *inside
   the adapter*, above the HTTP client — never at the call site.
4. **Our caps sit below the provider's**, so our stop fires first and theirs is a second line of
   defence rather than the plan.
5. **Exhaustion degrades, and says so.** The feature falls back to its fake, the response carries
   `service_status: "paused"` and a caveat naming a contact, and no other feature fails.

### The ledger

Reservation is one atomic statement:

```sql
INSERT INTO external_quota_usage (...) VALUES (..., 1, :cap, ...)
ON CONFLICT (provider, period_kind, period_key) DO UPDATE
   SET used = external_quota_usage.used + 1, last_call_at = now()
 WHERE external_quota_usage.used < external_quota_usage.cap
RETURNING used;
```

No row returned means exhausted. Correct under concurrency by construction, with no
read-modify-write to race — the same reasoning as the Lua script in `core/rate_limit.py`.

Three windows, checked tightest-first: per-user daily, global daily, global monthly. The daily cap
exists so one bad day cannot eat the month; the per-user cap so the first person to open the panel on
the first of the month does not spend everyone's month.

### Three rules that look like details and are not

**Postgres is the authority, not Redis.** This project's policy is that Redis holds only regenerable
state (`core/cache.py`, `core/jobs.py`). A quota counter is the one piece of state whose loss costs
money: an evicted key restarts the count at zero and the next thousand calls go through. The offer
cache is in Postgres for the same reason — what a cache miss costs here is *quota*.

**Reserve before the call, not after.** A crash mid-call must still consume the quota, because the
provider counted it. Optimism here is how a free tier becomes a bill. A failed HTTP request
therefore does *not* refund; a flapping endpoint would otherwise spend the month twice over.

**Never call from a background sweep.** No beat task, no prefetch, no wishlist refresher. At a couple
of hundred searches a month for the whole deployment, one automatic call is the entire allowance.

## Consequences

**Positive.** The zero-cost constraint is a property of the system rather than a habit. A future
metered dependency inherits the mechanism and the rules. The default provider is the simulator, so CI,
local development, and any deployment without a key are *incapable* of spending money.

**Negative.** ~200 searches a month across all users is roughly eight a day. At any real adoption it
is exhausted in hours on day one, and every subsequent user sees the paused banner. This is not
hidden: the honest framing is the one ADR-004 already makes for `PriceProvider` — the feature ships on
the simulator, and the live adapter demonstrates that a real one is a configuration change. The ledger
is what makes opting in safe.

**Negative.** The ledger is one more table and one more failure mode on a path that could have been a
direct call.

## Alternatives rejected

**Trust the provider's own limit.** It is their limit, enforced by their billing system, and the
common design is to serve the call and invoice for it. Relying on someone else's stop for our
constraint means discovering the constraint failed by receiving an invoice.

**A counter in Redis.** Faster and simpler, and wrong for exactly one reason: eviction. Every other
counter in this system can be rebuilt; this one cannot, and its loss is denominated in money.

**Check the quota at the call site.** Reads better and puts the "we are paused" branch next to the UI
that renders it. Rejected because a gate a caller must remember is not a gate — the second call site
is written by someone who has not read the first. `test_quota_is_enforced_inside_the_adapter` scans
for the natural refactor that would undo this.

**Refund the quota on a failed request.** Feels fair. It is the exact behaviour that lets a provider
outage drain the month: every failure returns the allowance, and the retry spends it again.
