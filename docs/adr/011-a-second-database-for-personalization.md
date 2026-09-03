# ADR-011 — Personalization signals live in a second database, and erasure across the boundary is an outbox

**Status:** Accepted · **Date:** 2026-08-25

## Context

ADR-001 committed to a modular monolith: one deployable, one Postgres, one migration history. That
decision has held for thirteen milestones and nothing here overturns it for the ledger.

M14 introduces a different kind of data. The system already reads bank alerts (ADR-009) and books
the ones that are transactions. From those, and from the ledger generally, it can derive a
*behavioural* record: what a person buys that is unusually large for them, how often, in what
category. That record is not accounting. Nobody reconciles against it, no balance depends on it, and
no report cites it. Its only consumer is a recommendation.

ADR-009 already recorded the uncomfortable half of this: **matched bank message bodies leave the
device and reach the server.** That was accepted with four mitigations, one of which was that
`raw_body` is dropped on commit and swept nightly. A derived behavioural profile is the thing that
survives all of those mitigations — it is built from messages after their text is gone, and it is
precisely the artefact a user would be most surprised to learn exists.

Two facts make the placement question sharp rather than aesthetic:

- **Purpose limitation** is a DPDP obligation, not a preference. Data collected to categorise a
  user's spending is not automatically available for anything else, and the cheapest way to make
  that true is for the other thing to be unable to reach it.
- **A derived profile is the most attractive part of this database to an attacker and the least
  necessary part to the product.** Losing it costs a personalised recommendation. Losing the ledger
  costs somebody their financial records.

## Decision

**Put purchase signals in a genuinely separate Postgres database: its own URL, its own engine, its
own Alembic history, its own declarative base. No foreign key crosses between them, because Postgres
cannot express one — and that impossibility is the point.**

The design consists of six things, and each exists to make a promise structural rather than
verbal.

### 1. A different database, enforced at boot

`SIGNALS_DATABASE_URL`, unset by default. A `model_validator` refuses to start the process if it
resolves to the same (host, port, path) as `DATABASE_URL`:

> the separation is the entire feature: it is what makes 'no cross-database foreign key' a property
> of the deployment rather than a promise in a comment.

Without that check the whole design could be satisfied by two settings pointing at one database,
with every contract kept, every test passing, and no isolation at all.

### 2. `subject_id`, not `user_id`

The single most consequential naming decision in the milestone. In this codebase `user_id` means
three things simultaneously: a cascading foreign key to `users`, membership in the account-deletion
cascade, and a `BaseRepository.scoped_select` predicate. **None of the three is true over there.**

Borrowing the name would do two kinds of damage. `test_every_user_owned_table_cascades` — which asks
the database which tables carry a `user_id` without a cascading foreign key — would describe a
database it cannot see. And a personalization model would be picked up by the tenant-repository
sweep and *silently skipped*, because the mechanism it checks for does not exist there.

`subject_id` says what it is: an opaque value, never joined, erased by a sweep.

### 3. The engine lives in its own module

`app/core/signals_database.py`, not three more functions in `core/database.py`. This is not
organisation, it is enforcement: `import-linter` is module-granular and cannot forbid a *function*
inside a file every module legitimately imports. Splitting it turns "only the personalization module
opens the second database" into the `the-signals-database-has-one-owner` contract.

Readiness reaches it the legal way — `app/api/system.py` asks
`app.modules.personalization.service.health_check()`, and api → modules is a permitted direction, so
the contract needs no carve-out. `app.main` is the one exception, because somebody has to dispose the
pool on shutdown.

### 4. Two Alembic trees, two ini files

Not one file with a `[alembic:signals]` section. Every invocation in this repo is a bare
`alembic upgrade head` — Makefile, CI, `conftest.py`, `deploy.sh`. A `--name signals` layout would
mean the second tree is reachable only by remembering a flag, and forgetting it would run the
*primary* migration silently and successfully. `alembic -c alembic_signals.ini` fails loudly when it
is wrong. The version table is `alembic_version_signals` as insurance: if the two URLs were ever
pointed at one database despite the validator, the result is two visible histories rather than one
corrupted row.

### 5. Erasure is an outbox, and it is eventually consistent

This is the part that must be stated rather than glossed, because it is a sentence a regulator may
one day read.

A cascading foreign key cannot cross a database boundary and **no transaction spans two databases.**
Neither is a limitation to engineer around; both are facts about Postgres. So account deletion
writes an `erasure_requests` row *in the same transaction as the delete*, and an idempotent hourly
sweep drains it.

Three properties make that a guarantee rather than a hope:

- **The debt lands or the deletion does not.** Same transaction, no separate commit. A committed
  request beside a rolled-back delete would erase a live user's data, which is worse than the bug it
  guards against.
- **The signals database is committed first, always.** There is no spanning transaction, so ordering
  *is* the correctness argument. A crash between the two commits leaves the debt outstanding and the
  sweep runs again — and deleting zero rows twice is free. The opposite order marks the obligation
  paid and leaves the data, which is the one outcome retrying cannot fix.
- **The sweep discovers tables from the metadata**, in reverse dependency order, rather than from a
  list. A hand-maintained list is how an erasure obligation quietly stops being met when somebody
  adds a table.

The dispatch on deletion is for latency only. If Redis is down, the worker is dead, or the task
throws, the hourly schedule still finds the row. **The outbox is the guarantee; the message never
is.**

### 6. What is measured

`oldest_pending_erasure_seconds` on `GET /system/providers`. "We cannot make this atomic" obliges us
to measure the gap: an unbounded queue here is a compliance failure that is otherwise entirely
invisible. Alert above 24 hours. This is the direct lesson of the M11 finding in ADR-010, where the
CloudWatch agent tailed a log file nothing ever wrote — an alarm that could not fire.

## Consequences

### Good

- Purpose limitation is structural. A module cannot query a behavioural profile it cannot reach, and
  the contract makes trying a build failure rather than a review comment.
- The blast radius of a personalization bug stops at recommendations. `readiness` reports the
  dependency and deliberately does **not** return 503 for it: taking a working ledger offline to
  report a degraded extra would be the wrong trade.
- The feature is droppable. Unset the variable and it does not exist — no routes, no task, no
  readiness entry. Not a broken feature: an absent one.
- The existing cascade invariant stays literally true and literally unchanged, because there is no
  `user_id` over there to describe.

### Bad, and accepted

- **Erasure is no longer atomic.** It is now eventually consistent, typically within the hour. This
  is the real cost of the decision and no amount of engineering removes it; only measurement makes
  it defensible.
- **A second engine, a second migration history, a second thing to back up.** `make migrate` runs
  both, CI checks both, and a developer who forgets the second one gets a loud failure rather than a
  quiet drift — but it is genuinely more to keep in step.
- **No joins, ever.** A question that spans the ledger and the profile must be answered in Python
  over two result sets. That cost is paid by design; if it ever becomes a hot path, the honest fix is
  to denormalise into the signals database, not to reach across.
- **The loop-binding trap now applies twice.** Any Celery task touching both databases needs *two*
  NullPool engines inside one `asyncio.run`. `worker_both_sessions()` exists so the correct thing is
  the convenient thing — the same reasoning that produced `worker_async_session` after that bug was
  hit in three separate places and looked different each time.
- **Locally the two are databases on one Postgres instance, not two servers.** That still buys the
  property the design depends on — no cross-database foreign key, no join — but it is a weaker
  isolation claim than production, and the docs must not blur them.

## Alternatives rejected

| Option | Why not |
|---|---|
| A `personalization` **schema** in the same database | Cheaper to run and back up, and the isolation would be a convention rather than a boundary. Same database, same credentials, one `SET search_path` from being irrelevant. |
| Regular tables behind a strict module service | What ADR-001 would ordinarily prescribe, and it is the right answer for every other module. It does not satisfy purpose limitation: the data remains one join away from every other engine. |
| Keep it in the ledger and rely on the import-linter | The linter stops an *import*. It does not stop a raw SQL query, a migration, a psql session, or a backup restored into the wrong place. |
| Redis or a document store | Introduces a second storage technology for one feature, and this data must survive a Redis flush — ADR-008 already argued that regenerable state and durable state are different things. |
