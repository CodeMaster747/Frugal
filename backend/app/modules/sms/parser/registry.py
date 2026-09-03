"""Which templates are worth trying for a given message, and in what order.

Sender-first, deliberately. Running every body pattern against every message is
O(templates) per message, and an XML backfill is fifty thousand messages -- the
one path where this module's cost is visible to a user waiting on a job.
"""

from __future__ import annotations

from collections import defaultdict

from app.modules.sms.parser.senders import issuer_for
from app.modules.sms.parser.templates import ALL_TEMPLATES, GENERIC_TEMPLATES, ISSUER_TEMPLATES
from app.modules.sms.parser.types import Precedence, Template

#: Every template the parser knows, issuer-specific first. The public name --
#: tests enumerate this, and `test_every_template_has_a_fixture` fails when an
#: entry here has no corresponding case.
TEMPLATES: tuple[Template, ...] = ALL_TEMPLATES


def _index_by_issuer() -> dict[str, tuple[Template, ...]]:
    grouped: dict[str, list[Template]] = defaultdict(list)
    for template in ISSUER_TEMPLATES:
        grouped[template.issuer].append(template)
    return {issuer: tuple(items) for issuer, items in grouped.items()}


_BY_ISSUER = _index_by_issuer()


def _assert_ids_are_unique() -> None:
    """A duplicate id would make a template untraceable in the data.

    Checked at import time: `sms_messages.template_id` is how a template that
    turns out to misread something gets found later, and two templates sharing
    an id makes that lookup ambiguous exactly when it matters.
    """
    seen: set[str] = set()
    for template in TEMPLATES:
        if template.id in seen:
            raise ValueError(f"duplicate template id: {template.id}")
        seen.add(template.id)


_assert_ids_are_unique()


def candidates(sender: str) -> tuple[tuple[Template, Precedence], ...]:
    """Templates to try for this sender, most specific first.

    The issuer's own templates come first and carry the highest confidence. The
    rail-generic fallbacks always follow, because a bank we have templated can
    still send a shape we have not seen -- and a generic read at reduced
    confidence is better than no read at all.

    **An empty sender tries every template**, at unknown-sender precedence. That
    is the pasted-message case: someone copying an alert out of their inbox
    copies the text, not the header above it, and the rail-generic patterns
    alone would miss most issuer wordings -- HDFC's "Sent Rs.450 From ... To ..."
    never says "debited", so nothing generic matches it. Trying the issuer
    patterns anyway reads the message correctly; capping the precedence means it
    still goes to a human, because we cannot confirm a bank sent it.
    """
    if not sender.strip():
        return tuple(
            (template, Precedence.RAIL_UNKNOWN_SENDER)
            for template in (*ISSUER_TEMPLATES, *GENERIC_TEMPLATES)
        )

    issuer = issuer_for(sender)
    ordered: list[tuple[Template, Precedence]] = []

    if issuer is not None:
        ordered.extend((template, Precedence.ISSUER) for template in _BY_ISSUER.get(issuer, ()))

    generic_precedence = (
        Precedence.RAIL_KNOWN_SENDER if issuer is not None else Precedence.RAIL_UNKNOWN_SENDER
    )
    ordered.extend((template, generic_precedence) for template in GENERIC_TEMPLATES)
    return tuple(ordered)
