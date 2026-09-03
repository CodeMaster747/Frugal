"""Tests that tenant scoping is structural, not a convention.

This is the guard against the highest-cost bug in a multi-tenant financial
product: returning one user's finances to another. The test compiles each
statement to SQL and asserts a user_id predicate is present, so a subclass that
builds an unscoped query fails the build rather than leaking in production.

From M1 onward this suite parametrises over every BaseRepository subclass, so
new repositories are covered automatically as they are added.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, SoftDeleteMixin, TenantMixin, TimestampMixin, UUIDMixin
from app.core.repository import BaseRepository


class OwnedThing(UUIDMixin, TenantMixin, TimestampMixin, SoftDeleteMixin, Base):
    """Stand-in for a user-owned table."""

    __tablename__ = "test_owned_thing"
    name: Mapped[str] = mapped_column(String(50))


class SharedThing(UUIDMixin, TimestampMixin, Base):
    """Stand-in for shared reference data, e.g. the product catalogue."""

    __tablename__ = "test_shared_thing"
    name: Mapped[str] = mapped_column(String(50))


class OwnedRepo(BaseRepository[OwnedThing]):
    model = OwnedThing


class SharedRepo(BaseRepository[SharedThing]):
    model = SharedThing


def sql(stmt) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": False}))


@pytest.fixture
def user_id() -> uuid.UUID:
    return uuid.uuid4()


class TestScopingIsEnforced:
    def test_scoped_select_filters_by_user(self, user_id):
        assert "user_id" in sql(OwnedRepo(None).scoped_select(user_id))  # type: ignore[arg-type]

    def test_scoped_select_excludes_soft_deleted_rows(self, user_id):
        assert "deleted_at IS NULL" in sql(OwnedRepo(None).scoped_select(user_id))  # type: ignore[arg-type]

    def test_deleted_rows_are_reachable_only_on_request(self, user_id):
        stmt = OwnedRepo(None).scoped_select(user_id, include_deleted=True)  # type: ignore[arg-type]
        assert "deleted_at IS NULL" not in sql(stmt)
        assert "user_id" in sql(stmt)  # still scoped -- the escape hatch is narrow


class TestTheTwoModesCannotBeConfused:
    """Shared reference data and tenant data use different entry points, and
    each refuses the other's model. The distinction is explicit so it cannot be
    made by accident."""

    def test_scoped_select_refuses_a_shared_model(self, user_id):
        with pytest.raises(TypeError, match="no user_id"):
            SharedRepo(None).scoped_select(user_id)  # type: ignore[arg-type]

    def test_global_select_refuses_a_tenant_model(self):
        with pytest.raises(TypeError, match="tenant-owned"):
            OwnedRepo(None).global_select()  # type: ignore[arg-type]

    def test_global_select_works_for_shared_data(self):
        assert "test_shared_thing" in sql(SharedRepo(None).global_select())  # type: ignore[arg-type]


class TestSubclassContract:
    def test_repository_without_a_model_is_rejected_at_definition(self):
        with pytest.raises(TypeError, match="must declare a `model`"):

            class Broken(BaseRepository):  # type: ignore[type-arg]
                pass


class TestEveryRepositoryIsScoped:
    """Sweeps every BaseRepository subclass in the application.

    This is the M1 exit criterion. Importing the module registers its
    repositories as subclasses, so each milestone's new repositories are
    covered here automatically -- nobody has to remember to add them.
    """

    def test_all_tenant_repositories_emit_a_user_predicate(self, user_id):
        # Importing for the side effect of subclass registration.
        import app.modules.auth.repository  # noqa: F401

        # A class whose __init_subclass__ raises is still registered as a
        # subclass, so the malformed repository from the test above appears
        # here. Skip anything without a model rather than tripping over it.
        subclasses = [c for c in BaseRepository.__subclasses__() if hasattr(c, "model")]
        assert len(subclasses) >= 3, f"expected the real repositories too, got {subclasses}"

        checked = []
        for repo_cls in subclasses:
            if not hasattr(repo_cls.model, "user_id"):
                # Skipping is legitimate for shared reference data -- products,
                # offer_snapshots, categories. It is never legitimate for a
                # signals model: that would be a table in the *other* database
                # mapped onto a repository whose scoping mechanism does not
                # exist over there, and this `continue` would wave it through.
                from app.core.signals_base import SignalsBase

                assert not issubclass(repo_cls.model, SignalsBase), (
                    f"{repo_cls.__name__} maps a personalization table onto "
                    "BaseRepository. Use SignalsRepository, which scopes by "
                    "`subject_id` -- see ADR-011."
                )
                continue
            statement = sql(repo_cls(None).scoped_select(user_id))  # type: ignore[arg-type]
            assert "user_id" in statement, f"{repo_cls.__name__} builds an unscoped query"
            checked.append(repo_cls.__name__)

        assert "RefreshTokenRepository" in checked


class TestEverySignalsRepositoryIsScoped:
    """The same sweep for the second database (ADR-011).

    A separate class over a separate base, deliberately. Sharing one would let
    `test_all_tenant_repositories_emit_a_user_predicate` check a signals
    repository for a `user_id` predicate that cannot exist there -- and its
    `hasattr` guard would skip it silently, which is the failure this pair of
    tests is arranged to make impossible.
    """

    def test_all_signals_repositories_emit_a_subject_predicate(self, user_id):
        # Importing for the side effect of subclass registration.
        import app.modules.personalization.repository  # noqa: F401
        from app.core.signals_repository import SignalsRepository

        subclasses = [c for c in SignalsRepository.__subclasses__() if hasattr(c, "model")]
        assert subclasses, "no signals repositories registered"

        for repo_cls in subclasses:
            statement = sql(repo_cls(None).scoped_select(user_id))  # type: ignore[arg-type]
            assert "subject_id" in statement, f"{repo_cls.__name__} builds an unscoped query"

    def test_a_model_without_a_subject_is_refused(self, user_id):
        """The mechanism working, not an obstacle to route around.

        `profile_archetypes` is shared reference data with no owner, and
        reaching it through a subject-scoped repository must fail loudly rather
        than quietly returning everyone's rows.
        """
        from app.core.signals_repository import SignalsRepository
        from app.modules.personalization.models import ProfileArchetype

        class ArchetypeRepo(SignalsRepository[ProfileArchetype]):  # type: ignore[type-var]
            model = ProfileArchetype

        with pytest.raises(TypeError, match="no subject_id"):
            ArchetypeRepo(None).scoped_select(user_id)  # type: ignore[arg-type]

    def test_the_users_table_is_the_documented_exception(self):
        """`users` cannot be tenant-scoped -- a login lookup happens *before* an
        identity exists. UserRepository therefore does not extend
        BaseRepository, which keeps it out of the sweep by construction rather
        than by an exclusion list someone could quietly extend.
        """
        from app.modules.auth.repository import UserRepository

        assert not issubclass(UserRepository, BaseRepository)
