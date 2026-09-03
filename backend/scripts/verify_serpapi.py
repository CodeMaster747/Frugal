#!/usr/bin/env python
"""Check the SerpAPI adapter against a real response. Costs one search.

The adapter's field mapping was written from SerpAPI's documentation, never
against live output. If a field name differs, `_parse` drops every result and
`find_offers` returns `[]` -- which looks exactly like "no results", while the
quota is spent anyway. That failure is silent and would be discovered by a user
seeing an empty panel, so it is worth one search to rule out.

Run once before switching a deployment to `serpapi`:

    cd backend
    SERPAPI_API_KEY=... python scripts/verify_serpapi.py "macbook air m3"

Exits non-zero if the response cannot be parsed into offers.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.adapters.offers.serpapi import _parse

ENDPOINT = "https://serpapi.com/search.json"


def main() -> int:
    key = os.environ.get("SERPAPI_API_KEY")
    if not key:
        print("SERPAPI_API_KEY is not set.", file=sys.stderr)
        return 2

    # Checked before spending a request. A key of the wrong shape is a copied
    # account id or a placeholder, and saying so beats a bare 401 -- which is
    # what SerpAPI returns for every kind of rejection and explains none of them.
    if not re.fullmatch(r"[0-9a-f]{64}", key):
        print(
            f"That does not look like a SerpAPI key: {len(key)} characters, "
            "expected 64 lowercase hex.",
            file=sys.stderr,
        )
        print("Find the real one at https://serpapi.com/manage-api-key", file=sys.stderr)
        print("(Dashboard -> Your Private API Key.)", file=sys.stderr)
        return 2

    query = sys.argv[1] if len(sys.argv) > 1 else "macbook air m3"
    print(f"One live search for {query!r}. This spends one of your monthly allowance.")
    print("Timing it too -- the production adapter needs a timeout that fits.\n")

    params = {
        "engine": "google_shopping",
        "q": query,
        "api_key": key,
        "gl": "in",
        "hl": "en",
        "location": "India",
        "num": "10",
    }
    url = f"{ENDPOINT}?{urllib.parse.urlencode(params)}"

    # Deliberately generous. The point of this run is to find out how long
    # google_shopping actually takes, and a short timeout here would only
    # reproduce the failure it exists to measure.
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        # SerpAPI puts the actual reason in the body; the status alone says only
        # that something was refused.
        detail = exc.read().decode("utf-8", "replace")[:400]
        print(f"SerpAPI refused the request: HTTP {exc.code}", file=sys.stderr)
        if detail:
            print(f"  {detail}", file=sys.stderr)
        if exc.code == 401:
            print(
                "\n401 means the key was rejected. Check it at "
                "https://serpapi.com/manage-api-key,\n"
                "and that the account's email is confirmed -- an unconfirmed "
                "account has a key\nthat looks valid and is refused.",
                file=sys.stderr,
            )
        return 1
    except Exception as exc:
        print(f"The request failed: {exc}", file=sys.stderr)
        return 1

    elapsed = time.monotonic() - started
    print(f"responded in {elapsed:.1f}s")
    from app.adapters.offers.serpapi import _TIMEOUT

    if elapsed > _TIMEOUT:
        print(
            f"  WARNING: the adapter's timeout is {_TIMEOUT:.0f}s, so a request like "
            f"this would be abandoned\n           after spending the quota. Raise "
            f"_TIMEOUT in app/adapters/offers/serpapi.py.",
            file=sys.stderr,
        )
    else:
        print(f"  within the adapter's {_TIMEOUT:.0f}s timeout")
    print()

    if "error" in payload:
        print(f"SerpAPI returned an error: {payload['error']}", file=sys.stderr)
        return 1

    raw = payload.get("shopping_results")
    if not isinstance(raw, list) or not raw:
        print("No `shopping_results` array in the response.", file=sys.stderr)
        print("Top-level keys were:", sorted(payload), file=sys.stderr)
        print("\nThe adapter reads `shopping_results`; if SerpAPI has renamed it,", file=sys.stderr)
        print("update `_parse` in app/adapters/offers/serpapi.py.", file=sys.stderr)
        return 1

    print(f"shopping_results: {len(raw)} entries")
    print(f"fields on the first entry: {sorted(raw[0])}\n")

    from datetime import UTC, datetime

    offers = _parse(payload, now=datetime.now(UTC), limit=10)

    if not offers:
        print("PARSED NOTHING.", file=sys.stderr)
        print(
            "Every entry was dropped, which means no readable `extracted_price`.", file=sys.stderr
        )
        print(f"First entry, verbatim:\n{json.dumps(raw[0], indent=2)[:1200]}", file=sys.stderr)
        return 1

    print(f"parsed {len(offers)}/{len(raw)} entries\n")
    for offer in offers[:5]:
        print(f"  {offer.price:>12,.2f}  {offer.seller[:24]:<24}  {offer.title[:44]}")

    # Which optional fields actually arrived. A field that is absent everywhere
    # means the reliability rubric is scoring with one fewer signal than
    # expected -- not broken, but worth knowing before trusting the bands.
    print("\noptional fields present across parsed offers:")
    for name in ("seller_rating", "rating_count", "delivery_note", "link", "thumbnail_url"):
        have = sum(1 for o in offers if getattr(o, name))
        flag = "ok " if have else "NONE"
        print(f"  {flag} {name:<15} {have}/{len(offers)}")

    if not any(o.seller_rating for o in offers):
        print("\nNote: no seller ratings came back. The reliability score still works --")
        print("missing signals are excluded and their weight redistributed -- but every")
        print("offer will sit at low confidence.")

    prices = [o.price for o in offers]
    if prices != sorted(prices):
        print(
            "\nWARNING: offers are not price-ordered. `_parse` should sort them.", file=sys.stderr
        )
        return 1

    print(f"\nOK. Cheapest {min(prices):,.2f}, dearest {max(prices):,.2f}, ascending.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
