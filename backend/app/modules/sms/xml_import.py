"""Reading an "SMS Backup & Restore" export.

This is the path that makes the whole feature useful without any permission at
all: the user exports their messages with an app that already holds the SMS
permission legitimately, uploads the file here, and gets years of history in one
go. It works in a browser, on iOS, and in the no-permission APK build -- which
is why it, and not live capture, is the primary ingestion route.

**The file is untrusted input.** It arrives over an authenticated endpoint, but
authenticated is not the same as trustworthy, and `xml.etree` is documented as
vulnerable to entity-expansion attacks. Four defences, because this is a public
upload on a financial product:

1. `defusedxml` refuses entity declarations outright.
2. A byte cap, checked before parsing begins.
3. An element cap, checked while parsing.
4. `iterparse` with `elem.clear()`, so memory stays flat regardless of input.

Any one of these would probably do. Together they mean a hostile file fails on
a cheap check rather than an expensive one.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import IO
from xml.etree.ElementTree import ParseError

from defusedxml.ElementTree import iterparse

from app.core.errors import ValidationError

#: Roughly three years of messages for a heavy user. The existing 5 MB upload
#: cap in the finance importer is sized for a CSV statement and would reject
#: most real backups.
MAX_XML_BYTES = 25 * 1024 * 1024

#: A hard stop on element count, independent of byte size. A compressed
#: expansion attack is small on disk and enormous in the parser.
MAX_ELEMENTS = 200_000


@dataclass(frozen=True, slots=True)
class BackupMessage:
    body: str
    sender: str
    received_at: datetime


def _to_datetime(millis: str) -> datetime | None:
    """SMS Backup & Restore writes `date` as epoch milliseconds.

    It also writes a human-readable `readable_date` in the device's locale,
    which is unparseable without knowing that locale. The epoch field is the
    only unambiguous one.
    """
    try:
        value = int(millis)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def iter_messages(stream: IO[bytes]) -> Iterator[BackupMessage]:
    """Yield received messages from a backup file, streaming.

    Only inbound messages (`type="1"`), because a message the user *sent* is
    never a bank alert, and parsing their outgoing texts would be both useless
    and a straightforward breach of what this feature says it does.

    Malformed rows are skipped rather than raising. A backup is tens of
    thousands of messages written by a third-party app across years of Android
    versions; one unreadable row must not cost the user the other 49,999.
    """
    context = iterparse(stream, events=("start", "end"))

    # The first event is the opening <smses>. Holding a reference to it is what
    # makes the clearing below work: ElementTree keeps every parsed child on the
    # root, so clearing the element alone still leaks one empty node per message
    # -- fifty thousand of them by the end of a real backup.
    try:
        _, root = next(context)
    except StopIteration:
        raise ValidationError("That file contains no XML.") from None
    except ParseError as exc:
        # A user who picks the wrong file -- a photo, a zip, a half-finished
        # export -- should be told that, not handed a 500. Malformed input is
        # an ordinary outcome for a file upload, not an internal failure.
        raise ValidationError(f"That file is not readable XML: {exc}") from exc

    seen = 0
    while True:
        try:
            event, elem = next(context)
        except StopIteration:
            break
        except ParseError as exc:
            # Truncated mid-file. Everything yielded so far is already ingested
            # and stays ingested -- a backup that stops early is still worth
            # what it delivered.
            raise ValidationError(f"That file ends unexpectedly: {exc}") from exc

        if event != "end" or elem.tag != "sms":
            continue

        seen += 1
        if seen > MAX_ELEMENTS:
            raise ValidationError(
                f"This backup holds more than {MAX_ELEMENTS:,} messages. "
                "Export a narrower date range and try again."
            )

        try:
            # type="1" is inbound. A message the user *sent* is never a bank
            # alert, and reading their outgoing texts would be both useless and
            # a plain breach of what this feature says it does.
            if elem.get("type") != "1":
                continue
            body = (elem.get("body") or "").strip()
            sender = (elem.get("address") or "").strip()
            received_at = _to_datetime(elem.get("date") or "")
            if body and sender and received_at is not None:
                yield BackupMessage(body=body, sender=sender, received_at=received_at)
        finally:
            elem.clear()
            root.clear()


def check_size(size_bytes: int) -> None:
    """Reject an oversized file before a single byte is parsed."""
    if size_bytes > MAX_XML_BYTES:
        raise ValidationError(
            f"The file is larger than {MAX_XML_BYTES // (1024 * 1024)} MB. "
            "Export a narrower date range and try again."
        )
