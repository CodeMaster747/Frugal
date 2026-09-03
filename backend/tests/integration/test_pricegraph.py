"""The shared price graph, end to end (ADR-013).

Three guarantees are asserted here, and they are the reason the module exists:

1. **The k-anonymity floor holds.** A single observation says "exactly one
   person shopped here and bought this", which is a sentence about a person.
2. **The shared row names nobody.** No user id, no receipt id, no basket.
3. **One contribution per user per item per store per day**, enforced by the
   database rather than by a check somebody can forget.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.modules.pricegraph.models import (
    CanonicalItem,
    ObservationSource,
    PriceObservation,
    Store,
    StoreSource,
)
from app.modules.pricegraph.service import PricegraphService

pytestmark = pytest.mark.integration

TODAY = date.today()


async def _store(session, *, name="Corner Store", pincode="560001", lat="12.9716", lon="77.5946"):
    store = Store(
        name=name,
        normalized_name=name.lower(),
        pincode=pincode,
        latitude=Decimal(lat),
        longitude=Decimal(lon),
        source=StoreSource.USER_PIN.value,
    )
    session.add(store)
    await session.flush()
    return store


async def _item(session, *, key="amul|taaza milk|500ml", name="Amul Taaza Milk 500ml"):
    item = CanonicalItem(
        canonical_name=name,
        normalized_key=key,
        brand="amul",
        pack_size=Decimal(500),
        pack_unit="ml",
        is_provisional=False,
    )
    session.add(item)
    await session.flush()
    return item


async def _observe(session, item, store, *, user_id, price, on=None):
    return await PricegraphService(session).record_observation(
        user_id=user_id,
        canonical_item_id=item.id,
        store_id=store.id,
        unit_price=Decimal(price),
        observed_on=on or TODAY,
        confidence=Decimal("0.900"),
        pack_size=Decimal(500),
        pack_unit="ml",
        source=ObservationSource.RECEIPT,
    )


class TestTheAnonymityFloor:
    async def test_one_contributor_is_not_shown(self, db_session, settings):
        """A price nobody else has confirmed is a fact about one shopper."""
        store = await _store(db_session)
        item = await _item(db_session)
        await _observe(db_session, item, store, user_id=uuid.uuid4(), price="32.00")
        await db_session.commit()

        prices = await PricegraphService(db_session).prices_for_item(item.id)
        assert prices == [], (
            f"a single observation was published; the floor is "
            f"{settings.min_contributors_for_display}"
        )

    async def test_it_appears_once_the_floor_is_met(self, db_session, settings):
        store = await _store(db_session)
        item = await _item(db_session)
        for _ in range(settings.min_contributors_for_display):
            await _observe(db_session, item, store, user_id=uuid.uuid4(), price="32.00")
        await db_session.commit()

        prices = await PricegraphService(db_session).prices_for_item(item.id)
        assert len(prices) == 1
        assert prices[0].contributors >= settings.min_contributors_for_display
        assert prices[0].median_price == "32.00"
        assert prices[0].store.name == "Corner Store"

    async def test_a_stale_price_falls_out_of_the_window(self, db_session, settings):
        """A shelf price from four months ago is history, not guidance."""
        store = await _store(db_session)
        item = await _item(db_session)
        old = TODAY - timedelta(days=120)
        for _ in range(settings.min_contributors_for_display):
            await _observe(db_session, item, store, user_id=uuid.uuid4(), price="32.00", on=old)
        await db_session.commit()

        assert await PricegraphService(db_session).prices_for_item(item.id) == []


class TestTheSharedRowNamesNobody:
    def test_the_table_has_no_user_column(self):
        """Structural. A `user_id` here would make every aggregate a join away
        from a per-person purchase history."""
        columns = set(PriceObservation.__table__.columns.keys())
        assert "user_id" not in columns
        assert "receipt_id" not in columns
        assert "receipt_line_item_id" not in columns

    def test_it_records_a_date_and_not_a_timestamp(self):
        """Time of day plus store is close to identifying."""
        from sqlalchemy import Date

        assert isinstance(PriceObservation.__table__.c.observed_on.type, Date)

    async def test_the_contributor_hash_is_not_linkable_across_stores(self, db_session):
        """The store and the date are inside the HMAC, so an attacker holding
        the whole table cannot assemble one person's history from it."""
        from app.modules.pricegraph.service import contributor_hash

        user = uuid.uuid4()
        one, two = uuid.uuid4(), uuid.uuid4()

        assert contributor_hash(user, one, TODAY) != contributor_hash(user, two, TODAY)
        assert contributor_hash(user, one, TODAY) != contributor_hash(
            user, one, TODAY - timedelta(days=1)
        )
        assert contributor_hash(user, one, TODAY) == contributor_hash(user, one, TODAY)


class TestOnePerContributorPerDay:
    async def test_a_second_contribution_is_refused(self, db_session):
        store = await _store(db_session)
        item = await _item(db_session)
        user = uuid.uuid4()

        first = await _observe(db_session, item, store, user_id=user, price="32.00")
        second = await _observe(db_session, item, store, user_id=user, price="29.00")
        await db_session.commit()

        assert first is not None
        assert second is None, "the same user contributed the same item twice in one day"

    async def test_the_database_enforces_it_not_the_service(self, db_session):
        """Belt and braces, and the braces are the ones that matter.

        A service check can be bypassed by a new call site; a unique constraint
        cannot.
        """
        constraints = (
            (
                await db_session.execute(
                    text("""
                SELECT conname FROM pg_constraint
                 WHERE conrelid = 'price_observations'::regclass AND contype = 'u'
                """)
                )
            )
            .scalars()
            .all()
        )
        assert "uq_price_observations_one_per_contributor_per_day" in constraints

    async def test_different_users_both_count(self, db_session):
        store = await _store(db_session)
        item = await _item(db_session)

        assert await _observe(db_session, item, store, user_id=uuid.uuid4(), price="32") is not None
        assert await _observe(db_session, item, store, user_id=uuid.uuid4(), price="30") is not None


class TestCheaperElsewhere:
    async def test_it_reports_a_real_saving_with_caveats(self, db_session, settings):
        cheap = await _store(db_session, name="Cheap Store")
        item = await _item(db_session)
        for _ in range(settings.min_contributors_for_display):
            await _observe(db_session, item, cheap, user_id=uuid.uuid4(), price="25.00")
        await db_session.commit()

        result = await PricegraphService(db_session).cheaper_elsewhere(
            item.id, paid=Decimal("32.00")
        )
        assert result is not None
        assert result.best_price == "25.00"
        assert result.saving == "7.00"
        assert result.caveats, "a crowdsourced price must say how many people said so"
        assert "shoppers" in " ".join(result.caveats)

    async def test_no_saving_is_a_real_answer(self, db_session, settings):
        store = await _store(db_session)
        item = await _item(db_session)
        for _ in range(settings.min_contributors_for_display):
            await _observe(db_session, item, store, user_id=uuid.uuid4(), price="40.00")
        await db_session.commit()

        assert (
            await PricegraphService(db_session).cheaper_elsewhere(item.id, paid=Decimal("32.00"))
            is None
        )


class TestTheMap:
    async def test_it_returns_pins_inside_the_box(self, client, db_session, settings):
        store = await _store(db_session)
        item = await _item(db_session)
        for _ in range(settings.min_contributors_for_display):
            await _observe(db_session, item, store, user_id=uuid.uuid4(), price="32.00")
        await db_session.commit()

        response = await client.get(
            "/api/v1/pricegraph/map",
            params={
                "min_lat": "12.9",
                "max_lat": "13.0",
                "min_lon": "77.5",
                "max_lon": "77.6",
            },
        )
        assert response.status_code == 200, response.text
        pins = response.json()
        assert any(p["store"]["name"] == "Corner Store" for p in pins)

    async def test_it_is_readable_without_signing_in(self, client):
        """The landing screen is a map. A signed-out visitor seeing an empty one
        would learn nothing about what the product is."""
        response = await client.get(
            "/api/v1/pricegraph/map",
            params={"min_lat": "12", "max_lat": "13", "min_lon": "77", "max_lon": "78"},
        )
        assert response.status_code == 200

    async def test_a_country_sized_viewport_is_refused(self, client):
        response = await client.get(
            "/api/v1/pricegraph/map",
            params={"min_lat": "8", "max_lat": "35", "min_lon": "68", "max_lon": "97"},
        )
        assert response.status_code == 400
        assert "Zoom in" in response.text

    async def test_an_inverted_box_is_refused(self, client):
        response = await client.get(
            "/api/v1/pricegraph/map",
            params={"min_lat": "13", "max_lat": "12", "min_lon": "77", "max_lon": "78"},
        )
        assert response.status_code == 400


class TestRetractionAndErasure:
    async def test_retraction_removes_and_erasure_anonymises(
        self, client, registered, auth_headers, db_session
    ):
        """Two different rights, and the difference matters to the person
        exercising them.

        Erasure anonymises: the price survives as a fact about the shop,
        because deleting it would silently degrade what everyone else sees and
        make the graph a function of churn. Retraction removes.
        """
        from app.modules.pricegraph.models import ReceiptPromotion
        from app.modules.receipts.models import Receipt, ReceiptLineItem, ReceiptStatus

        user = uuid.UUID(registered["user"]["id"])
        store = await _store(db_session)
        item = await _item(db_session)

        # Real rows: `receipt_promotions` carries foreign keys to both, which
        # is what makes an account deletion reach it by cascade.
        receipt = Receipt(
            user_id=user,
            s3_key=f"receipts/{user}/probe",
            content_type="image/jpeg",
            file_size_bytes=1024,
            status=ReceiptStatus.COMMITTED.value,
            total_extracted=Decimal("32.00"),
            date_extracted=TODAY,
        )
        db_session.add(receipt)
        await db_session.flush()

        line = ReceiptLineItem(
            user_id=user,
            receipt_id=receipt.id,
            line_number=1,
            description="AMUL TAAZA MILK 500ML",
            total_price=Decimal("32.00"),
            confidence=Decimal("0.900"),
        )
        db_session.add(line)
        await db_session.flush()

        observation = await _observe(db_session, item, store, user_id=user, price="32.00")
        assert observation is not None
        db_session.add(
            ReceiptPromotion(
                user_id=user,
                receipt_id=receipt.id,
                receipt_line_item_id=line.id,
                price_observation_id=observation.id,
            )
        )
        await db_session.flush()

        service = PricegraphService(db_session)

        anonymised = await service.anonymise(user)
        await db_session.flush()
        await db_session.refresh(observation)
        assert anonymised == 1
        assert observation.contributor_hash is None
        assert observation.pepper_version is None
        assert observation.retracted_at is None, "erasure must not remove the price"

        retracted = await service.retract_all(user)
        await db_session.flush()
        await db_session.refresh(observation)
        assert retracted == 1
        assert observation.retracted_at is not None

    async def test_a_retracted_price_disappears_from_reads(
        self, client, registered, auth_headers, db_session, settings
    ):
        store = await _store(db_session)
        item = await _item(db_session)
        observations = []
        for _ in range(settings.min_contributors_for_display):
            observations.append(
                await _observe(db_session, item, store, user_id=uuid.uuid4(), price="32.00")
            )
        await db_session.commit()

        assert await PricegraphService(db_session).prices_for_item(item.id)

        from datetime import UTC, datetime

        observations[0].retracted_at = datetime.now(UTC)
        await db_session.commit()

        # One contributor short of the floor now, so it stops being shown --
        # which is the floor working, not a separate rule.
        assert await PricegraphService(db_session).prices_for_item(item.id) == []
