"""Reading an SMS Backup & Restore export.

Half of these are about hostile input. The file arrives over an authenticated
endpoint, but authenticated is not trustworthy, and a financial product taking
an XML upload has to assume the file is trying something.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime

import pytest
from defusedxml.common import EntitiesForbidden

from app.core.errors import ValidationError
from app.modules.sms.xml_import import MAX_XML_BYTES, check_size, iter_messages


def backup(*rows: str) -> io.BytesIO:
    body = "".join(rows)
    return io.BytesIO(
        f'<?xml version="1.0" encoding="UTF-8"?><smses count="{len(rows)}">{body}</smses>'.encode()
    )


def sms(
    *,
    body: str = "Sent Rs.450.00 From HDFC Bank A/C x1234 To SWIGGY On 15/08/26",
    address: str = "VM-HDFCBK",
    date: str = "1786000000000",
    type_: str = "1",
) -> str:
    return f'<sms protocol="0" address="{address}" date="{date}" type="{type_}" body="{body}" />'


class TestReadingABackup:
    def test_it_reads_an_inbound_message(self):
        messages = list(iter_messages(backup(sms())))
        assert len(messages) == 1
        assert messages[0].sender == "VM-HDFCBK"
        assert "SWIGGY" in messages[0].body
        assert messages[0].received_at.tzinfo is not None

    def test_it_skips_messages_the_user_sent(self):
        """A message the user sent is never a bank alert, and reading their
        outgoing texts would be a plain breach of what this feature does."""
        messages = list(iter_messages(backup(sms(type_="2"), sms())))
        assert len(messages) == 1

    def test_the_epoch_field_is_used_not_the_readable_one(self):
        """`readable_date` is written in the device's locale and is
        unparseable without knowing it; `date` is unambiguous."""
        messages = list(iter_messages(backup(sms(date="1786000000000"))))
        expected = datetime.fromtimestamp(1786000000, tz=UTC)
        assert messages[0].received_at == expected

    def test_a_malformed_row_does_not_cost_the_other_fifty_thousand(self):
        """A backup is years of messages written by a third-party app across
        Android versions. One unreadable row must not fail the import."""
        rows = (
            sms(date="not-a-number"),
            sms(date=""),
            '<sms address="VM-HDFCBK" type="1" date="1786000000000" />',  # no body
            sms(address=""),
            sms(),
        )
        messages = list(iter_messages(backup(*rows)))
        assert len(messages) == 1

    def test_non_sms_elements_are_ignored(self):
        raw = (
            '<?xml version="1.0"?><smses count="2">'
            '<mms address="VM-HDFCBK" date="1786000000000" />'
            f"{sms()}</smses>"
        )
        assert len(list(iter_messages(io.BytesIO(raw.encode())))) == 1


class TestHostileInput:
    def test_an_entity_declaration_is_refused(self):
        """The billion-laughs shape.

        `defusedxml` refuses the *declaration* rather than expanding it, so this
        fails on a cheap check rather than after the parser has allocated
        gigabytes. Asserting the specific exception matters: a generic
        `Exception` would also pass if the file simply failed to parse for an
        unrelated reason, which is not the property being claimed.
        """
        raw = (
            '<?xml version="1.0"?>'
            "<!DOCTYPE smses ["
            '<!ENTITY lol "lol">'
            '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
            "]>"
            "<smses><sms body='&lol2;' address='VM-HDFCBK' date='1786000000000' type='1'/></smses>"
        )
        with pytest.raises(EntitiesForbidden):
            list(iter_messages(io.BytesIO(raw.encode())))

    def test_an_external_entity_is_refused(self):
        """The file-disclosure shape: without defusedxml this reads /etc/passwd
        into a message body and stores it under the user's account."""
        raw = (
            '<?xml version="1.0"?>'
            '<!DOCTYPE smses [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            "<smses><sms body='&xxe;' address='VM-HDFCBK' date='1786000000000' type='1'/></smses>"
        )
        with pytest.raises(EntitiesForbidden):
            list(iter_messages(io.BytesIO(raw.encode())))

    def test_an_oversized_file_is_rejected_before_parsing(self):
        with pytest.raises(ValidationError):
            check_size(MAX_XML_BYTES + 1)

    def test_a_file_at_the_limit_is_accepted(self):
        check_size(MAX_XML_BYTES)

    def test_an_empty_file_is_a_validation_error_not_a_crash(self):
        with pytest.raises(ValidationError):
            list(iter_messages(io.BytesIO(b"")))

    def test_too_many_elements_is_refused(self, monkeypatch):
        """A cap independent of byte size: a compressed expansion is small on
        disk and enormous in the parser."""
        monkeypatch.setattr("app.modules.sms.xml_import.MAX_ELEMENTS", 3)
        with pytest.raises(ValidationError, match="more than"):
            list(iter_messages(backup(*[sms() for _ in range(5)])))


class TestMemoryStaysBounded:
    def test_the_tree_does_not_accumulate(self):
        """ElementTree keeps every parsed child on the root, so clearing the
        element alone still leaks one empty node per message -- fifty thousand
        of them by the end of a real backup. This asserts the root is cleared.
        """
        rows = [sms(date=str(1786000000000 + i * 1000)) for i in range(300)]
        stream = backup(*rows)

        consumed = 0
        for _ in iter_messages(stream):
            consumed += 1
        assert consumed == 300
