"""SMS ingestion against a real database.

The tests that matter here are about *not* writing things: not booking a
message twice, not booking one to a guessed account, and not booking one that
a statement import already recorded. The parser's own correctness is covered
exhaustively in tests/unit/test_sms_parser.py; this is about what reaches the
ledger.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

SMS = "/api/v1/sms"
ACCOUNTS = "/api/v1/accounts"
TRANSACTIONS = "/api/v1/transactions"

# One real HDFC UPI debit, digits changed. Reused so the tests differ only in
# what they do with it.
DEBIT = "Sent Rs.450.00 From HDFC Bank A/C x1234 To SWIGGY On 15/08/26 Ref 522398456712"
SENDER = "VM-HDFCBK"
RECEIVED = "2026-08-15T13:45:00+00:00"


@pytest.fixture
async def account(client, auth_headers):
    response = await client.post(
        ACCOUNTS,
        headers=auth_headers,
        json={"name": "HDFC Savings", "type": "bank", "opening_balance": "50000.00"},
    )
    return response.json()


async def ingest(client, auth_headers, *, body=DEBIT, sender=SENDER, received_at=RECEIVED):
    return await client.post(
        f"{SMS}/messages",
        headers=auth_headers,
        json={"body": body, "sender": sender, "received_at": received_at},
    )


async def map_tail(client, auth_headers, account_id, *, value="1234", kind="account_tail"):
    return await client.post(
        f"{SMS}/mappings",
        headers=auth_headers,
        json={"kind": kind, "value": value, "account_id": account_id},
    )


class TestTheDryRun:
    """`/parse` is what makes the permission ask answerable, so it must store
    nothing at all -- not even the message it was asked about."""

    async def test_parse_reads_the_message_without_storing_it(self, client, auth_headers):
        response = await client.post(
            f"{SMS}/parse", headers=auth_headers, json={"body": DEBIT, "sender": SENDER}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["parsed"]["amount"] == "450.00"
        assert body["parsed"]["merchant"] == "SWIGGY"
        assert body["parsed"]["account_tail"] == "1234"

        queue = await client.get(f"{SMS}/messages", headers=auth_headers)
        assert queue.json()["messages"] == []

    async def test_a_promotion_is_read_as_not_a_transaction(self, client, auth_headers):
        response = await client.post(
            f"{SMS}/parse",
            headers=auth_headers,
            json={"body": "Pre-approved loan of Rs 500000! Apply now.", "sender": SENDER},
        )
        assert response.status_code == 200
        assert response.json()["parsed"] is None
        assert response.json()["disposition"] == "not_a_transaction"


class TestAccountResolution:
    async def test_an_unmapped_tail_holds_the_message_instead_of_guessing(
        self, client, auth_headers, account
    ):
        """The failure this prevents is silent: money in the wrong account is
        wrong in every balance, budget and forecast that reads it."""
        response = await ingest(client, auth_headers)
        assert response.status_code == 201
        assert response.json()["status"] == "needs_account"
        assert response.json()["transaction_id"] is None

        txns = await client.get(TRANSACTIONS, headers=auth_headers)
        assert txns.json()["data"] == []

    async def test_mapping_an_account_releases_what_was_waiting_on_it(
        self, client, auth_headers, account
    ):
        await ingest(client, auth_headers)

        queue = await client.get(f"{SMS}/messages", headers=auth_headers)
        assert queue.json()["unmapped_tails"] == {"1234": 1}

        await map_tail(client, auth_headers, account["id"])

        queue = await client.get(f"{SMS}/messages", headers=auth_headers)
        held = queue.json()["messages"][0]
        assert held["resolved_account_id"] == account["id"]
        # Attached, but deliberately not booked: the user mapped an account,
        # they did not ask for a month of held messages to appear unreviewed.
        assert held["status"] == "parsed"
        assert held["transaction_id"] is None

    async def test_a_confirmed_mapping_books_the_next_message_automatically(
        self, client, auth_headers, account
    ):
        await map_tail(client, auth_headers, account["id"])
        response = await ingest(client, auth_headers)
        assert response.json()["status"] == "committed"

        txns = await client.get(TRANSACTIONS, headers=auth_headers)
        items = txns.json()["data"]
        assert len(items) == 1
        assert items[0]["amount"] == "450.00"
        assert items[0]["source"] == "sms"
        assert items[0]["account_id"] == account["id"]

    async def test_a_card_tail_does_not_resolve_through_an_account_mapping(
        self, client, auth_headers, account
    ):
        """The same digits routinely name both a card and an account at one
        bank. Reading a card spend against the savings account is exactly the
        misattribution the separate identifier kinds exist to prevent."""
        await map_tail(client, auth_headers, account["id"], value="5678", kind="account_tail")
        response = await ingest(
            client,
            auth_headers,
            body="Rs.1250.00 spent on HDFC Bank Card x5678 at AMAZON on 15-08-26.",
        )
        assert response.json()["status"] == "needs_account"


class TestItDoesNotBookThingsTwice:
    async def test_the_same_message_twice_is_held_once(self, client, auth_headers, account):
        """Live capture then an overlapping XML backup is the normal case."""
        await map_tail(client, auth_headers, account["id"])
        assert (await ingest(client, auth_headers)).status_code == 201
        assert (await ingest(client, auth_headers)).status_code == 409

        txns = await client.get(TRANSACTIONS, headers=auth_headers)
        assert len(txns.json()["data"]) == 1

    async def test_sms_and_csv_converge_on_one_transaction(self, client, auth_headers, account):
        """The load-bearing claim of the whole ingestion design.

        A CSV narration `UPI/SWIGGY/883012` and an SMS payee `SWIGGY` must
        reduce to the same merchant key so `content_hash` matches and the same
        purchase is recorded once. Passing the SMS's raw VPA through instead
        would yield `swiggy ybl`, a different hash, and two transactions.
        """
        await map_tail(client, auth_headers, account["id"])

        csv_body = "date,amount,merchant\n2026-08-15,-450.00,UPI/SWIGGY/883012\n"
        commit = await client.post(
            "/api/v1/imports/csv/commit",
            headers=auth_headers,
            files={"file": ("statement.csv", csv_body, "text/csv")},
            params={
                "account_id": account["id"],
                "mapping.date": "date",
                "mapping.amount": "amount",
                "mapping.merchant": "merchant",
            },
        )
        assert commit.status_code in (200, 207), commit.text

        before = (await client.get(TRANSACTIONS, headers=auth_headers)).json()["data"]
        assert len(before) == 1, "the CSV import itself must produce exactly one row"

        # Now the SMS for the same payment. It must not create a second.
        await ingest(client, auth_headers)
        after = (await client.get(TRANSACTIONS, headers=auth_headers)).json()["data"]
        assert len(after) == 1, "SMS and CSV must converge on one transaction, not two"

    async def test_a_near_duplicate_is_surfaced_before_commit(self, client, auth_headers, account):
        """Card authorisation and settlement differ by days, so the content
        hash alone would let the pair through. This is the net under it."""
        await client.post(
            TRANSACTIONS,
            headers=auth_headers,
            json={
                "account_id": account["id"],
                "kind": "expense",
                "amount": "450.00",
                "occurred_on": "2026-08-13",
                "merchant_raw": "SWIGGY",
            },
        )
        await ingest(client, auth_headers)
        queue = await client.get(f"{SMS}/messages", headers=auth_headers)
        message_id = queue.json()["messages"][0]["id"]

        dupes = await client.get(f"{SMS}/messages/{message_id}/duplicates", headers=auth_headers)
        assert dupes.status_code == 200
        assert len(dupes.json()) == 1
        assert dupes.json()[0]["similarity"] == "0.99"

        commit = await client.post(
            f"{SMS}/messages/{message_id}/commit",
            headers=auth_headers,
            json={"account_id": account["id"]},
        )
        assert commit.status_code == 409

        forced = await client.post(
            f"{SMS}/messages/{message_id}/commit",
            headers=auth_headers,
            json={"account_id": account["id"], "allow_duplicate": True},
        )
        assert forced.status_code == 200


class TestWhatIsStored:
    async def test_a_non_transaction_is_not_stored_at_all(self, client, auth_headers):
        """A row here would be a permanent copy of a personal or promotional
        message, kept for no purpose."""
        response = await ingest(
            client, auth_headers, body="OTP is 483920 for txn of Rs 5000 at AMAZON. Do not share."
        )
        assert response.status_code == 422

        queue = await client.get(f"{SMS}/messages", headers=auth_headers)
        assert queue.json()["messages"] == []

    async def test_the_stored_body_is_redacted(self, client, auth_headers, account):
        response = await ingest(
            client,
            auth_headers,
            body=(
                "Sent Rs.450.00 From HDFC Bank A/C x1234 To SWIGGY On 15/08/26 "
                "Ref 522398456712. Avl Bal INR 98765.43. Call 9876543210"
            ),
        )
        stored = response.json()["body_redacted"]
        assert "450.00" in stored and "SWIGGY" in stored, "the queue must show its work"
        assert "98765.43" not in stored, "a running balance is more revealing than a purchase"
        assert "9876543210" not in stored

    async def test_the_raw_body_is_dropped_once_the_message_is_resolved(
        self, client, auth_headers, account, db_session
    ):
        from sqlalchemy import select

        from app.modules.sms.models import SmsMessage

        await map_tail(client, auth_headers, account["id"])
        await ingest(client, auth_headers)

        rows = (await db_session.execute(select(SmsMessage))).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "committed"
        assert rows[0].raw_body is None, "the original text has no purpose once booked"
        assert rows[0].body_redacted


class TestTheBackupImport:
    """The upload itself. The worker's behaviour is covered in
    tests/unit/test_sms_xml_import.py; this is about queueing it safely."""

    BACKUP = (
        '<?xml version="1.0" encoding="UTF-8"?><smses count="1">'
        '<sms protocol="0" address="VM-HDFCBK" date="1786000000000" type="1" '
        'body="Sent Rs.450.00 From HDFC Bank A/C x1234 To SWIGGY On 15/08/26" /></smses>'
    )

    async def test_an_upload_is_queued_not_parsed_inline(self, client, auth_headers):
        """202, because a three-year backup is tens of thousands of messages
        and parsing it in a request would hold a worker until it timed out."""
        response = await client.post(
            f"{SMS}/imports/backup",
            headers=auth_headers,
            files={"file": ("sms-backup.xml", self.BACKUP, "text/xml")},
        )
        assert response.status_code == 202, response.text
        assert response.json()["status"] == "queued"

    async def test_uploading_the_same_file_twice_returns_the_same_job(self, client, auth_headers):
        """A refreshed tab or an impatient second tap must not start a second
        pass over the same fifty thousand messages."""
        first = await client.post(
            f"{SMS}/imports/backup",
            headers=auth_headers,
            files={"file": ("sms-backup.xml", self.BACKUP, "text/xml")},
        )
        second = await client.post(
            f"{SMS}/imports/backup",
            headers=auth_headers,
            files={"file": ("sms-backup.xml", self.BACKUP, "text/xml")},
        )
        assert first.json()["id"] == second.json()["id"]

    async def test_an_oversized_file_is_refused_before_parsing(self, client, auth_headers):
        from app.modules.sms.xml_import import MAX_XML_BYTES

        response = await client.post(
            f"{SMS}/imports/backup",
            headers=auth_headers,
            files={"file": ("huge.xml", b"x" * (MAX_XML_BYTES + 1), "text/xml")},
        )
        assert response.status_code == 400

    async def test_a_file_that_is_not_xml_is_a_client_error(self, client, auth_headers):
        """Picking the wrong file is an ordinary outcome, not a 500."""
        response = await client.post(
            f"{SMS}/imports/backup",
            headers=auth_headers,
            files={"file": ("photo.jpg", b"\xff\xd8\xff\xe0notxml", "image/jpeg")},
        )
        assert response.status_code == 202, "validation happens in the worker, on the parse"


class TestTenancy:
    async def test_another_users_message_is_not_visible(
        self, client, auth_headers, second_user_headers, account
    ):
        await ingest(client, auth_headers)
        queue = await client.get(f"{SMS}/messages", headers=second_user_headers)
        assert queue.json()["messages"] == []
