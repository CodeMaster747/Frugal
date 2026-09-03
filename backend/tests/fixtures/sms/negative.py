"""Messages that must NOT parse.

Worth as much as the positive corpus, and arguably more. A missed transaction
costs the user a row they can add by hand; an invented one puts money in the
ledger that was never spent, and every engine downstream -- budgets, health
score, forecast, advisor -- reads it as fact.

Nearly all of these contain an amount, most contain a merchant, and several
contain the word "debited". That is the point: the disqualifying-phrase gate,
not the absence of transaction-shaped text, is what stops them.
"""

from __future__ import annotations

#: (sender, body, why_it_must_not_parse)
CASES: tuple[tuple[str, str, str], ...] = (
    (
        "VM-HDFCBK",
        "OTP is 483920 for txn of Rs 5000.00 at AMAZON. Do not share with anyone.",
        "An OTP for a purchase not yet made. Contains amount, merchant and 'txn'.",
    ),
    (
        "JD-ICICIB",
        "Rs 2500.00 will be debited from A/c XX1234 on 20-08-26 towards your SIP.",
        "Future tense. The money has not moved; booking it now double-counts.",
    ),
    (
        "AX-SBIINB",
        "You have a collect request of Rs 450.00 from swiggy@ybl. Approve in the app.",
        "A request for payment, not a payment.",
    ),
    (
        "AD-HDFCBK",
        "Pre-approved personal loan of Rs 500000 available! Apply now: bit.ly/xyz",
        "Marketing from a real bank header. The allowlist alone would let it through.",
    ),
    (
        "VK-AXISBK",
        "Your A/c XX1234 has a balance of INR 12345.67 as on 15-08-26.",
        "A balance enquiry. No money moved.",
    ),
    (
        "+919876543210",
        "hey I paid the 450 for dinner yesterday, you owe me. debited from my account lol",
        "A personal message. A phone number is not a DLT header, so the sender gate stops it.",
    ),
    (
        "BP-KOTAKB",
        "Dear Customer, your card ending 5678 is eligible for a limit upgrade to Rs 200000.",
        "An offer that names a card and an amount.",
    ),
    (
        "VM-HDFCBK",
        "Use code SAVE20 to get Rs 200 off on your next order above Rs 999. Click here.",
        "A promotion carrying two amounts.",
    ),
)
