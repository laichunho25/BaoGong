"""User-facing notices about a licensee's standing on the official register.

COMPLIANCE sections 1 and 3. The register publishes no removal reason and no
date column at all, so the only fact the platform may state about a licence
that stopped appearing is exactly that: it was listed on the date of one sync
and was not on the next. Wording that implies enforcement - struck off,
revoked, suspended, cancelled - would be an assertion about a named company
that the source data does not support, and the reasons are ordinary and
various: voluntary surrender, non-renewal, a merger, or a publishing error at
the source.

The copy lives here rather than in a template so that it is reviewable in one
place, translatable, and impossible to reword per page. It has not yet been
through legal review (COMPLIANCE preamble).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.utils import timezone
from django.utils.translation import gettext as _

if TYPE_CHECKING:
    from apps.registry.models import Licensee

# The "verify it yourself" link the notice tells the reader to follow lives in
# settings.REGISTRY_SOURCE_URL, alongside REGISTRY_SOURCE_NAME, so that the
# source attribution required on every page has a single definition.


def deregistration_notice(licensee: Licensee) -> str:
    """The notice shown in place of, never instead of, a removed licensee's page.

    Returns an empty string for a licensee still on the register, so callers
    can render it unconditionally.
    """
    if licensee.is_on_register:
        return ""

    last_seen = timezone.localtime(licensee.last_seen_at).strftime("%Y-%m-%d")
    return _(
        "此牌照已不在香港公司注册处最新公布的《信托或公司服务持牌人登记册》名单内。"
        "本平台最后一次在官方名单中见到该牌照的日期为 %(last_seen)s（香港时间）。"
        "官方名单不载明移除原因，本平台亦无从得知，"
        "请自行向官方登记册核实该公司的最新牌照状态。"
    ) % {"last_seen": last_seen}


def deregistration_headline(licensee: Licensee) -> str:
    """Short form for list rows and cards, where the full notice will not fit."""
    if licensee.is_on_register:
        return ""
    return _("已不在官方持牌名单内")
