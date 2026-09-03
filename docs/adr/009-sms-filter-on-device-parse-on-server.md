# ADR-009 — SMS is filtered on the device and parsed on the server

**Status:** Accepted · **Date:** 2026-08-17

## Context

SMS ingestion is the first path where data arrives unbidden. Every other source begins with the user
saying what an entry means — they typed it, mapped a CSV column, photographed a receipt. Here a
message arrives and the system has to decide whether it is a transaction at all, from text the user
never chose to hand over.

That makes *where the parsing runs* a privacy decision with legal weight, not an engineering
preference. Google Play's own rule for this category is explicit: budgeting apps "may not exfiltrate
or share non-financial or personal SMS history of a user." Frugal is distributed outside the Play
Store (see `mobile/README.md`), so that rule does not bind it — but it is the right line, and it is
the line a user would draw if asked.

India's DPDP Rules were notified on 2025-11-14 with substantive obligations commencing around
2027-05-14. Purpose limitation, an itemised consent notice, and erasure all apply to this feature.
That is roughly twenty-one months of runway, which is enough to build compliance in rather than bolt
it on, and this decision is the part that has to be right from the first commit.

There are three intake paths, and only one of them is on a phone:

| Path | Where it starts |
|---|---|
| Message-backup XML upload | Browser or app — arrives as bytes on the server |
| Share sheet | Android, no permission — arrives as text on the server |
| Live capture | Android, `READ_SMS`, `smsreader` build only |

## Decision

**Filter on the device. Parse on the server. One implementation of each.**

The device-side filter (`app/modules/sms/parser/senders.py`, mirrored into Kotlin) is an allowlist of
bank DLT sender headers plus a transactional-keyword gate with disqualifying phrases. A message that
fails it is discarded in the broadcast receiver: never written to disk on the handset, never
transmitted.

Everything that passes is parsed server-side by the template registry.

### Why not parse on the device

1. **A sideloaded APK has no update channel.** Not the Play Store, no auto-update. Banks change
   message wording without notice, and an on-device template table stays broken until every user
   manually re-downloads an APK from a website. A server-side parser is fixed by a deploy. This alone
   decides it.
2. **Two of the three paths are server-side anyway.** Parsing on the device too means the same regex
   table maintained twice, in two languages, drifting — worse for correctness than either choice
   alone.
3. **The policy line is drawn by the filter, not the parser.** "Do not exfiltrate personal SMS" is
   satisfied by never letting a personal message leave, which the allowlist achieves completely.
4. **Confidence thresholds need calibrating.** `sms_confidence_threshold` decides what books
   unattended. A threshold baked into an un-updatable APK cannot be tuned against real data.

### What this costs, stated plainly

**Matched bank message bodies leave the device and reach the server.** That is the trade. Four
mitigations ship with it, not after:

- The allowlist is **generated**, not hand-mirrored. `scripts/generate_sender_filter.py` emits the
  Kotlin from the Python, and `test_device_allowlist_matches_kotlin` regenerates and compares — so
  the two cannot drift, which is the one failure here nobody would notice.
- `sms_messages.raw_body` is dropped the moment a message is committed or dismissed, and by a nightly
  sweep after `sms_raw_body_retention_days` for anything left unreviewed.
- What persists is `body_redacted`: the amount, merchant and reference stay so the review queue can
  show its work; the phone number, any one-time code, and **the running balance** are removed. A
  balance is the most revealing figure in a bank message and no engine reads it.
- The download page and the in-app permission prompt say exactly this, in plain words.

### Nothing books itself without two independent confirmations

A transaction is written unattended only when the reading clears the confidence threshold **and** the
account handle resolves to a mapping the user has *confirmed*. An unmapped handle never creates an
account and never guesses one — it holds the message.

The two failures compound differently and both are silent: a bad reading writes a wrong amount, a bad
account writes the right amount into the wrong ledger. Neither is something a user has any reason to
go looking for.

## Consequences

**Positive.** One parser, fixable by deploy. A privacy boundary that is a generated artefact with a
test behind it rather than a promise. The filter is auditable in one 150-line file.

**Positive.** The permission-free paths are first-class rather than fallbacks, which is what lets the
`standard` APK — the one that actually installs — be the real product.

**Negative.** Bank message bodies transit to and rest on the server, briefly. A fully on-device design
would not have that property. It would also be unfixable, unmeasurable, and duplicated.

**Negative.** A message from an allowlisted sender that is *not* a transaction can still reach the
server if it passes the keyword gate. The disqualifying-phrase list is the mitigation, and
`tests/fixtures/sms/negative.py` is where it is held honest.

## Alternatives rejected

**Parse entirely on the device, upload only extracted fields.** The strongest privacy story, and
unworkable: no update channel, and two of three intake paths cannot use it, so the table would be
maintained twice.

**Upload everything and filter on the server.** Simplest, and the one option that is straightforwardly
wrong — it puts the user's personal messages on someone else's computer to decide whether they were
interesting.

**Use `NotificationListenerService` instead of `READ_SMS`.** The common workaround, and it is blocked
by the same Play Protect scan, which names it explicitly alongside the SMS permissions. It would also
give no historical backfill.
