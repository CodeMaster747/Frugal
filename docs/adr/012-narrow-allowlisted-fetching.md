# ADR-012 — Narrow, allowlisted, robots-honouring fetching

**Status:** Accepted · **Date:** 2026-08-26 · **Narrows:** [ADR-004](004-ports-and-adapters.md)

## Context

ADR-004 records a rejection in unusually direct terms:

> the original brief specified scraping Amazon, Flipkart, Croma, and Reliance Digital. This violates
> their terms of service.

`ports.py::PriceProvider` says the same thing in its own docstring — *"gets an IP banned within
hours"* — and `docs/01-srs.md` §5.2 adds that it *"fails against anti-bot defences, and creates
liability"*. Every one of those statements is true, and none of them is being withdrawn.

What has changed is the shape of the request. The brief was to reproduce four retailers' catalogues
by scraping their product pages. That is a fundamentally different activity from fetching a public
JSON endpoint that a host has explicitly told us, in its own robots.txt, that we may fetch.

There is also new pressure. ADR-008 records its own verdict on the alternative: SerpAPI's free tier
is *"roughly eight [searches] a day. At any real adoption it is exhausted in hours on day one."*
ADR-013 answers most of that with a crowdsourced graph, but a crowdsourced graph is empty until
people use it, and it will never cover national e-commerce pricing at all.

**A code comment the code has quietly outgrown is how the next reader ends up right and the code
ends up wrong.** So rather than adding a scraper beside a docstring that says scrapers were
rejected, this ADR narrows the earlier rule and the docstring is amended to point here.

## Decision

**Fetch only from an allowlist of hosts, only on paths robots.txt permits, rate-limited per host,
failing closed on any uncertainty — and ship with the allowlist empty.**

Concretely, six conditions. Each is enforced by code or by a test, not by intent.

### 1. The allowlist is checked-in code, not configuration

`ALLOWED_HOSTS` in `app/adapters/offers/scraper/hosts.py` maps a host to a rule that names its
extractor, its permitted path prefixes, its minimum request interval and its daily cap.
`scraper_enabled_hosts` can turn a host **off**; it cannot turn on one with no rule.

That asymmetry is the point. An operator who could point the crawler at an arbitrary host by
setting one environment variable could point it at anything, and adding a host means writing an
extractor, reading that host's robots.txt and terms by hand, and deciding the answer is yes — which
is a judgement, and judgements belong in a file somebody has to edit.

**`ALLOWED_HOSTS` ships empty.** A fresh deployment is incapable of crawling anyone, exactly as it
is incapable of spending money.

### 2. robots.txt is obeyed, and unreadable means no

| outcome | decision |
|---|---|
| 404 | **allow** — RFC 9309: no robots.txt means no restrictions |
| 2xx | obey it, honouring `Crawl-delay` |
| timeout, 5xx, connection error | **deny**, 24 hours |
| 403 or 429 on robots.txt itself | **deny**, 7 days |

Getting that table backwards is the entire risk of the feature, which is why it is a table.

Two implementation notes that are load-bearing rather than incidental. **`RobotFileParser.read()`
is never called** — it performs its own `urlopen` with no timeout and follows redirects; the bytes
are fetched with an explicit timeout and handed to `parse()`. And the policy is cached in
**Postgres, not Redis**: an evicted *allow* costs one wasted fetch, but an evicted *deny* means
crawling a path we were told not to, which is a compliance failure rather than a performance one.

### 3. Politeness is a ledger, and it is a sibling of the quota ledger

`app/core/host_budget.py` deliberately mirrors `app/core/quota.py` in structure and differs in
invariants. `quota.py` is a count per period and can express "200 calls this month". It cannot
express either thing politeness actually needs: **spacing** ("at least ten seconds since the last
request to this host" is an instant, not a count) and **cooldown** ("this host said 429, stay away
until T").

Cramming those into `external_quota_usage` would put non-quota semantics into the table ADR-008
describes and make that ADR's own rules — reserve before the call, no refund, never from a sweep —
false of half its rows. So: two tables, one atomic `UPDATE … WHERE … RETURNING` each.

Not tenant-scoped. Politeness to a host is owed by the deployment, never by the user who happened
to type the query.

### 4. The gate order is the safety argument

```
our path narrowing  →  robots.txt  →  host budget  →  HTTP
```

A path we were told not to fetch must not even consume a request slot. Reversing the middle two
would spend the day's allowance discovering we were not allowed to ask.

An ordering cannot be pinned by a behavioural test without a real host to be impolite to, so it is
pinned by a source scan — `test_robots_is_checked_before_the_budget_is_spent` and
`test_the_http_call_is_below_the_gates_not_beside_them`, the same technique
`test_quota_is_enforced_inside_the_adapter` already uses for ADR-008.

### 5. It runs in the worker, and it is bounded there

Zero network in the request path. `ScraperCacheSource` reads what has already been fetched; on a
miss it enqueues a crawl and returns empty **with a reason** (`status="warming"`), which is the
second reason `SourceResult` carries a status rather than being a bare list.

Async, despite ADR-006 — that ADR makes workers synchronous *because OCR and Prophet are
CPU-bound*, and a crawler is I/O over several hosts with enforced spacing, which is the opposite
case. It follows `tasks/market.py` line for line.

Bounded rather than isolated, and this is worth being honest about: routing to a `scrape` queue
buys nothing while one worker at concurrency 1 consumes every queue, and a second worker does not
fit in the 1 GB budget. So the task carries `soft_time_limit=120` against the global 540, plus a
per-run request cap. That actually bounds the damage; a separate queue would only look like it did.

### 6. Nothing is emitted without validation

A silently broken extractor returning garbage prices is worse than one returning nothing, because
the garbage lands in a graph users trust. Every offer must have a non-empty title, a positive price
below a ceiling, and a price within 10× of its peers — which catches the two misparses that
actually happen, the EMI instalment and the star rating. Neither is caught by a schema; both look
like perfectly good numbers.

Five consecutive parse failures put a host in a seven-day cooldown, because at that point the page
has changed and we are guessing.

## Consequences

### Good

- The offer chain has a link that costs nothing and is not capped at eight searches a day.
- Everything the earlier decision was protecting against is still protected against: unrestricted
  retail scraping remains rejected, and remains rejected for the reasons ADR-004 gave.
- The default configuration cannot make a single outbound request, so the risk of the feature is
  opt-in per deployment and per host.

### Bad, and accepted

- **ADR-004's blanket claim is now conditional**, and a future reader has to hold two documents in
  mind rather than one. The `PriceProvider` docstring is amended to point here so that at least the
  code says so.
- **A host can withdraw permission at any time**, by robots.txt or by a 403, and the answer is to
  stop. That is a feature whose availability somebody else controls — which is exactly the property
  ADR-008 disliked about metered APIs, arriving by a different route.
- **An extractor breaks when a page changes**, silently, and the cooldown limits the damage rather
  than preventing it. `HOST_RULES_VERSION` is what makes a bad batch retractable without guessing
  at dates.
- **`httpx` becomes a real dependency** of the worker image. It was previously only in the `dev`
  extra, imported lazily by the SerpAPI adapter.
- **Legal review is not a code review.** Adding a host to `ALLOWED_HOSTS` is a decision about
  somebody else's terms of service, and no test in this repository can make it for you.

## Alternatives rejected

| Option | Why not |
|---|---|
| Keep ADR-004 absolute and rely on SerpAPI alone | ADR-008's own numbers say that is eight searches a day across all users. |
| Headless browser against retail product pages | Precisely what ADR-004 rejected, and every one of its reasons still holds: ToS, anti-bot arms race, IP blocks, liability, and a proxy budget that breaks NFR-7. |
| Hosts configurable by environment variable | Turns "which sites may we crawl" into a deployment setting. The permission and the extractor have to move together. |
| robots.txt cached in Redis | An evicted deny means crawling a path we were told not to. Losing a compliance decision to an eviction policy is not a trade worth making. |
| Ignore robots.txt on public JSON endpoints | robots.txt is how a host says no. Reading it and choosing to disregard it is worse than not reading it. |
| Fetch in the request path with a short timeout | One slow host away from taking the API down on a single-instance deployment, and a page render does not have seconds to spend. |
