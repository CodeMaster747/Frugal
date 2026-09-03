"""One module per issuer, plus the rail-generic fallbacks.

Each module exports `TEMPLATES: tuple[Template, ...]`. `registry.py` is the only
importer; nothing else should reach in here.

Adding support for a bank message is three steps, and the third is enforced:

1. Add a `Template` to the issuer's module (or create one, and list it below).
2. Add the real message -- digits changed -- to `tests/fixtures/sms/<issuer>.py`
   with what it should parse to.
3. Run `pytest tests/unit/test_sms_parser.py`.

`test_every_template_has_a_fixture` enumerates the registry and fails when a
template has no case. That is what stops this directory rotting into a pile of
patterns nobody can safely change.
"""

from __future__ import annotations

from app.modules.sms.parser.templates import (
    axis,
    bob,
    card_generic,
    hdfc,
    icici,
    kotak,
    pnb,
    sbi,
    upi_generic,
)
from app.modules.sms.parser.types import Template

#: Issuer modules first, in no particular order -- precedence is decided by
#: `registry.candidates()`, not by position here.
ISSUER_MODULES = (hdfc, icici, sbi, axis, kotak, pnb, bob)

#: Rail-generic modules, tried only when no issuer template matched.
GENERIC_MODULES = (upi_generic, card_generic)

ISSUER_TEMPLATES: tuple[Template, ...] = tuple(
    template for module in ISSUER_MODULES for template in module.TEMPLATES
)

GENERIC_TEMPLATES: tuple[Template, ...] = tuple(
    template for module in GENERIC_MODULES for template in module.TEMPLATES
)

ALL_TEMPLATES: tuple[Template, ...] = ISSUER_TEMPLATES + GENERIC_TEMPLATES
