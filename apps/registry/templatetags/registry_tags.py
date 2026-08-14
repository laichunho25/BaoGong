"""Expose the registry notice copy to templates.

The wording lives in ``apps.registry.notices`` and only there: it is the one
place the deregistration text can be reviewed and the one place a test can
assert that it claims no reason for the removal. Templates call these tags so
that no page can quietly reword it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django import template

from apps.registry import notices

if TYPE_CHECKING:
    from apps.registry.models import Licensee

register = template.Library()


@register.simple_tag
def deregistration_notice(licensee: Licensee | None) -> str:
    if licensee is None:
        return ""
    return notices.deregistration_notice(licensee)


@register.simple_tag
def deregistration_headline(licensee: Licensee | None) -> str:
    if licensee is None:
        return ""
    return notices.deregistration_headline(licensee)
