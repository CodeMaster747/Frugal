"""The sender allowlist -- the filter that keeps personal messages on the phone.

This module is the privacy boundary, not the parser. On a device build the same
allowlist runs in Kotlin *before* a message is buffered, so a text from a friend
is never written to disk and never leaves the handset. Everything downstream --
the templates, the confidence rules, the review queue -- only ever sees traffic
that passed here.

That makes the list security-relevant in a way a regex table is not. Widening it
widens what leaves the device, so entries are bank sender headers and nothing
else: no wildcards, no "contains the word bank", no per-user additions.

`test_device_allowlist_matches_kotlin` asserts this file and its generated
Kotlin twin agree. If they drift, the device is filtering by different rules
than the server documents, which is the one failure here nobody would notice.
"""

from __future__ import annotations

import re

#: Indian commercial SMS arrives as `<2-char operator><hyphen><6-char header>`,
#: sometimes with a single-letter category suffix: "VM-HDFCBK", "AD-HDFCBK-S",
#: "JK-ICICIB-T". Only the middle part identifies the sender.
_SENDER_RE = re.compile(r"^(?:[A-Z]{2}-)?(?P<core>[A-Z0-9]{4,11})(?:-[A-Z])?$")


#: DLT header core -> issuer key. The issuer key is what template files use, so
#: adding a bank's second header is a one-line change here and nowhere else.
#:
#: Not exhaustive, and not claimed to be. India has hundreds of live headers and
#: banks add them without announcement; an unknown sender that still looks
#: transactional is parsed at RAIL_UNKNOWN_SENDER precedence and always
#: reviewed, which is the honest handling of a list that cannot be complete.
KNOWN_SENDERS: dict[str, str] = {
    # HDFC
    "HDFCBK": "HDFC",
    "HDFCBN": "HDFC",
    # ICICI
    "ICICIB": "ICICI",
    "ICICIT": "ICICI",
    "ICCICI": "ICICI",
    # State Bank of India
    "SBIINB": "SBI",
    "SBIUPI": "SBI",
    "SBIPSG": "SBI",
    "SBICRD": "SBI",
    "CBSSBI": "SBI",
    "ATMSBI": "SBI",
    # Axis
    "AXISBK": "AXIS",
    "AXISBL": "AXIS",
    # Kotak
    "KOTAKB": "KOTAK",
    # Punjab National Bank
    "PNBSMS": "PNB",
    "PUNBNK": "PNB",
    # Bank of Baroda
    "BOBSMS": "BOB",
    "BOBTXN": "BOB",
    "BOBIBK": "BOB",
    # Others, parsed by the rail-generic templates at known-sender precedence.
    "CANBNK": "CANARA",
    "UNIONB": "UNION",
    "IDFCFB": "IDFC",
    "YESBNK": "YES",
    "INDUSB": "INDUSIND",
    "FEDBNK": "FEDERAL",
    "RBLBNK": "RBL",
    "AUBANK": "AU",
    "IDBIBK": "IDBI",
    "INDBNK": "INDIAN",
    "CENTBK": "CENTRAL",
    "PYTMPB": "PAYTM",
    "AIRTLB": "AIRTEL",
}


#: A message from an allowlisted sender still has to look like money moving.
#: Banks use the same headers for marketing ("Pre-approved loan offer!"), and
#: forwarding those would be both useless and a broken promise about what we
#: read.
TRANSACTIONAL_KEYWORDS: frozenset[str] = frozenset(
    {
        "debited",
        "credited",
        "debit",
        "credit",
        "spent",
        "withdrawn",
        "received",
        "sent",
        "transferred",
        "paid",
        "purchase",
        "txn",
        "transaction",
    }
)

#: Phrases that mean a message is *about* a transaction without being one.
#: Checked before the keyword gate, because "Rs.5000 will be debited" in an OTP
#: message contains both an amount and "debited", and parsing it books a
#: payment the user never made.
DISQUALIFYING_PHRASES: tuple[str, ...] = (
    "otp",
    "one time password",
    "one-time password",
    "do not share",
    "never share",
    "will be debited",
    "will be credited",
    "request for",
    "is requesting",
    "collect request",
    "has requested",
    "pre-approved",
    "preapproved",
    "offer",
    "apply now",
    "click here",
    "eligible for",
)


def sender_core(sender: str) -> str | None:
    """The DLT header core, or None if this is not a commercial sender.

    A personal message comes from a phone number, which fails the pattern --
    so this doubles as the first half of the filter.
    """
    match = _SENDER_RE.match(sender.strip().upper())
    return match.group("core") if match else None


def issuer_for(sender: str) -> str | None:
    """Which bank a sender belongs to, if we know it."""
    core = sender_core(sender)
    return KNOWN_SENDERS.get(core) if core else None


def looks_transactional(body: str) -> bool:
    """Whether a body is worth parsing at all.

    Conservative in the direction that costs a user a transaction rather than
    invents one: a disqualifying phrase wins over any number of transactional
    keywords, because a false positive writes wrong money into the ledger and a
    false negative writes nothing.
    """
    lowered = body.lower()
    if any(phrase in lowered for phrase in DISQUALIFYING_PHRASES):
        return False
    words = set(re.findall(r"[a-z]+", lowered))
    return bool(words & TRANSACTIONAL_KEYWORDS)


def is_capturable(sender: str, body: str) -> bool:
    """The complete device-side filter, in one call.

    This is the function the Kotlin receiver mirrors. Everything it rejects
    never reaches disk on the handset, and never reaches this server.
    """
    return sender_core(sender) is not None and looks_transactional(body)
