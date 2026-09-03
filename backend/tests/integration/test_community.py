"""Community reports end to end (ADR-013 §7).

The anti-abuse controls are the substance of this feature rather than a layer
on top, so they are what this file asserts. All of them are structural: a
unique constraint, a weighted threshold, or an arithmetic impossibility. There
is no moderation queue and no admin role, so there is nothing here that depends
on somebody watching.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text

from app.modules.pricegraph.models import Store, StoreSource

pytestmark = pytest.mark.integration


async def _store(db_session, name="Corner Store") -> Store:
    store = Store(
        name=name,
        normalized_name=name.lower(),
        pincode="560001",
        latitude=Decimal("12.9716"),
        longitude=Decimal("77.5946"),
        source=StoreSource.USER_PIN.value,
    )
    db_session.add(store)
    await db_session.commit()
    return store


async def _report(client, headers, store_id, **overrides):
    payload = {
        "store_id": str(store_id),
        "kind": "good_price",
        "item_text": "AMUL TAAZA MILK 500ML",
        "price": "28.00",
        "note": "Cheaper than the supermarket",
    } | overrides
    response = await client.post("/api/v1/community/reports", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


class TestReporting:
    async def test_a_report_is_created_and_pays_points(self, client, auth_headers, db_session):
        """Points on submission, and this is the only place they are.

        A report *is* the contribution. Refusing to pay until somebody else
        turns up would mean the first person in any new area works for nothing,
        which is exactly the person a crowdsourced map needs most. The daily cap
        keeps it from being farmable, and a disputed report costs trust, which
        is worth more than the points.
        """
        store = await _store(db_session)
        report = await _report(client, auth_headers, store.id)

        assert report["item_text"] == "AMUL TAAZA MILK 500ML"
        assert report["promoted"] is False
        assert report["mine"] is True

        balance = (await client.get("/api/v1/points/balance", headers=auth_headers)).json()
        assert balance["balance"] > 0

    async def test_a_good_price_report_needs_a_price(self, client, auth_headers, db_session):
        store = await _store(db_session)
        response = await client.post(
            "/api/v1/community/reports",
            headers=auth_headers,
            json={
                "store_id": str(store.id),
                "kind": "good_price",
                "item_text": "AMUL TAAZA MILK 500ML",
            },
        )
        assert response.status_code == 400

    async def test_a_unique_item_report_needs_no_price(self, client, auth_headers, db_session):
        """A shop's specialty is a fact about stock, not about price."""
        store = await _store(db_session)
        report = await _report(client, auth_headers, store.id, kind="unique_item", price=None)
        assert report["price"] is None

    async def test_reports_are_readable_without_signing_in(self, client, auth_headers, db_session):
        """A map with pins nobody can inspect teaches nothing about the
        product."""
        store = await _store(db_session)
        await _report(client, auth_headers, store.id)

        response = await client.get("/api/v1/community/reports", params={"store_id": str(store.id)})
        assert response.status_code == 200
        assert len(response.json()) == 1


class TestVerification:
    async def test_you_cannot_verify_your_own_report(self, client, auth_headers, db_session):
        store = await _store(db_session)
        report = await _report(client, auth_headers, store.id)

        response = await client.post(
            f"/api/v1/community/reports/{report['id']}/verify",
            headers=auth_headers,
            json={"agrees": True},
        )
        assert response.status_code == 409

    async def test_one_verification_per_person(
        self, client, auth_headers, second_user_headers, db_session
    ):
        """Enforced by `uq_report_verifications_user_id_report_id`.

        A service-level check would be bypassed by the next call site somebody
        adds; a constraint would not.
        """
        store = await _store(db_session)
        report = await _report(client, auth_headers, store.id)

        first = await client.post(
            f"/api/v1/community/reports/{report['id']}/verify",
            headers=second_user_headers,
            json={"agrees": True},
        )
        second = await client.post(
            f"/api/v1/community/reports/{report['id']}/verify",
            headers=second_user_headers,
            json={"agrees": True},
        )
        assert first.status_code == 201
        assert second.status_code == 409

    async def test_the_database_enforces_it(self, db_session):
        constraints = (
            (
                await db_session.execute(
                    text("""
                    SELECT conname FROM pg_constraint
                     WHERE conrelid = 'report_verifications'::regclass AND contype = 'u'
                    """)
                )
            )
            .scalars()
            .all()
        )
        assert "uq_report_verifications_user_id_report_id" in constraints

    async def test_a_verifier_earns_points(
        self, client, auth_headers, second_user_headers, db_session
    ):
        store = await _store(db_session)
        report = await _report(client, auth_headers, store.id)
        await client.post(
            f"/api/v1/community/reports/{report['id']}/verify",
            headers=second_user_headers,
            json={"agrees": True},
        )

        balance = (await client.get("/api/v1/points/balance", headers=second_user_headers)).json()
        assert balance["balance"] > 0


class TestVoting:
    async def test_one_vote_per_person_and_it_toggles(
        self, client, auth_headers, second_user_headers, db_session
    ):
        store = await _store(db_session)
        report = await _report(client, auth_headers, store.id)
        url = f"/api/v1/community/report/{report['id']}/vote"

        assert (await client.post(url, headers=second_user_headers)).json()["upvoted"] is True
        assert (await client.post(url, headers=second_user_headers)).json()["upvoted"] is False
        assert (await client.post(url, headers=second_user_headers)).json()["upvoted"] is True

        reports = (
            await client.get("/api/v1/community/reports", params={"store_id": str(store.id)})
        ).json()
        assert reports[0]["upvote_count"] == 1

    async def test_you_cannot_upvote_your_own(self, client, auth_headers, db_session):
        store = await _store(db_session)
        report = await _report(client, auth_headers, store.id)

        response = await client.post(
            f"/api/v1/community/report/{report['id']}/vote", headers=auth_headers
        )
        assert response.status_code == 409

    async def test_the_author_is_paid_not_the_voter(
        self, client, auth_headers, second_user_headers, registered, db_session
    ):
        """Paying the voter would make clicking the cheapest way to farm, and
        there is no verification step on a click to gate it behind."""
        store = await _store(db_session)
        report = await _report(client, auth_headers, store.id)

        before = (await client.get("/api/v1/points/balance", headers=second_user_headers)).json()[
            "balance"
        ]
        await client.post(
            f"/api/v1/community/report/{report['id']}/vote", headers=second_user_headers
        )
        after = (await client.get("/api/v1/points/balance", headers=second_user_headers)).json()[
            "balance"
        ]

        assert after == before, "the voter must not be paid for clicking"

    async def test_the_database_enforces_one_vote(self, db_session):
        constraints = (
            (
                await db_session.execute(
                    text("""
                    SELECT conname FROM pg_constraint
                     WHERE conrelid = 'community_votes'::regclass AND contype = 'u'
                    """)
                )
            )
            .scalars()
            .all()
        )
        assert "uq_community_votes_one_per_user" in constraints


class TestComments:
    async def test_a_comment_is_added_and_listed(
        self, client, auth_headers, second_user_headers, db_session
    ):
        store = await _store(db_session)
        report = await _report(client, auth_headers, store.id)

        created = await client.post(
            f"/api/v1/community/reports/{report['id']}/comments",
            headers=second_user_headers,
            json={"body": "Still this price yesterday"},
        )
        assert created.status_code == 201

        listed = (await client.get(f"/api/v1/community/reports/{report['id']}/comments")).json()
        assert len(listed) == 1
        assert listed[0]["body"] == "Still this price yesterday"

    async def test_retracting_hides_it(self, client, auth_headers, second_user_headers, db_session):
        store = await _store(db_session)
        report = await _report(client, auth_headers, store.id)
        comment = (
            await client.post(
                f"/api/v1/community/reports/{report['id']}/comments",
                headers=second_user_headers,
                json={"body": "wrong, sorry"},
            )
        ).json()

        await client.delete(
            f"/api/v1/community/comments/{comment['id']}", headers=second_user_headers
        )
        listed = (await client.get(f"/api/v1/community/reports/{report['id']}/comments")).json()
        assert listed == []

    async def test_one_user_cannot_retract_anothers_comment(
        self, client, auth_headers, second_user_headers, db_session
    ):
        store = await _store(db_session)
        report = await _report(client, auth_headers, store.id)
        comment = (
            await client.post(
                f"/api/v1/community/reports/{report['id']}/comments",
                headers=second_user_headers,
                json={"body": "mine"},
            )
        ).json()

        response = await client.delete(
            f"/api/v1/community/comments/{comment['id']}", headers=auth_headers
        )
        assert response.status_code == 404, (
            "another user's comment is indistinguishable from missing"
        )


class TestDisputes:
    async def test_enough_disputes_hide_a_report(self, client, auth_headers, db_session):
        """Three rather than one: a single dissenter is often somebody who
        visited on a different day, and hiding on one dispute would hand any
        user a veto over everyone else's contributions."""
        from app.modules.community.service import HIDE_AFTER_DISPUTES

        store = await _store(db_session)
        report = await _report(client, auth_headers, store.id)

        for index in range(HIDE_AFTER_DISPUTES):
            registration = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": f"disputer{index}@example.com",
                    "password": "CorrectHorse9Battery",
                    "display_name": f"Disputer {index}",
                    "base_currency": "INR",
                },
            )
            headers = {"Authorization": f"Bearer {registration.json()['access_token']}"}
            await client.post(
                f"/api/v1/community/reports/{report['id']}/verify",
                headers=headers,
                json={"agrees": False},
            )

        listed = (
            await client.get("/api/v1/community/reports", params={"store_id": str(store.id)})
        ).json()
        assert listed == [], "a disputed report is no longer shown"


class TestMyContributions:
    async def test_a_user_can_see_and_retract_everything_they_added(
        self, client, auth_headers, db_session
    ):
        store = await _store(db_session)
        report = await _report(client, auth_headers, store.id)
        await client.post(
            f"/api/v1/community/reports/{report['id']}/comments",
            headers=auth_headers,
            json={"body": "my own note"},
        )

        reports = (await client.get("/api/v1/community/me/reports", headers=auth_headers)).json()
        comments = (await client.get("/api/v1/community/me/comments", headers=auth_headers)).json()

        assert len(reports) == 1
        assert len(comments) == 1
        assert all(c["mine"] for c in comments)

        await client.delete(f"/api/v1/community/reports/{report['id']}", headers=auth_headers)
        after = (await client.get("/api/v1/community/me/reports", headers=auth_headers)).json()
        assert after[0]["status"] == "retracted"

    async def test_trust_is_published_with_its_reasons(self, client, auth_headers):
        """A user whose contribution was not promoted can read exactly what
        would change that."""
        trust = (await client.get("/api/v1/community/me/trust", headers=auth_headers)).json()

        assert Decimal(trust["score"]) > 0
        assert trust["factors"], "ADR-002: a score with no factors is a bug"

        rubric = (await client.get("/api/v1/community/trust/rubric")).json()
        assert rubric["weights"]
        assert rubric["notes"]


class TestPromotionIntoTheSharedGraph:
    async def test_an_agreed_report_becomes_a_price(self, client, auth_headers, db_session):
        """The path that makes local shops representable at all.

        No API indexes the corner store, and it prints nothing OCR can read. A
        report that enough trusted people confirm enters the same
        `price_observations` table a receipt feeds -- at a lower confidence,
        permanently, because a receipt is a document and a report is a
        recollection.
        """
        from app.modules.pricegraph.models import CanonicalItem

        store = await _store(db_session)
        # A canonical item must already exist: a report is a claim, not an
        # observation, and letting one mint catalogue entries would fill the
        # catalogue with things nobody has ever bought.
        db_session.add(
            CanonicalItem(
                canonical_name="Amul Taaza Milk 500ml",
                normalized_key="amul|taaza milk|500ml",
                brand="amul",
                pack_size=Decimal(500),
                pack_unit="ml",
                is_provisional=False,
            )
        )
        await db_session.commit()

        report = await _report(client, auth_headers, store.id)

        for index in range(3):
            registration = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": f"confirmer{index}@example.com",
                    "password": "CorrectHorse9Battery",
                    "display_name": f"Confirmer {index}",
                    "base_currency": "INR",
                },
            )
            headers = {"Authorization": f"Bearer {registration.json()['access_token']}"}
            await client.post(
                f"/api/v1/community/reports/{report['id']}/verify",
                headers=headers,
                json={"agrees": True},
            )

        observations = (
            await db_session.execute(
                text("SELECT count(*) FROM price_observations WHERE source = 'user_report'")
            )
        ).scalar_one()
        assert observations == 1, "three confirmations should clear the trust threshold"

        listed = (
            await client.get("/api/v1/community/reports", params={"store_id": str(store.id)})
        ).json()
        assert listed[0]["promoted"] is True

    async def test_one_confirmation_is_not_enough(
        self, client, auth_headers, second_user_headers, db_session
    ):
        from app.modules.pricegraph.models import CanonicalItem

        store = await _store(db_session)
        db_session.add(
            CanonicalItem(
                canonical_name="Amul Taaza Milk 500ml",
                normalized_key="amul|taaza milk|500ml",
                brand="amul",
                pack_size=Decimal(500),
                pack_unit="ml",
                is_provisional=False,
            )
        )
        await db_session.commit()

        report = await _report(client, auth_headers, store.id)
        await client.post(
            f"/api/v1/community/reports/{report['id']}/verify",
            headers=second_user_headers,
            json={"agrees": True},
        )

        observations = (
            await db_session.execute(
                text("SELECT count(*) FROM price_observations WHERE source = 'user_report'")
            )
        ).scalar_one()
        assert observations == 0

    async def test_a_report_with_no_canonical_item_still_stands(
        self, client, auth_headers, second_user_headers, db_session
    ):
        """A shop's specialty often has no catalogue entry until somebody buys
        it and uploads the receipt. The report is still shown; it just is not a
        price yet."""
        store = await _store(db_session)
        report = await _report(
            client, auth_headers, store.id, item_text="Grandma's homemade pickle 500g"
        )
        await client.post(
            f"/api/v1/community/reports/{report['id']}/verify",
            headers=second_user_headers,
            json={"agrees": True},
        )

        listed = (
            await client.get("/api/v1/community/reports", params={"store_id": str(store.id)})
        ).json()
        assert len(listed) == 1
        assert listed[0]["promoted"] is False
