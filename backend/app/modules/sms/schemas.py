"""Wire types for SMS ingestion."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from app.modules.sms.models import IdentifierKind, SmsIntake


class SmsParseIn(BaseModel):
    """One message to read. Nothing is stored by this request."""

    body: str = Field(min_length=1, max_length=2000)
    #: The DLT header. Optional so the paste box works without one -- a user
    #: copying a message out of their inbox rarely copies the sender.
    sender: str = Field(default="", max_length=32)
    #: When the handset received it. Defaults to now, which is right for a
    #: paste and wrong for a backfill, so the XML importer always sets it.
    received_at: datetime | None = None
    intake: SmsIntake = SmsIntake.PASTE


class ParsedSmsOut(BaseModel):
    """What one message was read as, and how sure that reading is."""

    model_config = ConfigDict(from_attributes=True)

    template_id: str
    issuer: str
    rail: str
    direction: str
    amount: Decimal
    confidence: Decimal
    occurred_on: date | None
    date_inferred: bool
    merchant: str | None
    vpa: str | None
    account_tail: str | None
    reference: str | None
    reversal: bool
    #: Why the confidence is not the template's ceiling. Rendered verbatim.
    caveats: list[str]

    @field_serializer("amount", "confidence", when_used="json")
    def _decimals(self, value: Decimal) -> str:
        return format(value, "f")


class SmsParseOut(BaseModel):
    """The result of reading one message without storing it.

    `parsed` is null when nothing matched, which is an answer rather than an
    error: a promotional message from a bank header is correctly read as not a
    transaction, and the caller should say so rather than show a failure.
    """

    parsed: ParsedSmsOut | None
    #: Whether this reading would be committed automatically, held for review,
    #: or blocked for want of an account mapping. Named so the paste screen can
    #: tell the user what will happen before they agree to it.
    disposition: str
    reason: str
    #: The account this message's tail resolves to, when it resolves.
    resolved_account_id: uuid.UUID | None = None
    #: Present when the tail matched nothing the user has confirmed.
    unmapped_tail: str | None = None


class SmsIngestIn(SmsParseIn):
    """One message to read *and* store."""


class SmsBatchIn(BaseModel):
    """Many messages at once -- the device drain and the share sheet.

    Capped because this is an authenticated write endpoint that does real work
    per item. A backup file goes through the XML importer, which is a job.
    """

    messages: list[SmsIngestIn] = Field(min_length=1, max_length=500)


class SmsBatchOut(BaseModel):
    created: int
    already_held: int
    filtered: int
    committed: int


class SmsMessageOut(BaseModel):
    """A row in the review queue, with its working shown."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    intake: str
    sender: str
    received_at: datetime
    #: The redacted body. The original is dropped once the message is resolved.
    body_redacted: str
    status: str
    template_id: str | None
    confidence: Decimal | None
    parsed: dict[str, object] | None
    resolved_account_id: uuid.UUID | None
    transaction_id: uuid.UUID | None

    @field_serializer("confidence", when_used="json")
    def _confidence(self, value: Decimal | None) -> str | None:
        return format(value, "f") if value is not None else None


class DuplicateCandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    transaction_id: uuid.UUID
    occurred_on: date
    amount: Decimal
    merchant: str | None
    similarity: Decimal

    @field_serializer("amount", "similarity", when_used="json")
    def _decimals(self, value: Decimal) -> str:
        return format(value, "f")


class SmsQueueOut(BaseModel):
    messages: list[SmsMessageOut]
    #: Unmapped account handles seen in the queue, most frequent first. The tail
    #: on ninety messages is the user's main account; mapping it first clears
    #: most of the queue in one action.
    unmapped_tails: dict[str, int]


class SmsCommitIn(BaseModel):
    #: Overrides the resolved mapping, so the review screen can correct a
    #: misattribution without the user first having to fix the mapping.
    account_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None
    allow_duplicate: bool = False


class AccountMappingIn(BaseModel):
    kind: IdentifierKind
    value: str = Field(min_length=1, max_length=64)
    account_id: uuid.UUID


class SmsImportJobOut(BaseModel):
    """A queued backup import, for the client to poll.

    `progress` and `result` share a shape, so the UI renders one component
    throughout rather than switching on whether the job has finished.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    progress: dict[str, int] | None = None
    result: dict[str, int] | None = None
    error_message: str | None = None


class AccountMappingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID
    kind: str
    value: str
    is_primary: bool
    confirmed_at: datetime | None
    source: str
