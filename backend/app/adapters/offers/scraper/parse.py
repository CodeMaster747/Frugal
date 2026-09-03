"""Turning a fetched response into offers, or into nothing (ADR-012).

**JSON only, on purpose.** The permitted surface is public JSON price
endpoints, which need no HTML parser at all -- so shipping without one defers
the dependency argument entirely. The `Extractor` shape below is what makes
HTML one class later rather than a rewrite; if it is ever added, prefer
`selectolax` over `lxml`: lxml vendors libxml2, and this repo's `security` job
runs Trivy at `HIGH,CRITICAL, exit-code: 1`, so a libxml2 CVE would break CI on
somebody else's schedule.

**Everything is validated before it is emitted.** A silently broken extractor
returning garbage prices is worse than one returning nothing, because the
garbage lands in a graph users trust. The two failures that actually happen are
parsing the EMI instalment and parsing the star rating; both are caught by the
plausibility gate below and neither by a schema.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.adapters.ports import RetailOffer

#: Nothing this system compares costs more than this. A "price" above it is a
#: misparse -- a phone number, an order id, a total in paise.
MAX_PLAUSIBLE_PRICE = Decimal("10000000")

#: How far from its peers a price may sit before it is assumed misread. Ten
#: times catches the EMI instalment and the rating; a tighter bound would start
#: rejecting genuine clearance prices.
PEER_RATIO = Decimal(10)

#: An extractor takes the decoded response **and the query it was made for**.
#:
#: The query is not decoration. `prices.openfoodfacts.org` accepts unknown
#: filter parameters and silently ignores them: `?product_name__like=milk`
#: returned all 301,369 rows, so a search for milk produced French pasta with
#: entirely plausible prices. Nothing downstream could catch that -- the
#: plausibility gate checks that a number is sane, not that a row is relevant.
#:
#: Every extractor is therefore responsible for proving its results answer the
#: question, and `relevant()` below is the shared way to do it.
Extractor = Callable[[Any, str], Sequence["RawOffer"]]


class RawOffer(dict[str, Any]):
    """An extractor's output before validation. Deliberately loose: an
    extractor's job is to find fields, and judging them is this module's."""


def to_decimal(value: object) -> Decimal | None:
    """A price from whatever the endpoint called it.

    Strings with currency symbols and thousands separators are the norm, and a
    float would be an ADR-003 violation at the boundary where it is easiest to
    miss.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    text = str(value).strip().replace(",", "").replace("₹", "").replace("Rs.", "")
    text = "".join(c for c in text if c.isdigit() or c == ".")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def median_of(prices: list[Decimal]) -> Decimal | None:
    if not prices:
        return None
    ordered = sorted(prices)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def plausible(offer: RetailOffer, *, peers: list[Decimal]) -> bool:
    """Whether an offer is worth emitting at all."""
    if offer.price <= 0 or offer.price > MAX_PLAUSIBLE_PRICE:
        return False
    if not offer.title.strip():
        return False

    median = median_of(peers)
    if median is None or median <= 0:
        return True

    # The two misparses that actually happen: the EMI instalment (an order of
    # magnitude low) and the star rating (three orders low). Neither is caught
    # by a schema, and both look like perfectly good numbers.
    return median / PEER_RATIO <= offer.price <= median * PEER_RATIO


def build(raw: Sequence[RawOffer], *, provider: str, host: str) -> list[RetailOffer]:
    """Validate and stamp.

    `as_of` is set at the moment the response arrived, not when the row is later
    read: a scraped result is served from cache by construction, and the panel's
    "prices as last seen on X" reads this field. Getting it wrong would imply a
    currency the data does not have.
    """
    now = datetime.now(UTC)
    candidates: list[RetailOffer] = []

    for item in raw:
        price = to_decimal(item.get("price"))
        title = str(item.get("title") or "").strip()
        if price is None or not title:
            continue
        candidates.append(
            RetailOffer(
                title=title[:300],
                price=price,
                seller=str(item.get("seller") or host),
                link=str(item.get("link") or ""),
                as_of=now,
                provider=provider,
                currency=str(item.get("currency") or "INR"),
                delivery_note=(str(item["delivery_note"]) if item.get("delivery_note") else None),
                thumbnail_url=(str(item["thumbnail_url"]) if item.get("thumbnail_url") else None),
            )
        )

    peers = [offer.price for offer in candidates]
    kept = [offer for offer in candidates if plausible(offer, peers=peers)]
    return sorted(kept, key=lambda offer: offer.price)


def relevant(title: str, query: str) -> bool:
    """Whether a result plausibly answers the query.

    Every significant word of the query must appear in the title. Crude on
    purpose: the job is to catch a filter that was ignored entirely, not to
    rank. A host that returns its whole catalogue fails this on the first row.

    Short tokens are dropped -- "1l", "of", "g" match everything and would let
    an unfiltered page through.
    """
    haystack = title.lower()
    terms = [t for t in query.lower().split() if len(t) > 2]
    if not terms:
        return True
    return all(term in haystack for term in terms)


def open_prices(payload: Any, query: str) -> Sequence[RawOffer]:
    """Open Food Facts' Open Prices (ODbL) -> offers.

    The response is `{items: [...], total, page, size, pages}`. Each item
    carries `price`, `currency`, `date`, `product_code`, a nested `product` and
    `location`, and -- most usefully -- `location_osm_id` / `location_osm_type`,
    which is the same OpenStreetMap identity `stores.osm_id` holds.

    `product_name` is frequently null on the price row itself and present on the
    nested product, so both are tried before falling back to the barcode. A
    barcode is a poor title and a real one: it is what the contributor recorded,
    and inventing something friendlier would be inventing.

    Everything here is defensive. This is somebody else's JSON, it changes
    without notice, and `parse.build` validates whatever survives -- so the job
    of this function is to find fields, not to judge them.
    """
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []

    offers: list[RawOffer] = []
    for item in items:
        if not isinstance(item, dict):
            continue

        raw_product = item.get("product")
        product: dict[str, Any] = raw_product if isinstance(raw_product, dict) else {}
        raw_location = item.get("location")
        location: dict[str, Any] = raw_location if isinstance(raw_location, dict) else {}

        title = (
            item.get("product_name")
            or product.get("product_name")
            or product.get("name")
            or item.get("product_code")
        )
        seller = (
            location.get("osm_name")
            or location.get("osm_display_name")
            or location.get("osm_brand")
            or "Open Prices"
        )

        clean_title = str(title or "").strip()
        # The filter may have been ignored. See `Extractor`.
        if not relevant(clean_title, query):
            continue

        currency = item.get("currency")
        if not currency:
            # No fallback. Defaulting a foreign price to rupees would render a
            # 0.63 EUR row as Rs.0.63 -- a silent, confident misstatement, and
            # the worst possible failure for a product about comparing prices.
            continue

        offers.append(
            RawOffer(
                title=clean_title,
                price=item.get("price"),
                currency=currency,
                seller=str(seller),
                # No link: Open Prices records a price at a shop, and there is
                # no product page to send anyone to. An invented URL would be
                # worse than an empty one.
                link="",
                delivery_note=None,
                thumbnail_url=product.get("image_url"),
            )
        )
    return offers


#: Dotted name -> callable. A host rule names its extractor as data rather than
#: importing it, so `hosts.py` stays a table anyone can read.
EXTRACTORS: dict[str, Extractor] = {"open_prices": open_prices}


def resolve(name: str) -> Extractor | None:
    return EXTRACTORS.get(name)
