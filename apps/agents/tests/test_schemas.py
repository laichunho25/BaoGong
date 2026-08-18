"""The Literals in ``schemas.py`` against the enums they copy.

``schemas.py`` cannot import a Django model - pydantic needs these values at
import time, before the app registry exists - so the platform's enums are
restated there by hand. That is a copy, and copies drift. These tests are the
thing that makes the drift loud: the day somebody adds a service tag or a line
item label, one of them fails here rather than three weeks later, as a model
answering with a value the database rejects.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from apps.agents import schemas
from apps.providers.models import BankType, ServiceCategory
from apps.rfq.models import CompanyType, LineItemLabel, Timeline


def _values(literal: Any) -> set[str]:
    return set(literal.__args__)


def test_service_codes_match_the_platform_tags() -> None:
    assert _values(schemas.ServiceCode) == set(ServiceCategory.values)


def test_company_type_codes_match_the_form() -> None:
    assert _values(schemas.CompanyTypeCode) == set(CompanyType.values)


def test_timeline_codes_match_the_form() -> None:
    assert _values(schemas.TimelineCode) == set(Timeline.values)


def test_bank_type_codes_match_the_form() -> None:
    assert _values(schemas.BankTypeCode) == set(BankType.values)


def test_line_item_codes_match_the_comparison_basis() -> None:
    assert _values(schemas.LineItemCode) == set(LineItemLabel.values)


def test_the_schemas_refuse_a_field_nobody_declared() -> None:
    """``extra="forbid"`` is what turns an invented field into a fallback
    instead of a key silently dropped on the way to the database."""
    with pytest.raises(ValidationError):
        schemas.RfqIntakeOut(
            title="x",
            services_needed=[],
            missing_fields=[],
            clarifying_questions=[],
            confidence=0.5,
            recommended_provider="Some Company Limited",
        )
