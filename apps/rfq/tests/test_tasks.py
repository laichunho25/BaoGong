"""The beat task behind the wall's deadline."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.rfq.models import Rfq, RfqStatus
from apps.rfq.tasks import expire_rfqs

pytestmark = pytest.mark.django_db


def test_the_sweep_only_touches_requests_past_their_deadline(open_rfq: Rfq) -> None:
    assert expire_rfqs() == 0

    Rfq.objects.filter(pk=open_rfq.pk).update(expires_at=timezone.now() - timedelta(hours=1))

    assert expire_rfqs() == 1
    open_rfq.refresh_from_db()
    assert open_rfq.status == RfqStatus.EXPIRED
