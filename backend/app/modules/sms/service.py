"""SMS ingestion: read a message, decide what may be done with it.

The parser decides what a message *says*. This decides what happens next, and
the two are separate because the second is policy -- a threshold, a mapping, a
rule about what may reach the ledger unattended -- while the first is a regex.

Nothing here writes a transaction without both a confident reading and a
*confirmed* account mapping. That pairing is the whole safety story: a bad
reading and a bad account each produce a wrong row, and a wrong row in a
financial ledger is not something the user has any reason to go looking for.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ConflictError, UnprocessableError
from app.core.logging import get_logger
from app.core.models import utcnow
from app.modules.finance.models import Transaction, TransactionKind, TransactionSource
from app.modules.finance.schemas import TransactionCreate
from app.modules.finance.service import FinanceService, normalize_merchant
from app.modules.sms import parser
from app.modules.sms.models import (
    AccountIdentifier,
    IdentifierKind,
    IdentifierSource,
    SmsIntake,
    SmsMessage,
    SmsStatus,
)
from app.modules.sms.parser.types import Direction, ParsedSms, Rail
from app.modules.sms.repository import AccountIdentifierRepository, SmsMessageRepository

# Re-exported so callers outside this module -- the Celery tasks in particular --
# name an intake without importing `sms.models`, which the
# `sms-internals-are-private` contract forbids. The same shape as finance
# re-exporting its enums for the paths that legitimately need them.
__all__ = ["Disposition", "IngestOutcome", "SmsIntake", "SmsService", "message_hash"]

logger = get_logger(__name__)

#: How far apart two records of the same payment may sit and still be the same
#: payment. Wider than the receipts matcher's same-day rule, because the skew
#: here is structural rather than accidental: an SMS carries the moment a card
#: was authorised, a statement carries the day it settled, and for cards those
#: differ by one to three days as a matter of course.
DUPLICATE_DAY_WINDOW = 3

#: Tighter than the receipts matcher's 2%, for the opposite reason. OCR misreads
#: digits, so it needs slack; an SMS states the amount exactly. The remaining
#: tolerance covers forex settlement, where the alert quotes the authorised INR
#: and the statement posts at the settlement rate.
DUPLICATE_AMOUNT_TOLERANCE = Decimal("0.005")


@dataclass(frozen=True, slots=True)
class DuplicateCandidate:
    """A transaction this message might already be."""

    transaction_id: uuid.UUID
    occurred_on: date
    amount: Decimal
    merchant: str | None
    similarity: Decimal


@dataclass(frozen=True, slots=True)
class IngestOutcome:
    """What became of one message.

    `already_held` is not an error. Re-importing an overlapping SMS backup is
    the normal case, not the exception, and reporting it as a failure would
    make the common path look broken.
    """

    message: SmsMessage | None
    already_held: bool = False
    filtered: bool = False


class Disposition:
    """What may be done with a reading, before anything is done with it.

    Returned from the preview so the paste screen can tell the user what will
    happen *before* they agree to it, rather than reporting it afterwards.
    """

    COMMIT = "commit"
    REVIEW = "review"
    NEEDS_ACCOUNT = "needs_account"
    NOT_A_TRANSACTION = "not_a_transaction"


def message_hash(user_id: uuid.UUID, sender: str, received_at: datetime, body: str) -> str:
    """Identity of a *message*, not of a transaction.

    Distinct from `Transaction.content_hash`, and prior to it. This is what
    stops one alert being ingested twice when a user captures it live and later
    imports an SMS backup covering the same day -- a case the transaction hash
    catches only afterwards, and only when every one of its components happens
    to agree.

    The body is included whole: two messages a second apart from the same
    sender are genuinely different transactions, and often are (a split bill,
    two taps at the same terminal).
    """
    parts = [
        str(user_id),
        sender.strip().upper(),
        received_at.astimezone(UTC).isoformat(),
        body.strip(),
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


class SmsService:
    """The only entry point into this module from anywhere else."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.messages = SmsMessageRepository(session)
        self.identifiers = AccountIdentifierRepository(session)
        self._settings = get_settings()

    async def preview(
        self,
        user_id: uuid.UUID,
        *,
        body: str,
        sender: str,
        received_at: datetime | None = None,
    ) -> tuple[ParsedSms | None, str, str, uuid.UUID | None, str | None]:
        """Read a message and report what would happen. Writes nothing.

        Returns (parsed, disposition, reason, resolved_account_id, unmapped_tail).

        A dry run is the first thing this feature owes a user. Handing an app
        permission to read your bank messages is a large ask, and being able to
        paste one in and see exactly what is extracted -- before any of it is
        stored -- is what makes that ask answerable.
        """
        received_at = received_at or datetime.now(UTC)
        parsed = parser.parse(body, sender=sender, received_at=received_at)

        if parsed is None:
            return (
                None,
                Disposition.NOT_A_TRANSACTION,
                "This does not look like a transaction alert.",
                None,
                None,
            )

        account_id, unmapped = await self.resolve_account(user_id, parsed)
        disposition, reason = self._disposition(parsed, account_id)
        return parsed, disposition, reason, account_id, unmapped

    async def resolve_account(
        self, user_id: uuid.UUID, parsed: ParsedSms
    ) -> tuple[uuid.UUID | None, str | None]:
        """Turn "A/c XX1234" into an account id, or report what did not match.

        A card tail and an account tail are looked up as different kinds. They
        are frequently the same digits for the same customer at the same bank,
        and reading a card spend against the savings account is exactly the
        silent misattribution this separation exists to prevent.

        Never creates an account, and never guesses one. An unmapped handle
        goes to the reconciliation screen with the transaction held.
        """
        if parsed.account_tail is None:
            return None, None

        kind = IdentifierKind.CARD_TAIL if parsed.rail is Rail.CARD else IdentifierKind.ACCOUNT_TAIL
        identifier = await self.identifiers.find(
            user_id, kind=kind.value, value=parsed.account_tail
        )
        if identifier is not None:
            return identifier.account_id, None
        return None, parsed.account_tail

    def _disposition(self, parsed: ParsedSms, account_id: uuid.UUID | None) -> tuple[str, str]:
        threshold = Decimal(str(self._settings.sms_confidence_threshold))

        if parsed.reversal:
            # Right sign, wrong meaning: a refund reduces an expense rather
            # than being income, and only the user knows which expense.
            return Disposition.REVIEW, "This looks like a refund or reversal."

        if parsed.confidence < threshold:
            reason = parsed.caveats[0] if parsed.caveats else "The reading is not confident enough."
            return Disposition.REVIEW, reason

        if account_id is None:
            if parsed.account_tail is None:
                return Disposition.NEEDS_ACCOUNT, "The message names no account."
            return (
                Disposition.NEEDS_ACCOUNT,
                f"No account is mapped to {parsed.account_tail} yet.",
            )

        return Disposition.COMMIT, "Read confidently, and the account is mapped."

    # -- ingestion --------------------------------------------------------

    async def ingest(
        self,
        user_id: uuid.UUID,
        *,
        body: str,
        sender: str,
        received_at: datetime,
        intake: SmsIntake,
        auto_commit: bool = True,
    ) -> IngestOutcome:
        """Store one message and, when it is safe to, book it.

        Order matters here. The intake hash is checked *before* parsing,
        because re-importing an overlapping backup is the normal case and
        re-reading fifty thousand messages to discover we already hold them is
        the difference between a job that finishes and one that times out.
        """
        digest = message_hash(user_id, sender, received_at, body)
        if await self.messages.by_message_hash(user_id, digest) is not None:
            return IngestOutcome(message=None, already_held=True)

        parsed = parser.parse(body, sender=sender, received_at=received_at)
        if parsed is None:
            # Not a transaction. Nothing is stored -- the row would be a
            # permanent copy of a personal or promotional message, kept for no
            # purpose, which is precisely what this feature promises not to do.
            return IngestOutcome(message=None, filtered=True)

        account_id, _ = await self.resolve_account(user_id, parsed)
        disposition, _reason = self._disposition(parsed, account_id)

        message = SmsMessage(
            user_id=user_id,
            intake=intake.value,
            sender=sender.strip().upper()[:32],
            received_at=received_at,
            body_redacted=parser.redact(body),
            raw_body=body,
            message_hash=digest,
            status=_STATUS_FOR[disposition],
            template_id=parsed.template_id,
            parser_version=parsed.parser_version,
            confidence=parsed.confidence,
            parsed=_as_json(parsed),
            resolved_account_id=account_id,
        )
        self.session.add(message)
        await self.session.flush()

        if auto_commit and disposition == Disposition.COMMIT:
            await self.commit(user_id, message.id)

        return IngestOutcome(message=message)

    async def ingest_batch(
        self,
        user_id: uuid.UUID,
        items: list[tuple[str, str, datetime]],
        *,
        intake: SmsIntake,
    ) -> dict[str, int]:
        """Many messages at once. Returns per-outcome counts.

        Pre-filters against the hashes we already hold in one query rather than
        one per message, which is what makes a fifty-thousand-message backfill
        finish. The per-message check inside `ingest` stays as the correctness
        guard -- this is only an optimisation, and a duplicate inside the same
        batch would slip past a snapshot taken before the loop.
        """
        counts = {"created": 0, "already_held": 0, "filtered": 0, "committed": 0}
        digests = [message_hash(user_id, s, r, b) for b, s, r in items]
        known = await self.messages.existing_hashes(user_id, digests)

        for (body, sender, received_at), digest in zip(items, digests, strict=True):
            if digest in known:
                counts["already_held"] += 1
                continue
            known.add(digest)

            outcome = await self.ingest(
                user_id, body=body, sender=sender, received_at=received_at, intake=intake
            )
            if outcome.filtered:
                counts["filtered"] += 1
            elif outcome.message is not None:
                counts["created"] += 1
                if outcome.message.status == SmsStatus.COMMITTED.value:
                    counts["committed"] += 1
        return counts

    # -- review and commit ------------------------------------------------

    async def queue(self, user_id: uuid.UUID) -> list[SmsMessage]:
        """Everything awaiting a human."""
        return await self.messages.queue(
            user_id,
            statuses=[
                SmsStatus.NEEDS_REVIEW.value,
                SmsStatus.NEEDS_ACCOUNT.value,
                SmsStatus.PARSED.value,
            ],
        )

    async def commit(
        self,
        user_id: uuid.UUID,
        message_id: uuid.UUID,
        *,
        account_id: uuid.UUID | None = None,
        category_id: uuid.UUID | None = None,
        allow_duplicate: bool = False,
    ) -> Transaction:
        """Turn a read message into a transaction.

        The account is required and never guessed. `account_id` overrides the
        resolved mapping so the review screen can correct a misattribution
        without the user first having to fix the mapping itself.
        """
        message = await self.messages.get_or_404(user_id, message_id)

        if message.status == SmsStatus.COMMITTED.value:
            raise ConflictError("This message has already been saved")
        if message.parsed is None:
            raise UnprocessableError("This message was never read as a transaction")

        target_account = account_id or message.resolved_account_id
        if target_account is None:
            raise UnprocessableError(
                "Map this message's account before saving it. "
                "Guessing would attribute the money to the wrong account."
            )

        parsed = _from_json(message.parsed)

        if not allow_duplicate and await self.duplicate_candidates(user_id, parsed):
            raise ConflictError(
                "A matching transaction already exists. "
                "Review it, or set allow_duplicate=true to save anyway."
            )

        finance = FinanceService(self.session)
        outcome = await finance.create_transaction(
            user_id,
            TransactionCreate(
                account_id=target_account,
                kind=(
                    TransactionKind.INCOME
                    if parsed.direction is Direction.CREDIT
                    else TransactionKind.EXPENSE
                ),
                amount=parsed.amount,
                occurred_on=parsed.occurred_on or utcnow().date(),
                category_id=category_id,
                merchant_raw=parsed.merchant,
                description=message.body_redacted[:500],
                # Deliberately never set. Any discriminator would make this row
                # hash differently from a CSV import of the same payment, which
                # is exactly the double-booking ADR-007 exists to prevent.
                allow_duplicate=False,
            ),
            source=TransactionSource.SMS,
        )

        if outcome.transaction is None:
            # The content hash caught it even though the near-duplicate scan
            # did not. Same-day, same-amount, same-merchant: converged exactly
            # as intended, so record that and move on rather than erroring.
            message.status = SmsStatus.COMMITTED.value
            await self.session.flush()
            raise ConflictError("This transaction is already recorded")

        message.transaction_id = outcome.transaction.id
        message.resolved_account_id = target_account
        message.status = SmsStatus.COMMITTED.value
        # The reading is now a transaction, so the original text has no further
        # purpose. Dropping it here rather than waiting for the retention job
        # keeps the window as short as the data allows.
        message.raw_body = None
        await self.session.flush()
        return outcome.transaction

    async def dismiss(self, user_id: uuid.UUID, message_id: uuid.UUID) -> SmsMessage:
        """Mark a message as not worth booking, without deleting the evidence."""
        message = await self.messages.get_or_404(user_id, message_id)
        if message.status == SmsStatus.COMMITTED.value:
            raise ConflictError("This message has already been saved")
        message.status = SmsStatus.IGNORED.value
        message.raw_body = None
        await self.session.flush()
        return message

    async def candidates_for(
        self, user_id: uuid.UUID, message_id: uuid.UUID
    ) -> list[DuplicateCandidate]:
        """Duplicate candidates for a stored message, by id."""
        message = await self.messages.get_or_404(user_id, message_id)
        if message.parsed is None:
            return []
        return await self.duplicate_candidates(user_id, _from_json(message.parsed))

    async def duplicate_candidates(
        self, user_id: uuid.UUID, parsed: ParsedSms
    ) -> list[DuplicateCandidate]:
        """Transactions this message might already be.

        The content hash converges only when the day, amount, merchant and
        account all agree. Card authorisation and settlement routinely differ
        by a day or three, and a statement import of the same purchase carries
        the posting date -- so the hash alone would let that pair through as
        two transactions. This is the net under it.

        Surfaced before commit and never auto-merged. Discovering a
        double-counted expense weeks later is far worse than one dismissible
        prompt now.
        """
        if parsed.occurred_on is None:
            return []

        tolerance = parsed.amount * DUPLICATE_AMOUNT_TOLERANCE
        window = timedelta(days=DUPLICATE_DAY_WINDOW)

        stmt = select(Transaction).where(
            Transaction.user_id == user_id,
            Transaction.deleted_at.is_(None),
            Transaction.occurred_on >= parsed.occurred_on - window,
            Transaction.occurred_on <= parsed.occurred_on + window,
            Transaction.amount >= parsed.amount - tolerance,
            Transaction.amount <= parsed.amount + tolerance,
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        merchant = normalize_merchant(parsed.merchant)
        candidates: list[DuplicateCandidate] = []
        for row in rows:
            similarity = Decimal("0.80")
            if merchant and row.merchant_normalized:
                if merchant == row.merchant_normalized:
                    similarity = Decimal("0.99")
                elif merchant in row.merchant_normalized or row.merchant_normalized in merchant:
                    similarity = Decimal("0.90")
            candidates.append(
                DuplicateCandidate(
                    transaction_id=row.id,
                    occurred_on=row.occurred_on,
                    amount=Decimal(row.amount),
                    merchant=row.merchant_raw,
                    similarity=similarity,
                )
            )
        return candidates

    # -- account identifiers ----------------------------------------------

    async def map_account(
        self,
        user_id: uuid.UUID,
        *,
        kind: IdentifierKind,
        value: str,
        account_id: uuid.UUID,
    ) -> AccountIdentifier:
        """Bind a handle to an account, confirmed by the user.

        Re-binding an existing handle moves it rather than failing: a user
        correcting a mistake should not have to delete a row first.
        """
        # Through the service, not its repository: reaching into another
        # module's data access couples this to finance's table layout, which is
        # what `finance-repositories-are-private` exists to prevent.
        await FinanceService(self.session).get_account(user_id, account_id)

        existing = await self.identifiers.find(
            user_id, kind=kind.value, value=value, confirmed_only=False
        )
        if existing is not None:
            existing.account_id = account_id
            existing.confirmed_at = utcnow()
            existing.source = IdentifierSource.USER.value
            identifier = existing
        else:
            identifier = AccountIdentifier(
                user_id=user_id,
                account_id=account_id,
                kind=kind.value,
                value=value,
                confirmed_at=utcnow(),
                source=IdentifierSource.USER.value,
            )
            self.session.add(identifier)

        await self.session.flush()
        await self._resolve_waiting(user_id, kind=kind, value=value, account_id=account_id)
        return identifier

    async def _resolve_waiting(
        self,
        user_id: uuid.UUID,
        *,
        kind: IdentifierKind,
        value: str,
        account_id: uuid.UUID,
    ) -> int:
        """Attach a newly-mapped account to the messages that were waiting on it.

        Without this, mapping an account fixes nothing already in the queue and
        the user has to revisit every held message by hand -- which, after a
        backfill, is the entire point of the queue.

        Deliberately does not auto-commit them. The user mapped an account;
        they did not ask for a month of held transactions to appear unreviewed.
        """
        held = await self.messages.queue(user_id, statuses=[SmsStatus.NEEDS_ACCOUNT.value])
        expected_rail_is_card = kind is IdentifierKind.CARD_TAIL
        touched = 0
        for message in held:
            if message.parsed is None:
                continue
            parsed = _from_json(message.parsed)
            if parsed.account_tail != value:
                continue
            if (parsed.rail is Rail.CARD) != expected_rail_is_card:
                continue
            message.resolved_account_id = account_id
            message.status = SmsStatus.PARSED.value
            touched += 1
        if touched:
            await self.session.flush()
        return touched

    async def purge_raw_bodies(self) -> int:
        """Drop the original text of messages past the retention window.

        Deployment-wide rather than per user, which is why it takes no
        `user_id` and does not go through the tenant-scoped repository: a
        retention promise is about the data, not about who owns it.

        A message that was committed or dismissed already had its raw body
        cleared at that moment. This catches the rest -- the ones sitting
        unresolved in a queue nobody came back to, which is the population most
        likely to hold something personal that slipped the sender filter.
        """
        cutoff = utcnow() - timedelta(days=self._settings.sms_raw_body_retention_days)
        result = await self.session.execute(
            update(SmsMessage)
            .where(SmsMessage.received_at < cutoff, SmsMessage.raw_body.is_not(None))
            .values(raw_body=None)
        )
        await self.session.flush()
        # `rowcount` is on CursorResult, which an UPDATE returns; SQLAlchemy
        # types `execute` as the general Result.
        return int(getattr(result, "rowcount", 0) or 0)

    async def list_mappings(self, user_id: uuid.UUID) -> list[AccountIdentifier]:
        return await self.identifiers.all_for_user(user_id)

    async def suggest_identifiers(self, user_id: uuid.UUID) -> dict[str, int]:
        """Unmapped handles seen in the queue, with how often each appeared.

        Drives the reconciliation screen. Frequency is the useful ordering: the
        tail on ninety messages is the user's main account, and mapping it
        first clears most of the queue in one action.
        """
        held = await self.messages.queue(user_id, statuses=[SmsStatus.NEEDS_ACCOUNT.value])
        counts: dict[str, int] = {}
        for message in held:
            if message.parsed is None:
                continue
            tail = message.parsed.get("account_tail")
            if tail:
                counts[str(tail)] = counts.get(str(tail), 0) + 1
        return counts


#: Disposition -> the status the row is stored with. Kept as a table rather
#: than a chain of ifs so the two vocabularies stay visibly aligned.
_STATUS_FOR = {
    Disposition.COMMIT: SmsStatus.PARSED.value,
    Disposition.REVIEW: SmsStatus.NEEDS_REVIEW.value,
    Disposition.NEEDS_ACCOUNT: SmsStatus.NEEDS_ACCOUNT.value,
    Disposition.NOT_A_TRANSACTION: SmsStatus.UNPARSED.value,
}


def _as_json(parsed: ParsedSms) -> dict[str, object]:
    """The reading, as stored. Decimals and dates as strings.

    JSONB has no Decimal, and letting it round-trip through float would put a
    money value through binary floating point -- the exact thing ADR-003 bans
    at the column level, and no less wrong for being inside a JSON document.
    """
    return {
        "template_id": parsed.template_id,
        "issuer": parsed.issuer,
        "rail": parsed.rail.value,
        "direction": parsed.direction.value,
        "amount": format(parsed.amount, "f"),
        "confidence": format(parsed.confidence, "f"),
        "parser_version": parsed.parser_version,
        "occurred_on": parsed.occurred_on.isoformat() if parsed.occurred_on else None,
        "date_inferred": parsed.date_inferred,
        "merchant": parsed.merchant,
        "vpa": parsed.vpa,
        "account_tail": parsed.account_tail,
        "reference": parsed.reference,
        "reversal": parsed.reversal,
        "caveats": list(parsed.caveats),
    }


def _from_json(raw: Mapping[str, Any]) -> ParsedSms:
    """Rebuild a reading from its stored form.

    Round-tripping rather than re-parsing on purpose: the raw body is deleted
    once a message is resolved, and a commit that depended on re-reading it
    would fail on exactly the rows that had been handled correctly.
    """
    occurred = raw.get("occurred_on")
    return ParsedSms(
        template_id=str(raw["template_id"]),
        issuer=str(raw.get("issuer") or ""),
        rail=Rail(str(raw["rail"])),
        direction=Direction(str(raw["direction"])),
        amount=Decimal(str(raw["amount"])),
        confidence=Decimal(str(raw["confidence"])),
        parser_version=str(raw.get("parser_version") or ""),
        occurred_on=date.fromisoformat(str(occurred)) if occurred else None,
        date_inferred=bool(raw.get("date_inferred")),
        merchant=(str(raw["merchant"]) if raw.get("merchant") else None),
        vpa=(str(raw["vpa"]) if raw.get("vpa") else None),
        account_tail=(str(raw["account_tail"]) if raw.get("account_tail") else None),
        reference=(str(raw["reference"]) if raw.get("reference") else None),
        reversal=bool(raw.get("reversal")),
        caveats=tuple(str(c) for c in (raw.get("caveats") or [])),
    )
