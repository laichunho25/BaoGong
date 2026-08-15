"""The recompute tasks.

Called eagerly (``CELERY_TASK_ALWAYS_EAGER`` in the test settings), so these are
really tests of the orchestration: that a task which arrives after the company
is gone logs and returns instead of retrying forever, and that the bulk rebuild
touches every company whose number could be wrong.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from apps.reviews import tasks

if TYPE_CHECKING:
    from collections.abc import Callable

    from apps.accounts.models import User
    from apps.providers.models import Provider
    from apps.reviews.models import Review

pytestmark = pytest.mark.django_db


def test_recompute_rating_refreshes_one_company(
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
) -> None:
    provider = make_provider()
    make_review(provider=provider, author=make_user(), overall="4.5")

    assert tasks.recompute_rating(str(provider.pk)) == "4.95"


def test_recompute_rating_tolerates_a_deleted_company(
    make_provider: Callable[..., Provider],
) -> None:
    """A verification result can land after the company row is gone; retrying
    that forever would block the queue behind a company that no longer exists."""
    provider = make_provider()
    provider_id = str(provider.pk)
    provider.delete()

    assert tasks.recompute_rating(provider_id) is None


def test_the_bulk_rebuild_covers_every_scored_company(
    make_provider: Callable[..., Provider],
    make_user: Callable[..., User],
    make_review: Callable[..., Review],
) -> None:
    """The formula is a product decision that will change; when it does, every
    cached number on the site is wrong until this runs."""
    first, second = make_provider(), make_provider()
    for provider in (first, second):
        make_review(provider=provider, author=make_user(), overall="4.5")
        tasks.recompute_rating(str(provider.pk))
    first.refresh_from_db()
    first.rating_cached = Decimal("1.00")
    first.save(update_fields=["rating_cached"])

    assert tasks.recompute_all_ratings() == 2

    first.refresh_from_db()
    assert first.rating_cached == Decimal("4.95")
