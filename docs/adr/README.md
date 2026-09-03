# Architecture Decision Records

Each record captures one binding decision, the context that forced it, and what it costs. They are
written to be read by someone who arrives later and wants to know *why*, not *what* — the what is in
the code.

| # | Decision | Summary |
|---|---|---|
| [001](001-modular-monolith.md) | Modular monolith with tool-enforced boundaries | One deployable; `import-linter` makes module boundaries a build failure rather than a review note. |
| [002](002-explanation-contract.md) | A single typed `Explanation` contract | Every engine returns the same envelope; a score with no factors cannot serialise. |
| [003](003-money-as-decimal.md) | Money as `Decimal`, currency from day one | `NUMERIC(18,2)` end to end; JSON amounts are strings so values survive a round trip. |
| [004](004-ports-and-adapters.md) | Ports and adapters at every external boundary | Isolates the price-source risk; the whole suite runs with no network or credentials. |
| [005](005-deterministic-explainable-ai.md) | Deterministic AI, no LLM in the decision path | Reproducible, auditable, inspectable — the product thesis made structural. |
| [006](006-async-api-sync-workers.md) | Async API, synchronous workers | Matches each workload's actual nature; one schema, two engines. |
| [007](007-idempotent-ingestion.md) | Idempotent ingestion at two layers | Content hash protects the data; idempotency keys protect the client's knowledge of it. |
| [008](008-external-quota-and-degradation.md) | Metered dependencies pay nothing, or they stop | Zero recurring cost as an enforced invariant: a local ledger below the provider's cap, and graceful degradation when it is spent. |
| [009](009-sms-filter-on-device-parse-on-server.md) | SMS filtered on the device, parsed on the server | The filter is the privacy boundary and is generated, not mirrored by hand; the parser stays fixable by deploy. |
| [010](010-azure-migration.md) | Deploy on Azure for Students, with a second object-storage adapter | A term decision, not a cost-safety one: twelve renewable months instead of six. Neon and Render do not move, so the financial data never migrates. |
| [011](011-a-second-database-for-personalization.md) | Personalization signals live in a second database, and erasure across the boundary is an outbox | Purpose limitation made structural: a module cannot query a behavioural profile it cannot reach. The cost is that erasure stops being atomic and becomes eventually consistent, which is measured rather than assumed. |
| [012](012-narrow-allowlisted-fetching.md) | Narrow, allowlisted, robots-honouring fetching | Narrows ADR-004 rather than reversing it: unrestricted retail scraping stays rejected, while an empty-by-default allowlist of robots-permitted public endpoints is added, fail-closed and rate-limited per host. |
| [013](013-crowdsourced-price-graph.md) | A crowdsourced price graph, and what may cross the line from a receipt | Verified receipts become shared prices; the basket never does. Privacy is a schema property — no user_id on the shared row, a k-anonymity floor on read, and a contributor hash that is pseudonymous and says so. |
| [014](014-self-hosted-vector-tiles.md) | The basemap is a file we host, not an API we call | A 6.9 MB Chennai extract served with byte ranges from the box we already run: no key, no meter, no card, and no third party learning where a user is looking. Costs finite coverage and no labels. |

## Format

Context → Decision → Consequences (positive and negative) → why the main alternative was rejected.

Consequences include the negative ones. An ADR listing only benefits is advocacy, not a record, and it
is useless to the person who later hits the cost it failed to mention.

## Changing a decision

Supersede rather than edit. Add a new ADR, mark the old one `Superseded by ADR-NNN`, and leave its text
intact. The reasoning that was valid under earlier constraints is what explains the code written under
them.
