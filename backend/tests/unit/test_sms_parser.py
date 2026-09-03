"""The SMS parser, against a corpus of real messages with the digits changed.

Three kinds of test here, and the third is the one that matters in a year:

1. Every fixture reads as its label says.
2. Nothing in the negative corpus reads as anything.
3. Structural tests over the registry, which fail when someone adds a template
   without a fixture or renames a capture group. Those are what stop the
   template directory rotting into patterns nobody can safely change.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest

from app.modules.sms.parser import parse
from app.modules.sms.parser.normalize import (
    account_tail,
    parse_amount,
    parse_date,
    redact,
    split_vpa,
)
from app.modules.sms.parser.registry import TEMPLATES
from app.modules.sms.parser.senders import is_capturable, issuer_for, looks_transactional
from app.modules.sms.parser.types import Precedence, Rail
from tests.fixtures.sms import CASES, NEGATIVE_CASES, RECEIVED_AT, SmsCase


def _ids(cases: tuple[SmsCase, ...]) -> list[str]:
    return [f"{case.template_id}::{case.amount}" for case in cases]


class TestTheCorpusReadsCorrectly:
    @pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
    def test_message_parses_to_its_label(self, case: SmsCase):
        parsed = parse(case.body, sender=case.sender, received_at=RECEIVED_AT)

        assert parsed is not None, f"{case.template_id} did not parse at all"
        assert parsed.template_id == case.template_id
        assert parsed.direction == case.direction
        assert parsed.amount == case.amount
        assert parsed.occurred_on == case.occurred_on
        assert parsed.date_inferred == case.date_inferred
        assert parsed.account_tail == case.account_tail
        assert parsed.merchant == case.merchant
        assert parsed.reference == case.reference
        assert parsed.vpa == case.vpa
        assert parsed.reversal == case.reversal

    @pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
    def test_confidence_clears_the_case_floor(self, case: SmsCase):
        """Each case declares the confidence it must reach.

        Cases that should land in review declare a floor below the commit
        threshold -- so a change that made everything confident would fail here
        rather than quietly start auto-committing doubtful reads.
        """
        parsed = parse(case.body, sender=case.sender, received_at=RECEIVED_AT)
        assert parsed is not None
        assert parsed.confidence >= case.min_confidence
        assert Decimal("0") <= parsed.confidence <= Decimal("1")


class TestTheNegativeCorpus:
    """A message that is not a transaction must produce nothing.

    An invented transaction is materially worse than a missed one: the user can
    add a row by hand, but they cannot know to remove one they never made.
    """

    @pytest.mark.parametrize(
        ("sender", "body", "why"),
        NEGATIVE_CASES,
        ids=[c[2][:40] for c in NEGATIVE_CASES],
    )
    def test_non_transactions_do_not_parse(self, sender: str, body: str, why: str):
        assert parse(body, sender=sender, received_at=RECEIVED_AT) is None, why

    def test_an_unknown_sender_that_looks_transactional_still_parses(self):
        """The negative corpus must not be over-broad.

        Rejecting everything would pass every test above and ship a useless
        feature, so this asserts the gate is a filter rather than a wall.
        """
        parsed = parse(
            "INR 560.00 debited from A/c no. XX2211 on 15-08-26 at DUNZO.",
            sender="VM-ZZZBNK",
            received_at=RECEIVED_AT,
        )
        assert parsed is not None
        assert parsed.confidence < Decimal("0.75"), "an unknown sender must not auto-commit"


class TestTheRegistryStaysHonest:
    """Structural tests. These are the ones that still matter in a year."""

    def test_every_template_has_a_fixture(self):
        """A template with no case is a pattern nobody can safely change.

        This is the guard that keeps the corpus growing with the registry --
        the same job test_all_tenant_repositories_emit_a_user_predicate does
        for repositories.
        """
        covered = {case.template_id for case in CASES}
        missing = sorted({t.id for t in TEMPLATES} - covered)
        assert not missing, f"templates with no fixture: {missing}"

    def test_no_fixture_names_a_template_that_does_not_exist(self):
        known = {t.id for t in TEMPLATES}
        orphans = sorted({c.template_id for c in CASES} - known)
        assert not orphans, f"fixtures for templates that no longer exist: {orphans}"

    def test_template_groups_match_declared_provides(self):
        """A renamed capture group must be an error, not a silent None."""
        for template in TEMPLATES:
            groups = set(template.pattern.groupindex)
            missing = template.provides - groups
            assert not missing, f"{template.id} declares {sorted(missing)} but has no such group"

    def test_every_template_can_produce_a_direction(self):
        for template in TEMPLATES:
            has_group = "dir" in template.pattern.groupindex
            assert template.direction is not None or has_group, (
                f"{template.id} can never determine a direction"
            )

    def test_template_ids_are_unique(self):
        ids = [t.id for t in TEMPLATES]
        assert len(ids) == len(set(ids))

    def test_template_ids_are_versioned(self):
        """An id ends in a version so a reading can be traced to the pattern.

        `sms_messages.template_id` is how a template that turns out to misread
        something gets found in the data rather than reasoned about.
        """
        for template in TEMPLATES:
            assert re.search(r"\.v\d+$", template.id), f"{template.id} carries no version suffix"

    def test_the_parser_touches_no_database(self):
        """Purity, where import-linter cannot reach.

        `include_external_packages = False` means the contract cannot forbid
        sqlalchemy, so the source scan covers it. The property being protected
        is that a template runs against a fixture in microseconds with no
        database anywhere near it.
        """
        parser_dir = Path(__file__).resolve().parents[2] / "app" / "modules" / "sms" / "parser"
        forbidden = ("sqlalchemy", "AsyncSession", "get_session", "app.core.database")
        for path in parser_dir.rglob("*.py"):
            source = path.read_text()
            for token in forbidden:
                assert token not in source, f"{path.name} references {token}"


class TestSenderFiltering:
    """The device-side filter. This is the privacy boundary, not an optimisation."""

    def test_a_phone_number_is_not_a_commercial_sender(self):
        assert not is_capturable("+919876543210", "Rs 450 debited from A/c XX1234")

    @pytest.mark.parametrize(
        ("sender", "issuer"),
        [
            ("VM-HDFCBK", "HDFC"),
            ("AD-HDFCBK-S", "HDFC"),
            ("HDFCBK", "HDFC"),
            ("JD-ICICIB", "ICICI"),
            ("VM-ZZZBNK", None),
        ],
    )
    def test_sender_headers_resolve_to_issuers(self, sender: str, issuer: str | None):
        assert issuer_for(sender) == issuer

    def test_a_disqualifying_phrase_beats_any_keyword(self):
        """Conservative in the direction that costs a row rather than invents one."""
        assert not looks_transactional("Rs 5000 debited. OTP 123456. Do not share.")

    def test_a_plain_transaction_passes(self):
        assert looks_transactional("Rs 450.00 debited from A/c XX1234 at SWIGGY")


class TestNormalisation:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("450.00", Decimal("450.00")),
            ("1,250.00", Decimal("1250.00")),
            ("1,23,456.78", Decimal("123456.78")),  # Indian lakh grouping
            ("123,456.78", Decimal("123456.78")),  # Western grouping, same number
            ("Rs.450", Decimal("450")),
            ("INR 450", Decimal("450")),
            ("450.0", Decimal("450.0")),
        ],
    )
    def test_amounts(self, raw: str, expected: Decimal):
        value, _ = parse_amount(raw)
        assert value == expected

    def test_an_ambiguous_comma_is_flagged_not_guessed(self):
        value, ambiguous = parse_amount("450,00")
        assert ambiguous, "450,00 may be a decimal comma; the caller must lose confidence"
        assert value == Decimal("45000")

    def test_a_zero_or_negative_amount_is_not_an_amount(self):
        assert parse_amount("0.00")[0] is None
        assert parse_amount("abc")[0] is None

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("15/08/26", "2026-08-15"),
            ("15-08-26", "2026-08-15"),
            ("15-08-2026", "2026-08-15"),
            ("15-Aug-26", "2026-08-15"),
            ("15Aug26", "2026-08-15"),
            ("2026-08-15", "2026-08-15"),
            ("15 Aug 2026", "2026-08-15"),
        ],
    )
    def test_dates(self, raw: str, expected: str):
        parsed = parse_date(raw, received_on=RECEIVED_AT.date())
        assert parsed is not None
        assert parsed.isoformat() == expected

    def test_numeric_dates_are_day_first(self):
        """Matching DATE_FORMATS in the CSV importer.

        The two paths disagreeing about the same payment's date would break
        content-hash convergence, which is the whole reason SMS ingestion does
        not double-book a statement import.
        """
        parsed = parse_date("05/08/26", received_on=RECEIVED_AT.date())
        assert parsed is not None
        assert (parsed.day, parsed.month) == (5, 8)

    def test_a_date_after_the_message_arrived_is_rejected(self):
        """A future date means the pattern read the wrong field."""
        assert parse_date("15/12/26", received_on=RECEIVED_AT.date()) is None

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("XX1234", "1234"),
            ("xx1234", "1234"),
            ("**1234", "1234"),
            ("...1234", "1234"),
            ("X1234", "1234"),
            ("1234", "1234"),
        ],
    )
    def test_account_tails_normalise_to_digits(self, raw: str, expected: str):
        """One account written six ways is one identifier, not six."""
        assert account_tail(raw) == expected

    @pytest.mark.parametrize(
        ("vpa", "merchant"),
        [
            ("swiggy@ybl", "swiggy"),
            ("john.doe@oksbi", "john doe"),
            ("q73829183@okaxis", None),
            ("9876543210@paytm", None),
            ("paytmqr2810@paytm", None),
        ],
    )
    def test_opaque_handles_name_nobody(self, vpa: str, merchant: str | None):
        """None, not junk.

        A dynamic-QR handle stored as a merchant matches nothing, pollutes the
        trigram index on merchant_normalized, and gives the categoriser a label
        it can only learn noise from.
        """
        assert split_vpa(vpa)[0] == merchant

    def test_redaction_removes_the_balance_and_keeps_the_transaction(self):
        body = "Rs.450.00 debited from A/c XX1234 at SWIGGY. Avl Bal INR 12345.67. Call 9876543210"
        out = redact(body)
        assert "450.00" in out and "SWIGGY" in out, "the review queue must still show its work"
        assert "12345.67" not in out, "a running balance is more revealing than a purchase"
        assert "9876543210" not in out


class TestRailSemantics:
    def test_an_atm_withdrawal_is_not_penalised_for_having_no_merchant(self):
        """Cash has no payee. Absence is completeness here, not doubt."""
        parsed = parse(
            "Rs.2000 withdrawn from HDFC Bank Card x5678 at HDFC ATM on 15-08-26",
            sender="VM-HDFCBK",
            received_at=RECEIVED_AT,
        )
        assert parsed is not None
        assert parsed.rail is Rail.ATM
        assert parsed.merchant is None
        assert parsed.confidence >= Decimal("0.90")

    def test_a_reversal_is_flagged_however_confident_it_is(self):
        """It parses as a credit, which is arithmetically right and semantically
        wrong -- a refund reduces an expense rather than being income."""
        parsed = parse(
            "Rs.450.00 reversed to your A/c XX1234 on 16-08-26 for failed UPI txn",
            sender="VM-HDFCBK",
            received_at=RECEIVED_AT,
        )
        assert parsed is not None
        assert parsed.reversal
        assert any("refund" in c or "reversal" in c for c in parsed.caveats)

    def test_a_missing_date_is_declared_rather_than_hidden(self):
        parsed = parse(
            "Rs.450.00 debited from A/c XX1234 and credited to swiggy@ybl "
            "(UPI Ref no 522398456712) -Bank of Baroda",
            sender="VM-BOBSMS",
            received_at=RECEIVED_AT,
        )
        assert parsed is not None
        assert parsed.date_inferred
        assert parsed.occurred_on == RECEIVED_AT.date()
        assert any("date" in c.lower() for c in parsed.caveats)


class TestPrecedence:
    def test_an_issuer_template_beats_a_generic_one(self):
        """Both match this body; the issuer's own reading must win."""
        parsed = parse(
            "Sent Rs.450.00 From HDFC Bank A/C x1234 To SWIGGY On 15/08/26 Ref 522398456712",
            sender="VM-HDFCBK",
            received_at=RECEIVED_AT,
        )
        assert parsed is not None
        assert parsed.template_id == "hdfc.upi.debit.v1"

    def test_a_known_sender_outscores_an_unknown_one_on_the_same_body(self):
        body = "INR 560.00 debited from A/c no. XX2211 on 15-08-26 at DUNZO."
        known = parse(body, sender="AD-YESBNK", received_at=RECEIVED_AT)
        unknown = parse(body, sender="VM-ZZZBNK", received_at=RECEIVED_AT)
        assert known is not None and unknown is not None
        assert known.confidence > unknown.confidence
        assert Precedence.RAIL_KNOWN_SENDER != Precedence.RAIL_UNKNOWN_SENDER


class TestTheDeviceFilterMatchesTheServer:
    """The Kotlin allowlist is generated from the Python one. This is what makes
    that generation trustworthy.

    The device filter is the privacy boundary: whatever it rejects never reaches
    disk on the handset and never reaches the server. Two hand-maintained copies
    of that rule in two languages would drift, and the drift would be silent --
    the app would quietly start (or stop) capturing messages that the server-side
    documentation says it does not.
    """

    def _generated(self) -> str:
        import subprocess
        import sys

        root = Path(__file__).resolve().parents[2]
        result = subprocess.run(  # noqa: S603
            [sys.executable, str(root / "scripts" / "generate_sender_filter.py")],
            capture_output=True,
            text=True,
            check=True,
            cwd=root,
        )
        return result.stdout

    def test_the_committed_kotlin_is_current(self):
        kotlin = (
            Path(__file__).resolve().parents[3]
            / "mobile"
            / "android-overlay"
            / "app"
            / "src"
            / "smsreader"
            / "java"
            / "app"
            / "frugal"
            / "sms"
            / "SenderFilter.kt"
        )
        if not kotlin.exists():
            pytest.skip("the Android overlay is not checked out")

        assert kotlin.read_text() == self._generated(), (
            "SenderFilter.kt is stale. Regenerate it:\n"
            "  cd backend && python scripts/generate_sender_filter.py > "
            "../mobile/android-overlay/app/src/smsreader/java/app/frugal/sms/SenderFilter.kt"
        )

    def test_every_known_sender_appears_in_the_generated_filter(self):
        from app.modules.sms.parser.senders import KNOWN_SENDERS

        generated = self._generated()
        for header in KNOWN_SENDERS:
            assert f'"{header}"' in generated


class TestAPastedMessageWithNoSender:
    """Someone copying an alert out of their inbox copies the text, not the
    header above it. The paste box has to work without one."""

    def test_it_still_reads_the_message(self):
        parsed = parse(
            "Sent Rs.450.00 From HDFC Bank A/C x1234 To SWIGGY On 15/08/26 Ref 522398456712",
            sender="",
            received_at=RECEIVED_AT,
        )
        assert parsed is not None
        assert parsed.amount == Decimal("450.00")
        assert parsed.account_tail == "1234"

    def test_it_never_reaches_the_ledger_unattended(self):
        """With no header to recognise, we cannot verify a bank sent this. The
        reading is still useful; it just needs a human."""
        parsed = parse(
            "Sent Rs.450.00 From HDFC Bank A/C x1234 To SWIGGY On 15/08/26",
            sender="",
            received_at=RECEIVED_AT,
        )
        assert parsed is not None
        assert parsed.confidence < Decimal("0.75")

    def test_a_personal_message_is_still_refused(self):
        assert (
            parse("hey can you send me 500 for dinner", sender="", received_at=RECEIVED_AT) is None
        )

    def test_a_phone_number_sender_is_refused_outright(self):
        """Nothing on the device path produces one, so its presence means the
        caller is passing something this was not built to read."""
        assert (
            parse(
                "Sent Rs.450.00 From HDFC Bank A/C x1234 To SWIGGY On 15/08/26",
                sender="+919876543210",
                received_at=RECEIVED_AT,
            )
            is None
        )
