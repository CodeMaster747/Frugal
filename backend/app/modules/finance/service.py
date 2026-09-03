"""Financial core service — the module's public interface.

Other modules import this file and nothing else from `app.modules.finance`
(ADR-001). Analytics, health, forecasting, and the advisor all read through it.
"""

from __future__ import annotations

import re
import uuid
from calendar import monthrange
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import cache
from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.core.logging import get_logger
from app.core.pagination import Cursor
from app.modules.finance.models import (
    Account,
    Budget,
    Category,
    Goal,
    RecurringItem,
    Transaction,
    TransactionKind,
    TransactionSource,
)
from app.modules.finance.repository import (
    AccountRepository,
    BudgetRepository,
    CategoryRepository,
    GoalRepository,
    RecurringRepository,
    TransactionRepository,
)
from app.modules.finance.schemas import (
    AccountCreate,
    AccountUpdate,
    BudgetCreate,
    BudgetUpdate,
    CategoryCreate,
    GoalCreate,
    GoalUpdate,
    RecurringCreate,
    RecurringUpdate,
    TransactionCreate,
    TransactionFilters,
    TransactionUpdate,
)

if TYPE_CHECKING:
    # Import-time cycle otherwise: categorization imports finance's models, and
    # finance calls back into categorization's service. The annotation is all
    # that is needed at type-check time; `_categorizer` does the real import.
    from app.modules.categorization.service import CategorizationService

logger = get_logger(__name__)

# Strips the noise banks add to merchant strings: terminal ids, reference
# numbers, city codes, POS prefixes. Shared with the categoriser in M5, which is
# why it lives here rather than in the import path.
_NOISE = re.compile(
    # Rail and channel prefixes.
    r"\b(?:pos|upi|neft|imps|ach|atm|txn|ref|rrn|trf|vps|mps)\b[\s:/-]*|"
    # Long reference numbers anywhere.
    r"\b\d{6,}\b|"
    # Terminal ids: a run of 4+ digits at the end of the string. Bounded to the
    # end so a number that is part of the name ("7 Eleven", "Cafe 1730") is
    # kept -- those are identity, not noise.
    r"\s\d{4,}\s*$|"
    r"[*#]+\d+|"
    r"\b\d{2}/\d{2}(?:/\d{2,4})?\b",
    re.IGNORECASE,
)


def normalize_merchant(raw: str | None) -> str | None:
    """Reduce a raw bank narration to a stable merchant key.

    ``SWIGGY*ORDER 88213`` and ``UPI/SWIGGY/883012`` both become ``swiggy``, so
    the same vendor deduplicates and categorises consistently.
    """
    if not raw:
        return None
    cleaned = _NOISE.sub(" ", raw)
    cleaned = re.sub(r"[^a-zA-Z0-9&' ]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    return cleaned[:255] or None


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:80]


@dataclass(frozen=True, slots=True)
class DetectedRecurring:
    """A commitment the forecasting module detected.

    A typed payload rather than a dict: this crosses a module boundary, and a
    dict would make a renamed key a runtime failure in a background task rather
    than a type error at the call site.
    """

    name: str
    kind: str
    item_type: str
    cadence: str
    amount: Decimal
    next_due_on: date
    first_seen_on: date
    amount_variance: Decimal
    confidence: Decimal
    category_id: uuid.UUID | None = None
    account_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class CreateOutcome:
    transaction: Transaction | None
    duplicate: bool = False


class FinanceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.accounts = AccountRepository(session)
        self.categories = CategoryRepository(session)
        self.transactions = TransactionRepository(session)
        self.budgets = BudgetRepository(session)
        self.goals = GoalRepository(session)
        self.recurring = RecurringRepository(session)
        # Built on first use, then reused for the life of the request -- see
        # `_categorizer`. Most requests never categorise anything.
        self._categorization: CategorizationService | None = None

    # --- accounts ---------------------------------------------------------

    async def get_account(self, user_id: uuid.UUID, account_id: uuid.UUID) -> Account:
        """Fetch one account, or 404.

        Public because other modules legitimately need to check that an account
        exists and belongs to the caller -- the SMS module does this before
        binding a bank's account handle to it. The alternative is reaching
        through `service.accounts`, which is the coupling to a module's table
        layout that `finance-repositories-are-private` exists to prevent.
        """
        return await self.accounts.get_or_404(user_id, account_id)

    async def create_account(self, user_id: uuid.UUID, data: AccountCreate) -> Account:
        if await self.accounts.by_name(user_id, data.name):
            raise ConflictError(f"An account named {data.name!r} already exists")

        account = Account(
            user_id=user_id,
            name=data.name,
            type=data.type.value,
            currency=data.currency,
            opening_balance=data.opening_balance,
            # A new account starts at its opening balance; every later movement
            # adjusts this incrementally.
            current_balance=data.opening_balance,
            credit_limit=data.credit_limit,
            is_liquid=data.is_liquid,
            institution=data.institution,
        )
        created = await self.accounts.add(account)
        await self._invalidate(user_id)
        return created

    async def update_account(
        self, user_id: uuid.UUID, account_id: uuid.UUID, data: AccountUpdate
    ) -> Account:
        account = await self.accounts.get_or_404(user_id, account_id)
        for field, value in data.model_dump(exclude_unset=True, exclude_none=True).items():
            setattr(account, field, value)
        await self.session.flush()
        return account

    async def archive_account(self, user_id: uuid.UUID, account_id: uuid.UUID) -> Account:
        account = await self.accounts.get_or_404(user_id, account_id)
        account.archived_at = datetime.now(UTC)
        await self.session.flush()
        return account

    async def delete_account(
        self, user_id: uuid.UUID, account_id: uuid.UUID, *, force: bool
    ) -> None:
        account = await self.accounts.get_or_404(user_id, account_id)

        if not force:
            filters = TransactionFilters(account_id=account_id)
            rows, _ = await self.transactions.page(user_id, cursor=None, limit=1, filters=filters)
            if rows:
                raise ConflictError(
                    "This account has transactions. Archive it, or delete with force=true."
                )
        account.soft_delete()
        await self.session.flush()

    # --- categories -------------------------------------------------------

    async def list_categories(self, user_id: uuid.UUID) -> Sequence[Category]:
        return await self.categories.list_all(user_id)

    async def list_system_categories(self) -> Sequence[Category]:
        """The shared taxonomy, with no user's custom categories in it."""
        return await self.categories.list_system()

    async def create_category(self, user_id: uuid.UUID, data: CategoryCreate) -> Category:
        slug = slugify(data.name)
        if await self.categories.by_slug(user_id, slug):
            raise ConflictError(f"A category named {data.name!r} already exists")

        if data.parent_id:
            parent = await self.categories.get(user_id, data.parent_id)
            if parent is None:
                raise NotFoundError("Parent category")
            if parent.parent_id is not None:
                # Two levels only: deeper trees make budget rollup ambiguous
                # and analytics queries recursive, for no user-visible gain.
                raise UnprocessableError("Categories nest two levels deep at most")

        return await self.categories.add(
            Category(
                user_id=user_id,
                parent_id=data.parent_id,
                name=data.name,
                slug=slug,
                kind=data.kind,
                icon=data.icon,
                color=data.color,
                is_system=False,
            )
        )

    # --- transactions -----------------------------------------------------

    async def create_transaction(
        self,
        user_id: uuid.UUID,
        data: TransactionCreate,
        *,
        source: TransactionSource = TransactionSource.MANUAL,
    ) -> CreateOutcome:
        """Create a transaction, or report it as a duplicate.

        A transfer becomes a *linked pair* -- an expense on the source account
        and an income on the destination -- so account balances move correctly
        while income/expense aggregates can exclude both (FR-2.3).
        """
        account = await self.accounts.get_or_404(user_id, data.account_id)

        if data.kind == TransactionKind.TRANSFER:
            return await self._create_transfer(user_id, data, account, source)

        if data.category_id and not await self.categories.get(user_id, data.category_id):
            raise NotFoundError("Category")

        txn = self._build(user_id, data, source, kind=data.kind)

        if await self.transactions.hash_exists(user_id, txn.content_hash):
            return CreateOutcome(transaction=None, duplicate=True)

        # Categorise *after* the duplicate check: a duplicate is discarded, so
        # classifying it is wasted work -- and re-importing a statement, which is
        # mostly duplicates, is the common case.
        #
        # Only when the caller did not choose. An explicit category is a
        # decision; overriding it would be the model second-guessing a human.
        if txn.category_id is None:
            await self._auto_categorize(user_id, txn)

        try:
            await self.transactions.add(txn)
        except IntegrityError:
            # The unique index is the authority: another request may have
            # inserted the same content between the check above and this write.
            await self.session.rollback()
            return CreateOutcome(transaction=None, duplicate=True)

        await self.accounts.adjust_balance(account.id, self._signed(txn))
        await self._invalidate(user_id)
        return CreateOutcome(transaction=await self._with_category(txn))

    async def _invalidate(self, user_id: uuid.UUID) -> None:
        """Invalidate every cached aggregate for this user.

        Called from each write path rather than from the router, so a future
        caller that reaches the service directly -- a Celery task, the seeder --
        cannot forget it and leave the dashboard stale.
        """
        await cache.bump_version(user_id)

    async def _with_category(self, txn: Transaction) -> Transaction:
        """Ensure the category relationship is populated after a write.

        `lazy="selectin"` loads on query, but an object just added or mutated in
        this session has not been through one. Under an async session, touching
        an unloaded relationship raises MissingGreenlet rather than lazy-loading,
        so the response schema would fail instead of silently returning null --
        which is the better failure, but still a failure.
        """
        await self.session.refresh(txn, ["category"])
        return txn

    async def _create_transfer(
        self,
        user_id: uuid.UUID,
        data: TransactionCreate,
        source_account: Account,
        source: TransactionSource,
    ) -> CreateOutcome:
        assert data.to_account_id is not None  # guaranteed by the schema validator
        if data.to_account_id == data.account_id:
            raise UnprocessableError("A transfer needs two different accounts")

        destination = await self.accounts.get_or_404(user_id, data.to_account_id)

        # The two legs are stored as a plain EXPENSE and a plain INCOME rather
        # than both as kind='transfer'. A shared 'transfer' kind makes the sign
        # ambiguous -- nothing in the row says which way the money went -- so
        # every balance calculation would need to special-case it and could
        # silently get it backwards.
        #
        # What marks them as a transfer is `transfer_pair_id`, which is also
        # what the aggregates exclude on (FR-2.3). Sign stays unambiguous
        # everywhere, and "is this a transfer" stays a single, precise test.
        outgoing = self._build(user_id, data, source, kind=TransactionKind.EXPENSE)
        if await self.transactions.hash_exists(user_id, outgoing.content_hash):
            return CreateOutcome(transaction=None, duplicate=True)

        incoming = self._build(
            user_id,
            data,
            source,
            kind=TransactionKind.INCOME,
            account_id=destination.id,
            discriminator=f"transfer-in:{outgoing.content_hash[:16]}",
        )

        await self.transactions.add(outgoing)
        await self.transactions.add(incoming)
        outgoing.transfer_pair_id = incoming.id
        incoming.transfer_pair_id = outgoing.id
        await self.session.flush()

        await self.accounts.adjust_balance(source_account.id, self._signed(outgoing))
        await self.accounts.adjust_balance(destination.id, self._signed(incoming))
        await self._invalidate(user_id)

        return CreateOutcome(transaction=await self._with_category(outgoing))

    def _build(
        self,
        user_id: uuid.UUID,
        data: TransactionCreate,
        source: TransactionSource,
        *,
        kind: TransactionKind,
        account_id: uuid.UUID | None = None,
        discriminator: str = "",
    ) -> Transaction:
        target_account = account_id or data.account_id
        merchant = normalize_merchant(data.merchant_raw)

        if data.allow_duplicate and not discriminator:
            # The user has confirmed this is a genuinely separate transaction,
            # so give the hash a unique component rather than merging it.
            discriminator = uuid.uuid4().hex[:12]

        return Transaction(
            user_id=user_id,
            account_id=target_account,
            category_id=data.category_id,
            kind=kind.value,
            amount=data.amount,
            currency=data.currency,
            occurred_on=data.occurred_on,
            merchant_raw=data.merchant_raw,
            merchant_normalized=merchant,
            description=data.description,
            source=source.value,
            content_hash=Transaction.compute_hash(
                user_id, data.occurred_on, data.amount, merchant, target_account, discriminator
            ),
        )

    def _categorizer(self) -> CategorizationService:
        """One categoriser per request.

        A CSV import calls `create_transaction` once per row; a fresh service
        each time would re-read the category table and the user's rules for every
        row. The instance memoises both, so a 500-row import does two lookups
        rather than a thousand. Request-scoped, so it cannot go stale in any way
        a caller can observe.
        """
        from app.modules.categorization.service import CategorizationService

        if self._categorization is None:
            self._categorization = CategorizationService(self.session)
        return self._categorization

    async def _auto_categorize(self, user_id: uuid.UUID, txn: Transaction) -> None:
        """Attach a suggested category, recording how sure it was.

        `is_reviewed` stays False so the row surfaces in the review queue: a
        machine guess is provisional until a person has seen it.
        """
        suggestion = await self._categorizer().suggest(user_id, txn.merchant_normalized)
        if suggestion is None:
            # Uncategorised is the honest answer when nothing is confident.
            return

        txn.category_id = suggestion.category_id
        txn.category_confidence = suggestion.confidence
        txn.categorizer_version = suggestion.version[:32]
        txn.is_reviewed = suggestion.is_rule and suggestion.source == "user_rule"

    @staticmethod
    def _signed(txn: Transaction) -> Decimal:
        return (
            Decimal(txn.amount)
            if txn.kind == TransactionKind.INCOME.value
            else -Decimal(txn.amount)
        )

    async def list_transactions(
        self,
        user_id: uuid.UUID,
        *,
        cursor: Cursor | None,
        limit: int,
        filters: TransactionFilters,
    ) -> tuple[Sequence[Transaction], bool]:
        return await self.transactions.page(user_id, cursor=cursor, limit=limit, filters=filters)

    async def update_transaction(
        self, user_id: uuid.UUID, txn_id: uuid.UUID, data: TransactionUpdate
    ) -> Transaction:
        txn = await self.transactions.get_or_404(user_id, txn_id)
        changes = data.model_dump(exclude_unset=True)

        if "amount" in changes and changes["amount"] is not None:
            # Keep the materialised balance in step: back out the old signed
            # value and apply the new one.
            old_signed = self._signed(txn)
            txn.amount = changes.pop("amount")
            await self.accounts.adjust_balance(txn.account_id, self._signed(txn) - old_signed)

        if "category_id" in changes:
            category_id = changes.pop("category_id")
            if category_id and not await self.categories.get(user_id, category_id):
                raise NotFoundError("Category")

            # Record the correction *before* overwriting, so the feedback row
            # keeps what the model predicted and how sure it was. That is what
            # lets the eval harness tell "the model was wrong" from "the model
            # was correctly unsure" (FR-5.5).
            if category_id and category_id != txn.category_id and txn.merchant_normalized:
                await self._categorizer().record_correction(
                    user_id,
                    merchant_normalized=txn.merchant_normalized,
                    corrected_category_id=category_id,
                    transaction_id=txn.id,
                    predicted_category_id=txn.category_id,
                    predicted_confidence=txn.category_confidence,
                    categorizer_version=txn.categorizer_version,
                )

            txn.category_id = category_id
            # A human decision supersedes whatever the model predicted.
            txn.is_reviewed = True
            txn.category_confidence = None

        elif changes.get("is_reviewed") and not txn.is_reviewed:
            # Confirming a suggestion is a label too, and until now it was
            # thrown away. "Yes, that was right" is exactly as informative to
            # the next training run as "no, it wasn't" -- a loop that only
            # learns from its mistakes never learns what it already gets right,
            # and drifts as the corpus grows around it.
            #
            # Only for *model* predictions: confirming a seed-corpus match would
            # re-add a pair the corpus already contains, inflating its weight
            # for no new information.
            if (
                txn.category_id
                and txn.merchant_normalized
                and txn.categorizer_version
                and not txn.categorizer_version.startswith("rules")
            ):
                await self._categorizer().record_correction(
                    user_id,
                    merchant_normalized=txn.merchant_normalized,
                    # Predicted and corrected are equal, which is how the eval
                    # harness tells a confirmation from a correction.
                    corrected_category_id=txn.category_id,
                    transaction_id=txn.id,
                    predicted_category_id=txn.category_id,
                    predicted_confidence=txn.category_confidence,
                    categorizer_version=txn.categorizer_version,
                )

        if "merchant_raw" in changes:
            txn.merchant_raw = changes.pop("merchant_raw")
            txn.merchant_normalized = normalize_merchant(txn.merchant_raw)

        for field, value in changes.items():
            if value is not None:
                setattr(txn, field, value)

        await self.session.flush()
        await self._invalidate(user_id)
        return await self._with_category(txn)

    async def delete_transaction(self, user_id: uuid.UUID, txn_id: uuid.UUID) -> None:
        txn = await self.transactions.get_or_404(user_id, txn_id)

        # Deleting one leg of a transfer and leaving the other would make both
        # account balances wrong. The pair moves together.
        if txn.transfer_pair_id:
            pair = await self.transactions.get(user_id, txn.transfer_pair_id)
            if pair:
                await self.accounts.adjust_balance(pair.account_id, -self._signed(pair))
                pair.soft_delete()

        await self.accounts.adjust_balance(txn.account_id, -self._signed(txn))
        txn.soft_delete()
        await self.session.flush()
        await self._invalidate(user_id)

    # --- budgets ----------------------------------------------------------

    async def create_budget(self, user_id: uuid.UUID, data: BudgetCreate) -> Budget:
        if await self.budgets.find(user_id, data.category_id, data.period_start):
            raise ConflictError("A budget already exists for that category and period")
        if data.category_id and not await self.categories.get(user_id, data.category_id):
            raise NotFoundError("Category")

        return await self.budgets.add(
            Budget(
                user_id=user_id,
                category_id=data.category_id,
                period_start=data.period_start,
                amount_limit=data.amount_limit,
                currency=data.currency,
                rollover_enabled=data.rollover_enabled,
            )
        )

    async def update_budget(
        self, user_id: uuid.UUID, budget_id: uuid.UUID, data: BudgetUpdate
    ) -> Budget:
        budget = await self.budgets.get_or_404(user_id, budget_id)
        for field, value in data.model_dump(exclude_unset=True, exclude_none=True).items():
            setattr(budget, field, value)
        await self.session.flush()
        return budget

    async def budgets_with_spend(
        self, user_id: uuid.UUID, period_start: date
    ) -> list[tuple[Budget, Decimal]]:
        """Budgets for a period, each with its actual spend."""
        period_end = _end_of_month(period_start)
        budgets = await self.budgets.for_period(user_id, period_start)
        spend = await self.transactions.spend_by_category(user_id, period_start, period_end)
        total = sum(spend.values(), Decimal("0"))

        return [
            (b, total if b.category_id is None else spend.get(b.category_id, Decimal("0")))
            for b in budgets
        ]

    async def copy_budgets_forward(self, user_id: uuid.UUID, period_start: date) -> list[Budget]:
        """Roll the previous month's budgets into this one."""
        previous = _previous_month(period_start)
        source = await self.budgets.for_period(user_id, previous)
        created: list[Budget] = []

        for old in source:
            if await self.budgets.find(user_id, old.category_id, period_start):
                continue
            created.append(
                await self.budgets.add(
                    Budget(
                        user_id=user_id,
                        category_id=old.category_id,
                        period_start=period_start,
                        amount_limit=old.amount_limit,
                        currency=old.currency,
                        rollover_enabled=old.rollover_enabled,
                    )
                )
            )
        return created

    # --- goals ------------------------------------------------------------

    async def create_goal(self, user_id: uuid.UUID, data: GoalCreate) -> Goal:
        if data.linked_account_id:
            await self.accounts.get_or_404(user_id, data.linked_account_id)
        return await self.goals.add(
            Goal(
                user_id=user_id,
                name=data.name,
                target_amount=data.target_amount,
                currency=data.currency,
                target_date=data.target_date,
                linked_account_id=data.linked_account_id,
                priority=data.priority,
            )
        )

    async def update_goal(self, user_id: uuid.UUID, goal_id: uuid.UUID, data: GoalUpdate) -> Goal:
        goal = await self.goals.get_or_404(user_id, goal_id)
        for field, value in data.model_dump(exclude_unset=True, exclude_none=True).items():
            setattr(goal, field, value.value if hasattr(value, "value") else value)
        await self.session.flush()
        return goal

    async def contribute_to_goal(
        self, user_id: uuid.UUID, goal_id: uuid.UUID, amount: Decimal
    ) -> Goal:
        goal = await self.goals.get_or_404(user_id, goal_id)
        goal.current_amount = Decimal(goal.current_amount) + amount
        if goal.current_amount >= goal.target_amount:
            goal.status = "achieved"
        await self.session.flush()
        return goal

    # --- recurring --------------------------------------------------------

    async def create_recurring(self, user_id: uuid.UUID, data: RecurringCreate) -> RecurringItem:
        if data.account_id:
            await self.accounts.get_or_404(user_id, data.account_id)
        return await self.recurring.add(
            RecurringItem(
                user_id=user_id,
                account_id=data.account_id,
                category_id=data.category_id,
                name=data.name,
                kind=data.kind,
                item_type=data.item_type.value,
                amount=data.amount,
                currency=data.currency,
                cadence=data.cadence.value,
                next_due_on=data.next_due_on,
                end_on=data.end_on,
            )
        )

    async def update_recurring(
        self, user_id: uuid.UUID, item_id: uuid.UUID, data: RecurringUpdate
    ) -> RecurringItem:
        item = await self.recurring.get_or_404(user_id, item_id)
        for field, value in data.model_dump(exclude_unset=True, exclude_none=True).items():
            setattr(item, field, value)
        await self.session.flush()
        return item

    async def upsert_detected_recurring(
        self,
        user_id: uuid.UUID,
        detected: list[DetectedRecurring],
    ) -> dict[str, int]:
        """Record automatically detected recurring commitments.

        Called by the forecasting module, which owns the detection algorithm;
        this owns the table. Matching is by `(name, cadence)` because the
        detector keys on a normalised merchant, which is stable across the
        narration noise that would otherwise create a new row every month.

        **User-created items are never touched.** Someone who typed in their
        rent has stated a fact; a detector second-guessing it would overwrite a
        decision with an inference.
        """
        existing = {
            (item.name, item.cadence): item
            for item in await self.recurring.list_all(user_id, active_only=False)
        }

        created = updated = 0
        for row in detected:
            key = (row.name, row.cadence)
            current = existing.get(key)

            if current is None:
                self.session.add(
                    RecurringItem(
                        user_id=user_id,
                        account_id=row.account_id,
                        category_id=row.category_id,
                        name=row.name,
                        kind=row.kind,
                        item_type=row.item_type,
                        amount=row.amount,
                        currency="INR",
                        cadence=row.cadence,
                        next_due_on=row.next_due_on,
                        amount_variance=row.amount_variance,
                        detection_confidence=row.confidence,
                        first_seen_on=row.first_seen_on,
                        is_auto_detected=True,
                    )
                )
                created += 1
            elif current.is_auto_detected:
                current.amount = row.amount
                current.next_due_on = row.next_due_on
                current.amount_variance = row.amount_variance
                current.detection_confidence = row.confidence
                # Never moves forward: the pattern's start is a fact about the
                # past, and letting a later detection overwrite it would make an
                # old commitment look new again.
                current.first_seen_on = current.first_seen_on or row.first_seen_on
                updated += 1

        await self.session.flush()
        return {"created": created, "updated": updated}

    # --- reconciliation ---------------------------------------------------

    async def reconcile_balances(self, user_id: uuid.UUID) -> list[dict[str, object]]:
        """Verify each materialised balance against the ledger.

        Drift means a write path adjusted the balance incorrectly. Reporting it
        is the point -- a silent correction would hide the bug that caused it.
        """
        drifts: list[dict[str, object]] = []

        for account in await self.accounts.list_all(user_id, include_archived=True):
            expected = await self.accounts.ledger_balance(account.id)
            actual = Decimal(account.current_balance)
            if expected != actual:
                drifts.append(
                    {
                        "account_id": str(account.id),
                        "name": account.name,
                        "materialised": str(actual),
                        "ledger": str(expected),
                        "drift": str(actual - expected),
                    }
                )
                logger.warning(
                    "account balance drift",
                    extra={"account_id": str(account.id), "drift": str(actual - expected)},
                )
        return drifts


def _end_of_month(start: date) -> date:
    """Last calendar day of the month `start` falls in."""
    return date(start.year, start.month, monthrange(start.year, start.month)[1])


def _previous_month(start: date) -> date:
    return date(start.year - 1, 12, 1) if start.month == 1 else date(start.year, start.month - 1, 1)
