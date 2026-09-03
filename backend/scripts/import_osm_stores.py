"""Seed the store directory from an OpenStreetMap extract.

**Offline, one-off, and deliberately not a runtime dependency.** Geocoding a
printed Indian retail address resolves poorly, and a live geocoder would be a
metered external call on the path of every receipt -- which ADR-008 spent a
milestone establishing is the thing not to build. This reads a file, once.

Input: an OSM extract in the `.osm`/`.xml` format, filtered to India. Produce
one with, for example:

    osmium tags-filter india-latest.osm.pbf \\
        n/shop w/shop n/amenity=marketplace -o shops.osm

Then:

    python -m scripts.import_osm_stores shops.osm

Idempotent: `stores.osm_id` is unique, so re-running updates rather than
duplicates. Safe to run against a populated database.

Parsed with `defusedxml`, which is a core dependency for exactly this reason --
an OSM extract is a large file from a third party, and `xml.etree` on untrusted
input is how a billion laughs gets in.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

#: A node is only a shop worth recording if it carries one of these. `shop=*`
#: is broad on purpose -- a price graph cares about anywhere a receipt might
#: come from -- but a name is required: an unnamed pin cannot be matched to a
#: receipt's merchant string and would sit on the map meaning nothing.
SHOP_KEYS = ("shop", "amenity")
WANTED_AMENITIES = frozenset(("marketplace", "pharmacy", "fuel"))

#: Guards against a bad extract quietly rewriting the whole directory.
MAX_ELEMENTS = 2_000_000


@dataclass(frozen=True, slots=True)
class OsmStore:
    osm_id: str
    name: str
    latitude: Decimal
    longitude: Decimal
    brand: str | None
    pincode: str | None
    city: str | None
    state: str | None


def parse(path: Path) -> list[OsmStore]:
    """Read shops out of an OSM XML extract.

    Streaming with `iterparse` and clearing as it goes: an India extract is
    gigabytes, and holding the tree would exceed the memory of the machine this
    project is deployed on several times over.
    """
    from defusedxml.ElementTree import iterparse

    stores: list[OsmStore] = []
    seen = 0

    context = iterparse(str(path), events=("end",))
    for _, element in context:
        # Only nodes are cleared, and only after they are read.
        #
        # `end` fires for every `<tag>` child *before* its parent `<node>`, so
        # clearing them here -- which the first version did, to keep memory
        # flat -- wiped their `k`/`v` attributes before the node ever looked at
        # them. Every shop then arrived nameless and the import silently
        # produced zero rows. Clearing the node releases its children anyway,
        # so the memory argument costs nothing.
        if element.tag != "node":
            continue

        seen += 1
        if seen > MAX_ELEMENTS:
            raise ValueError(
                f"extract has more than {MAX_ELEMENTS} nodes; filter it further with "
                "`osmium tags-filter` before importing"
            )

        tags = {t.get("k"): t.get("v") for t in element.findall("tag")}
        # Everything the node has to say, read *before* clearing it.
        # `Element.clear()` drops attributes as well as children, so reading
        # `lat` afterwards yields None -- which silently produced an import of
        # zero shops the first time this ran.
        node_id = element.get("id")
        raw_lat, raw_lon = element.get("lat"), element.get("lon")
        element.clear()

        name = tags.get("name")
        if not name:
            # An unnamed pin cannot be matched to a receipt's merchant string.
            continue

        kind = next((tags.get(k) for k in SHOP_KEYS if tags.get(k)), None)
        if kind is None:
            continue
        if "amenity" in tags and "shop" not in tags and kind not in WANTED_AMENITIES:
            continue

        try:
            latitude = Decimal(raw_lat or "")
            longitude = Decimal(raw_lon or "")
        except InvalidOperation:
            # A node without usable coordinates cannot go on a map. Skipped
            # silently and by the million: an extract has plenty, and logging
            # each one would bury the summary this script exists to print.
            continue

        stores.append(
            OsmStore(
                osm_id=f"node/{node_id}",
                name=name[:200],
                latitude=latitude,
                longitude=longitude,
                brand=tags.get("brand"),
                pincode=(tags.get("addr:postcode") or "")[:6] or None,
                city=tags.get("addr:city"),
                state=tags.get("addr:state"),
            )
        )

    return stores


async def load(stores: list[OsmStore]) -> tuple[int, int]:
    from sqlalchemy import select

    from app.core.database import worker_async_session
    from app.modules.finance.service import normalize_merchant
    from app.modules.pricegraph.models import Store, StoreSource

    created = updated = 0
    async with worker_async_session() as session:
        for batch_start in range(0, len(stores), 500):
            batch = stores[batch_start : batch_start + 500]
            existing = {
                row.osm_id: row
                for row in (
                    await session.execute(
                        select(Store).where(Store.osm_id.in_([s.osm_id for s in batch]))
                    )
                )
                .scalars()
                .all()
            }

            for store in batch:
                row = existing.get(store.osm_id)
                if row is None:
                    session.add(
                        Store(
                            name=store.name,
                            normalized_name=normalize_merchant(store.name) or store.name.lower(),
                            brand_slug=store.brand,
                            pincode=store.pincode,
                            city=store.city,
                            state=store.state,
                            latitude=store.latitude,
                            longitude=store.longitude,
                            source=StoreSource.OSM_IMPORT.value,
                            osm_id=store.osm_id,
                        )
                    )
                    created += 1
                else:
                    # Coordinates and names get corrected upstream; a re-import
                    # should pick that up. `confirmed_at` is deliberately not
                    # touched: a human confirmation is not OSM's to revoke.
                    row.name = store.name
                    row.normalized_name = normalize_merchant(store.name) or store.name.lower()
                    row.latitude = store.latitude
                    row.longitude = store.longitude
                    row.pincode = store.pincode or row.pincode
                    updated += 1

            await session.commit()

    return created, updated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("extract", type=Path, help="An .osm/.xml extract, filtered to shops")
    parser.add_argument("--dry-run", action="store_true", help="Parse and report without writing")
    args = parser.parse_args()

    if not args.extract.exists():
        print(f"no such file: {args.extract}", file=sys.stderr)
        return 1

    stores = parse(args.extract)
    print(f"parsed {len(stores)} named shops")

    if args.dry_run:
        for store in stores[:10]:
            print(f"  {store.name} ({store.latitude}, {store.longitude}) {store.pincode or ''}")
        return 0

    created, updated = asyncio.run(load(stores))
    print(f"created {created}, updated {updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
