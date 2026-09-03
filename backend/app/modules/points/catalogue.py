"""The rewards catalogue.

Nothing here is fulfilled. `POST /rewards/{id}/redeem` returns 501 and says so
plainly, and `is_available` is false on every row.

The catalogue exists anyway, and that is a deliberate call rather than a stub:
a user earning points needs to see what they are for. A number that goes up and
buys nothing, with no indication of what it might ever buy, is not a reward --
it is a counter. Publishing the intended costs now also means the economy is
argued about while it is cheap to change.
"""

from __future__ import annotations

from typing import Any

REWARDS: tuple[dict[str, Any], ...] = (
    {
        "slug": "ad-free-month",
        "title": "A month without the support banner",
        "description": (
            "Hides the funding banner for thirty days. Costs the project nothing, "
            "which is why it is first."
        ),
        "cost_points": 200,
    },
    {
        "slug": "extra-price-searches",
        "title": "Ten extra live price searches",
        "description": (
            "Live comparison runs on a metered free tier with a hard monthly cap "
            "(ADR-008). This spends some of that allowance on you."
        ),
        "cost_points": 500,
    },
    {
        "slug": "priority-receipt-processing",
        "title": "Priority receipt processing",
        "description": (
            "Your next twenty receipts jump the OCR queue. On a single-worker "
            "deployment that is a real, if modest, difference."
        ),
        "cost_points": 750,
    },
    {
        "slug": "contributor-badge",
        "title": "Contributor badge",
        "description": "Shown beside your reports, so other users know who is reliable.",
        "cost_points": 1000,
    },
)
