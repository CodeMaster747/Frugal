"""Fill the price graph with invented shops and prices, for local development.

**The problem this solves.** Every engine downstream of the map -- pins, store
sheets, "cheaper elsewhere" -- is meaningless on an empty graph, and the graph
starts empty. A developer running the stack for the first time gets a map with
nothing on it, which is indistinguishable from a map that is broken.

**Invented, and labelled as such.** Every shop written here carries an
`osm_id` of the form ``demo:<slug>``, so a query can always tell seeded rows
from real contributions, and a later import cannot silently collide with one.
Nothing here claims to be a real price at a real shop.

**Chennai, and not by accident.** The bounding box below is the one
``scripts/build-basemap.sh`` extracts by default, so every pin lands somewhere
the basemap actually has streets under it. It is also where the landing map
opens (``FALLBACK_CENTRE`` in the frontend), and the map endpoint refuses a
viewport wider than two degrees -- so seeding a second city would produce pins
nobody sees without panning.

**Idempotent.** Shops upsert on their synthetic ``osm_id``; observations go
through ``PricegraphService.record_observation``, which pre-checks the
one-per-contributor-per-day constraint and returns ``None`` rather than
raising. Re-running writes nothing new.

**What survives what.** The stores, items and observations here are
deployment-scoped -- no ``user_id`` -- so they outlive ``make reset-dev-data``,
which only deletes users. The community reports do not: they hang off a demo
user by a cascading foreign key and go with it. Re-run this script to restore
them.

Refuses to run against anything but a local database, for the same reason
``reset_dev_data`` does: a script that writes invented data into the price
graph must not be one SSH session away from doing it in production.

    python -m scripts.seed_demo_pricegraph [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

#: Same guard, same list, same reason as `scripts/reset_dev_data.py`.
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "postgres", "db", "host.docker.internal"})

#: Fixed, so two runs on two machines produce the same map. A demo whose
#: numbers move every run is a demo nobody can describe to anybody else.
SEED = 20260907

#: Distinct pretend contributors per (item, shop). Must exceed
#: `min_contributors_for_display` (default 2) or the k-anonymity floor hides
#: every price and the pins come back bare -- which is the bug being fixed, so
#: getting this wrong looks exactly like not having run the script.
CONTRIBUTORS_PER_PRICE = 3

#: Observations must land inside `FRESH_DAYS` (30) to count as current. Kept
#: well inside it so the seed does not go stale a day later.
MAX_AGE_DAYS = 12

#: Namespace for the pretend contributors behind each seeded price.
#:
#: `uuid5`, not `uuid4`, and that is the whole of this script's idempotency for
#: observations. `record_observation` refuses a duplicate by looking up
#: HMAC(pepper, user|store|day) -- so a random user id per run produces a fresh
#: hash every time, the pre-check never matches, and re-running silently doubles
#: every price instead of doing nothing. Derived ids make the second run a
#: no-op, which is what "idempotent" has to mean here.
CONTRIBUTOR_NS = uuid.UUID("5f7d1c94-3a2e-4b60-9c51-2d8e0f6a4b73")

#: The demo user that owns the seeded community reports.
#:
#: Deliberately not `@example.com`: the E2E teardown deletes `%@example.com`
#: users, so a demo account on that domain would vanish after every test run.
#: `.invalid` is reserved by RFC 2606 and can never be a real address.
DEMO_EMAIL = "demo-contributor@frugal.invalid"
DEMO_NAME = "Demo contributor"


@dataclass(frozen=True, slots=True)
class DemoShop:
    slug: str
    name: str
    latitude: str
    longitude: str
    pincode: str
    #: Confirmed shops carry no "added by a user, unconfirmed" banner in the
    #: store sheet. A mix is the honest picture of a young graph.
    confirmed: bool


#: Inside 80.10,12.83 .. 80.35,13.25 -- the basemap archive's own bounds.
#: Coordinates are placed by neighbourhood, not surveyed; these are invented
#: shops, and the point is that they sit on plausible streets.
SHOPS: tuple[DemoShop, ...] = (
    DemoShop("adyar-nilgiris", "Nilgiris Adyar", "13.006700", "80.257000", "600020", True),
    DemoShop("adyar-grand-sweets", "Grand Sweets Adyar", "12.998200", "80.253100", "600020", True),
    DemoShop("besant-amma-nana", "Amma Nana Stores", "13.000400", "80.266500", "600090", True),
    DemoShop(
        "tnagar-ranganathan", "Ranganathan Provisions", "13.041800", "80.234100", "600017", True
    ),
    DemoShop(
        "tnagar-pothys-daily", "Pothys Daily Needs", "13.038900", "80.229700", "600017", False
    ),
    DemoShop(
        "mylapore-karpagambal", "Karpagambal Stores", "13.033600", "80.269400", "600004", True
    ),
    DemoShop("mylapore-kapali-mart", "Kapali Mart", "13.029100", "80.264800", "600004", False),
    DemoShop(
        "velachery-reliance", "Reliance Fresh Velachery", "12.979100", "80.221000", "600042", True
    ),
    DemoShop(
        "velachery-more", "More Supermarket Velachery", "12.975400", "80.217600", "600042", True
    ),
    DemoShop("guindy-dmart", "DMart Guindy", "13.007800", "80.212300", "600032", True),
    DemoShop("saidapet-kumaran", "Kumaran Stores", "13.021700", "80.223900", "600015", False),
    DemoShop("anna-nagar-vivek", "Vivek Provision Mart", "13.085200", "80.210400", "600040", True),
    DemoShop(
        "anna-nagar-sri-krishna",
        "Sri Krishna Sweets Anna Nagar",
        "13.090600",
        "80.216100",
        "600040",
        True,
    ),
    DemoShop(
        "kilpauk-nuts-n-spices", "Nuts n Spices Kilpauk", "13.078400", "80.242700", "600010", True
    ),
    DemoShop("egmore-hot-breads", "Hot Breads Egmore", "13.073100", "80.259300", "600008", False),
    DemoShop("porur-heritage", "Heritage Fresh Porur", "13.037200", "80.157800", "600116", True),
    DemoShop(
        "perungudi-daily-bazaar",
        "Daily Bazaar Perungudi",
        "12.962400",
        "80.243100",
        "600096",
        False,
    ),
    DemoShop(
        "thiruvanmiyur-selvam", "Selvam Super Market", "12.982900", "80.259600", "600041", True
    ),
)


@dataclass(frozen=True, slots=True)
class DemoItem:
    #: Written the way a receipt would print it, so `normalize_item` does the
    #: same work here that it does on a real upload -- including the pack size.
    line: str
    low: str
    high: str


#: Everyday Indian grocery lines with plausible late-2026 rupee ranges. The
#: spread is what makes the graph interesting: a price identical everywhere
#: gives the "cheaper elsewhere" engine nothing to find.
ITEMS: tuple[DemoItem, ...] = (
    DemoItem("AMUL TAAZA MILK 500ML", "27", "34"),
    DemoItem("AASHIRVAAD ATTA 5KG", "265", "319"),
    DemoItem("TATA SALT 1KG", "24", "31"),
    DemoItem("FORTUNE SUNFLOWER OIL 1L", "138", "172"),
    DemoItem("TOOR DAL 1KG", "142", "186"),
    DemoItem("SURF EXCEL EASY WASH 1KG", "118", "145"),
    DemoItem("COLGATE STRONG TEETH 200G", "96", "119"),
    DemoItem("BRITANNIA GOOD DAY 200G", "38", "50"),
    DemoItem("NESCAFE CLASSIC 50G", "165", "205"),
    DemoItem("SUNFEAST YIPPEE NOODLES 280G", "52", "68"),
)


@dataclass(frozen=True, slots=True)
class DemoReport:
    shop_slug: str
    kind: str
    item_text: str
    price: str | None
    note: str | None


#: Enough to make "What people say" a populated section rather than an empty
#: one. A `good_price` report must carry a price -- there is a check
#: constraint -- while `unique_item` is the "they stock this at all" case.
REPORTS: tuple[DemoReport, ...] = (
    DemoReport(
        "mylapore-karpagambal",
        "unique_item",
        "Filter coffee powder, ground to order",
        None,
        "They grind it while you wait. Ask for the 70:30 blend.",
    ),
    DemoReport(
        "guindy-dmart", "good_price", "Aashirvaad Atta 5kg", "265", "Cheapest I have found nearby."
    ),
    DemoReport("tnagar-ranganathan", "good_price", "Toor dal 1kg", "142", None),
    DemoReport(
        "kilpauk-nuts-n-spices",
        "unique_item",
        "Kashmiri saffron, 1g tin",
        None,
        "Kept behind the counter, so you have to ask.",
    ),
    DemoReport(
        "velachery-more", "good_price", "Fortune sunflower oil 1L", "138", "Only on the 1L pack."
    ),
    DemoReport(
        "adyar-grand-sweets",
        "unique_item",
        "Adhirasam, made same morning",
        None,
        "Sold out by about eleven on weekends.",
    ),
)


async def _seed(*, dry_run: bool) -> int:
    # Imported here, not at module scope, for the reason `worker_async_session`
    # exists: it builds a throwaway NullPool engine because the cached one does
    # not survive `asyncio.run`.
    from sqlalchemy import delete, select
    from sqlalchemy.engine import make_url

    from app.core.clock import utc_today
    from app.core.config import get_settings
    from app.core.database import worker_async_session
    from app.core.security import hash_password
    from app.modules.auth.models import User
    from app.modules.community.models import StoreReport
    from app.modules.finance.service import normalize_merchant
    from app.modules.pricegraph import matching
    from app.modules.pricegraph.models import (
        ObservationSource,
        PriceObservation,
        Store,
        StoreSource,
    )
    from app.modules.pricegraph.normalize import normalize_item
    from app.modules.pricegraph.service import PricegraphService

    settings = get_settings()
    host = make_url(str(settings.database_url)).host

    if host not in LOCAL_HOSTS:
        print(f"refusing to run: database host {host!r} is not local", file=sys.stderr)
        return 1

    floor = settings.min_contributors_for_display
    if floor > CONTRIBUTORS_PER_PRICE:
        # Better to say so than to write data that renders as an empty map.
        print(
            f"refusing to run: this deployment shows a price only above "
            f"{floor} contributors, and the script seeds {CONTRIBUTORS_PER_PRICE}",
            file=sys.stderr,
        )
        return 1

    if dry_run:
        print(f"would seed {len(SHOPS)} shops, {len(ITEMS)} items, {len(REPORTS)} reports")
        print(f"  {CONTRIBUTORS_PER_PRICE} contributors per price (floor is {floor})")
        for shop in SHOPS[:5]:
            print(f"  {shop.name} @ {shop.latitude},{shop.longitude}")
        print(f"  ... and {len(SHOPS) - 5} more")
        return 0

    rng = random.Random(SEED)  # noqa: S311 -- invented prices, not a security decision
    today = utc_today()

    async with worker_async_session() as session:
        service = PricegraphService(session)

        # --- shops --------------------------------------------------------
        #
        # Upsert on the synthetic osm_id, in the same select-then-branch shape
        # as `import_osm_stores`. `confirmed_at` is set rather than left alone:
        # unlike a real import, this script owns these rows entirely.
        wanted = {f"demo:{shop.slug}": shop for shop in SHOPS}
        existing = {
            store.osm_id: store
            for store in (
                await session.execute(select(Store).where(Store.osm_id.in_(wanted)))
            ).scalars()
            if store.osm_id is not None
        }

        stores: dict[str, Store] = {}
        created = 0
        for osm_id, shop in wanted.items():
            store = existing.get(osm_id)
            if store is None:
                store = Store(
                    name=shop.name,
                    normalized_name=normalize_merchant(shop.name) or shop.name.lower(),
                    osm_id=osm_id,
                    source=StoreSource.OSM_IMPORT.value,
                )
                session.add(store)
                created += 1
            store.latitude = Decimal(shop.latitude)
            store.longitude = Decimal(shop.longitude)
            store.pincode = shop.pincode
            store.city = "Chennai"
            store.state = "Tamil Nadu"
            if shop.confirmed and store.confirmed_at is None:
                # `confirmed_at` is a timestamp while everything else here is a
                # date; midnight UTC is the honest reading of "confirmed today".
                store.confirmed_at = datetime(today.year, today.month, today.day, tzinfo=UTC)
            stores[shop.slug] = store

        await session.flush()

        # --- items --------------------------------------------------------
        #
        # Through the real normalizer and matcher, so the canonical rows are
        # shaped exactly as a receipt upload would shape them.
        items = []
        for entry in ITEMS:
            normalized = normalize_item(entry.line)
            if normalized is None:
                print(f"skipping unparseable demo line: {entry.line!r}", file=sys.stderr)
                continue
            match = await matching.resolve_item(session, normalized, allow_create=True)
            if match is None:
                print(f"could not resolve demo line: {entry.line!r}", file=sys.stderr)
                continue
            items.append((entry, normalized, match.item))

        await session.flush()

        # --- observations -------------------------------------------------
        #
        # Wipe this script's previous prices first, and only this script's:
        # every observation at a `demo:` shop came from a run of this file, so
        # the delete is exactly scoped and cannot touch a real contribution.
        #
        # Without it the script is only idempotent *within a day*. `observed_on`
        # is anchored to today so the prices stay inside the 30-day freshness
        # window, which means tomorrow's run computes different dates, misses
        # `record_observation`'s duplicate check, and lays down a second full
        # set. Running daily for a month would leave thirteen thousand rows
        # describing eighteen shops.
        demo_store_ids = [store.id for store in stores.values()]
        await session.execute(
            delete(PriceObservation).where(PriceObservation.store_id.in_(demo_store_ids))
        )

        #
        # Not every shop stocks every item: a graph where all 18 shops carry
        # all 10 items is not a graph anyone would recognise, and it gives the
        # comparison engines nothing to distinguish.
        observations = 0
        for slug, store in stores.items():
            for entry, normalized, item in items:
                if rng.random() < 0.25:
                    continue

                # One price per shop, jittered per contributor by a rupee or
                # two -- which is what independent people reading the same
                # shelf label actually produce.
                low, high = Decimal(entry.low), Decimal(entry.high)
                shelf = low + (high - low) * Decimal(str(round(rng.random(), 3)))

                for nth in range(CONTRIBUTORS_PER_PRICE):
                    jitter = Decimal(str(rng.uniform(-1.5, 1.5))).quantize(Decimal("0.01"))
                    price = max(Decimal("1.00"), (shelf + jitter).quantize(Decimal("0.01")))
                    observed = today - timedelta(days=rng.randint(0, MAX_AGE_DAYS))
                    # Distinct per contributor, so the k-anonymity floor clears;
                    # derived rather than random, so a second run recognises
                    # them and writes nothing. No user row is needed -- an
                    # observation deliberately carries no user_id.
                    contributor = uuid.uuid5(CONTRIBUTOR_NS, f"{slug}|{normalized.key}|{nth}")
                    written = await service.record_observation(
                        user_id=contributor,
                        canonical_item_id=item.id,
                        store_id=store.id,
                        unit_price=price,
                        observed_on=observed,
                        confidence=Decimal("0.900"),
                        pack_size=normalized.pack_size,
                        pack_unit=normalized.pack_unit,
                        source=ObservationSource.RECEIPT,
                    )
                    if written is not None:
                        observations += 1

        await session.flush()

        # --- item reach -----------------------------------------------------
        #
        # Runs last, because it counts the observations written above. Skip it
        # and every seeded item stays `is_provisional`, which
        # `/pricegraph/items/search` filters out -- so the shops would carry
        # prices the search box swears do not exist. Setting the flag by hand
        # clears that symptom and leaves `observation_count` at zero, so this
        # goes through the same method the real ingest path uses.
        for _, _, item in items:
            await service.refresh_item_reach(item)

        # --- community reports --------------------------------------------
        #
        # These need a real user, because `store_reports` is tenant-scoped and
        # cascades from `users`. Everything above does not.
        demo_user = (
            await session.execute(select(User).where(User.email == DEMO_EMAIL))
        ).scalar_one_or_none()
        if demo_user is None:
            demo_user = User(
                email=DEMO_EMAIL,
                # Unusable by design: nothing should be able to sign in as the
                # account that owns demo content.
                password_hash=hash_password(uuid.uuid4().hex),
                display_name=DEMO_NAME,
            )
            session.add(demo_user)
            await session.flush()

        already = {
            (report.store_id, report.item_text)
            for report in (
                await session.execute(
                    select(StoreReport).where(StoreReport.user_id == demo_user.id)
                )
            ).scalars()
        }

        reports = 0
        for report in REPORTS:
            store = stores[report.shop_slug]
            if (store.id, report.item_text) in already:
                continue
            session.add(
                StoreReport(
                    user_id=demo_user.id,
                    store_id=store.id,
                    kind=report.kind,
                    item_text=report.item_text,
                    price=Decimal(report.price) if report.price is not None else None,
                    note=report.note,
                )
            )
            reports += 1

        await session.commit()

    print(
        f"seeded {len(stores)} shops ({created} new), {len(items)} items, "
        f"{observations} observations, {reports} reports"
    )
    print("the landing map opens on Chennai, where all of this is.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be written without touching the database",
    )
    args = parser.parse_args()
    return asyncio.run(_seed(dry_run=args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
