"""The two Celery tasks the claim flow depends on."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from django.utils import timezone

from apps.core.scanning import ScanStatus
from apps.core.uploads import inspect_upload
from apps.providers import services, tasks

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.accounts.models import User
    from apps.providers.models import Provider

pytestmark = pytest.mark.django_db


class TestScanTask:
    def test_it_records_the_verdict_on_the_row(
        self,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        make_upload: Callable[..., SimpleUploadedFile],
    ) -> None:
        claim = services.submit_claim(
            provider=make_provider(), user=make_user(), contact_name="Chan"
        )
        upload = make_upload()
        evidence = services.attach_evidence(
            claim=claim, upload=upload, inspected=inspect_upload(upload)
        )

        result = tasks.scan_claim_evidence(str(evidence.pk))

        assert result == ScanStatus.PENDING

    def test_a_file_deleted_before_the_worker_ran_is_not_an_error(self) -> None:
        # Retrying forever on a row that no longer exists would block the queue
        # behind a job that can never succeed.
        assert tasks.scan_claim_evidence(str(uuid4())) == "missing"


class TestPurgeTask:
    def test_it_deletes_only_what_is_past_its_retention_date(
        self,
        make_provider: Callable[..., Provider],
        make_user: Callable[..., User],
        make_upload: Callable[..., SimpleUploadedFile],
        moderator: User,
    ) -> None:
        claim = services.submit_claim(
            provider=make_provider(), user=make_user(), contact_name="Chan"
        )
        upload = make_upload()
        evidence = services.attach_evidence(
            claim=claim, upload=upload, inspected=inspect_upload(upload)
        )
        services.reject_claim(claim=claim, reviewer=moderator, reason="Not the licensee")

        assert tasks.purge_claim_evidence() == 0

        evidence.purge_at = timezone.now() - timedelta(seconds=1)
        evidence.save(update_fields=["purge_at"])

        assert tasks.purge_claim_evidence() == 1
