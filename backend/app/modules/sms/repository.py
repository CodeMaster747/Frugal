"""Tenant-scoped access to SMS intake rows and account identifiers."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.core.repository import BaseRepository
from app.modules.sms.models import AccountIdentifier, SmsMessage


class AccountIdentifierRepository(BaseRepository[AccountIdentifier]):
    model = AccountIdentifier

    async def find(
        self, user_id: uuid.UUID, *, kind: str, value: str, confirmed_only: bool = True
    ) -> AccountIdentifier | None:
        """The account a handle names, if the user has said so.

        `confirmed_only` defaults to True and callers on the commit path must
        leave it that way. An unconfirmed identifier is a suggestion the
        reconciliation screen made; treating it as fact writes money to an
        account the user never agreed to, and every balance, budget and
        forecast downstream reads that as truth.
        """
        stmt = self.scoped_select(user_id).where(self.model.kind == kind, self.model.value == value)
        if confirmed_only:
            stmt = stmt.where(self.model.confirmed_at.is_not(None))
        return (await self.session.execute(stmt.limit(1))).scalar_one_or_none()

    async def for_account(
        self, user_id: uuid.UUID, account_id: uuid.UUID
    ) -> list[AccountIdentifier]:
        stmt = self.scoped_select(user_id).where(self.model.account_id == account_id)
        return list((await self.session.execute(stmt)).scalars())

    async def all_for_user(self, user_id: uuid.UUID) -> list[AccountIdentifier]:
        stmt = self.scoped_select(user_id).order_by(self.model.kind, self.model.value)
        return list((await self.session.execute(stmt)).scalars())

    async def unconfirmed(self, user_id: uuid.UUID) -> list[AccountIdentifier]:
        """Suggestions awaiting a human. Drives the reconciliation screen."""
        stmt = self.scoped_select(user_id).where(self.model.confirmed_at.is_(None))
        return list((await self.session.execute(stmt)).scalars())


class SmsMessageRepository(BaseRepository[SmsMessage]):
    model = SmsMessage

    async def by_message_hash(self, user_id: uuid.UUID, message_hash: str) -> SmsMessage | None:
        """Intake-level dedup.

        Checked before parsing, not after: re-reading fifty thousand messages
        on a second XML import of an overlapping period is the difference
        between a job that finishes and one that times out.
        """
        stmt = self.scoped_select(user_id).where(self.model.message_hash == message_hash)
        return (await self.session.execute(stmt.limit(1))).scalar_one_or_none()

    async def existing_hashes(self, user_id: uuid.UUID, hashes: list[str]) -> set[str]:
        """Which of these we already hold. One query, not one per message."""
        if not hashes:
            return set()
        stmt = select(self.model.message_hash).where(
            self.model.user_id == user_id,
            self.model.deleted_at.is_(None),
            self.model.message_hash.in_(hashes),
        )
        return set((await self.session.execute(stmt)).scalars())

    async def queue(self, user_id: uuid.UUID, *, statuses: list[str]) -> list[SmsMessage]:
        """The review queue, newest first."""
        stmt = (
            self.scoped_select(user_id)
            .where(self.model.status.in_(statuses))
            .order_by(self.model.received_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars())
