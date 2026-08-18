"""A1 - turn what a buyer wrote into a form they then correct.

AI_AGENTS A1, with two deliberate departures recorded here because both are the
kind of thing that must not be discovered later by reading code:

1. **The codes are the platform's own enums**, not the spec's shorter ones
   (``hk_private_limited`` rather than ``private_limited``, ``within_1_month``
   rather than ``1_month``). The output of this agent is a pre-filled form
   whose fields are the model's fields; a translation table between two enums
   is a place where a wrong prefill hides, and it buys nothing.
2. **There is a ``title``.** The form requires one and a prefill that leaves the
   required field empty makes the buyer do the one bit of typing the feature
   exists to save.

What this agent may not do is more important than what it does. It writes
nothing. ``services.draft_rfq_prefill`` returns values for a form; only the
buyer pressing Submit creates an ``Rfq`` (CLAUDE.md rule 3, AI_AGENTS A1). The
buyer's own words stay on the record in ``Rfq.raw_input`` beside the fields, so
the difference between what they wrote and what the model made of it is
visible afterwards.

The fallback is keyword matching, and it is genuinely useful: most of these
requirements say 「注册香港公司 + 开户」 and nothing else, which rules read as
well as a model does. What rules cannot do is read a budget out of a sentence,
so they do not try unless the buyer wrote a Hong Kong dollar amount in as many
words - AI_AGENTS A1 sets the hallucinated-budget rate at zero, and that is a
rule about this path too.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, ClassVar, Final

from apps.agents.base import BaseAgent
from apps.agents.redaction import redact
from apps.agents.schemas import RfqIntakeOut

if TYPE_CHECKING:
    from pydantic import BaseModel

#: Longest buyer paragraph worth sending. Anything past this is a pasted email
#: thread, and the tail of it is not the requirement.
MAX_INPUT_CHARS = 4000

#: Service keywords, checked in order. Simplified and traditional forms both
#: appear: buyers paste from WeChat, and half of what they paste came from a
#: Hong Kong firm's own Chinese copy.
_SERVICE_KEYWORDS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    (
        "incorporation",
        (
            "注册公司",
            "註冊公司",
            "开公司",
            "開公司",
            "成立公司",
            "注册香港",
            "註冊香港",
            # 「注册一家香港公司」 is how people actually write it, and the
            # measure word between the verb and the noun defeats every pattern
            # above. Matching 「香港公司」 on its own would not do: a buyer who
            # already has one writes it too.
            "注册一家",
            "註冊一家",
            "开一家",
            "開一家",
            "成立一家",
            "设立公司",
            "設立公司",
            "incorporat",
            "set up a company",
        ),
    ),
    (
        "bank_account_assist",
        (
            "开户",
            "開戶",
            "开账户",
            "開賬戶",
            "银行账户",
            "銀行賬戶",
            "公户",
            "公戶",
            "bank account",
        ),
    ),
    ("company_secretary", ("公司秘书", "公司秘書", "法定秘书", "法定秘書", "company secretary")),
    (
        "registered_address",
        ("注册地址", "註冊地址", "挂靠地址", "掛靠地址", "registered address", "registered office"),
    ),
    ("accounting", ("做账", "做賬", "记账", "記賬", "会计", "會計", "bookkeeping", "accounting")),
    ("tax_filing", ("报税", "報稅", "利得税", "利得稅", "tax filing", "tax return")),
    ("audit_liaison", ("审计", "審計", "核数", "核數", "audit")),
    ("trademark", ("商标", "商標", "trademark")),
    (
        "work_visa",
        ("工作签证", "工作簽證", "受养人签证", "受養人簽證", "work visa", "employment visa"),
    ),
)

#: Bank names buyers actually type, mapped to the three types the form offers.
_BANK_KEYWORDS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    (
        "traditional",
        (
            "汇丰",
            "滙豐",
            "hsbc",
            "恒生",
            "恆生",
            "hang seng",
            "中银",
            "中銀",
            "boc",
            "渣打",
            "standard chartered",
            "实体银行",
            "實體銀行",
        ),
    ),
    ("virtual", ("众安", "眾安", "za bank", "天星", "airstar", "welab", "虚拟银行", "虛擬銀行")),
    (
        "emi",
        (
            "airwallex",
            "空中云汇",
            "空中雲匯",
            "currenxie",
            "statrys",
            "payoneer",
            "pingpong",
            "电子钱包",
            "電子錢包",
        ),
    ),
)

_COMPANY_TYPE_KEYWORDS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("hk_branch", ("分公司", "分行", "branch")),
    ("hk_rep_office", ("代表处", "代表處", "representative office")),
    (
        "offshore",
        (
            "bvi",
            "开曼",
            "開曼",
            "cayman",
            "萨摩亚",
            "薩摩亞",
            "samoa",
            "塞舌尔",
            "塞舌爾",
            "seychelles",
            "离岸公司",
            "離岸公司",
        ),
    ),
    ("hk_private_limited", ("有限公司", "私人公司", "limited company", "private limited")),
)

_TIMELINE_KEYWORDS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("asap", ("越快越好", "尽快", "盡快", "加急", "急", "asap", "urgent")),
    (
        "within_1_month",
        ("一个月", "一個月", "1个月", "1個月", "本月", "within a month", "one month"),
    ),
    ("within_3_months", ("三个月", "三個月", "3个月", "3個月", "一季", "three months")),
    ("flexible", ("不急", "不着急", "不著急", "随时", "隨時", "flexible", "no rush")),
)

_NATIONALITY_KEYWORDS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("CN", ("内地", "內地", "大陆", "大陸", "中国", "中國", "mainland", "chinese")),
    ("HK", ("香港", "本地", "hong kong")),
    ("TW", ("台湾", "台灣", "taiwan")),
    ("SG", ("新加坡", "singapore")),
    ("US", ("美国", "美國", "美籍")),
    ("GB", ("英国", "英國", "英籍")),
)

#: A Hong Kong dollar amount, written the way buyers write it. The currency
#: marker is required on purpose: 「预算一万」 is as likely to be RMB, and a
#: number this agent guessed the currency of is a number the buyer never wrote.
_HKD_AMOUNT = re.compile(
    r"(?:(?:HK\$|HKD|港币|港幣|港元)\s*([\d,]+(?:\.\d+)?)\s*(万|萬|k|K)?)"
    r"|(?:([\d,]+(?:\.\d+)?)\s*(万|萬|k|K)?\s*(?:HKD|港币|港幣|港元))",
    re.IGNORECASE,
)

_BANK_ACCOUNT_HINTS: Final[tuple[str, ...]] = ("开户", "開戶", "账户", "賬戶", "bank account")

#: Every field the form asks about, so the fallback can say what it did not read.
_FORM_FIELDS: Final[tuple[str, ...]] = (
    "title",
    "company_type",
    "shareholder_nationalities",
    "business_nature",
    "services_needed",
    "needs_bank_account",
    "preferred_bank_types",
    "budget",
    "timeline",
)


class RfqIntakeAgent(BaseAgent):
    name: ClassVar[str] = "rfq_intake"
    model: ClassVar[str] = "claude-haiku-4-5-20251001"
    prompt_file: ClassVar[str] = "rfq_intake_v1.md"
    output_schema: ClassVar[type[BaseModel]] = RfqIntakeOut
    max_tokens: ClassVar[int] = 1024
    #: A buyer is watching a spinner, so this one is called synchronously and
    #: given less time than the queued agents get.
    timeout_s: ClassVar[int] = 20
    object_type: ClassVar[str] = "rfq.Rfq"

    def build_user_prompt(self, ctx: dict[str, Any]) -> str:
        """The buyer's paragraph, redacted and truncated.

        Redacted even though this text is going into the buyer's own form:
        COMPLIANCE section 4 has no exception for A1, and buyers who were told
        not to write a phone number write one anyway. What comes back with a
        ``[PHONE]`` in it is a field the buyer will fix, which is the correct
        outcome - the wall carries requirements, not contact details.
        """
        text = redact(str(ctx.get("raw_input", "")))[:MAX_INPUT_CHARS]
        return f"--- what the buyer wrote ---\n{text}\n--- end ---"

    def fallback(self, ctx: dict[str, Any], reason: str) -> RfqIntakeOut:
        """Keyword rules over the same text, with nothing inferred.

        Everything it could not read comes back in ``missing_fields`` and the
        confidence is fixed low, so the page can tell the buyer plainly that
        this was a keyword match and every box needs checking.
        """
        text = str(ctx.get("raw_input", ""))
        lowered = text.lower()

        services = _matches(lowered, _SERVICE_KEYWORDS)
        banks = _matches(lowered, _BANK_KEYWORDS)
        nationalities = _matches(lowered, _NATIONALITY_KEYWORDS)
        company_type = _first_match(lowered, _COMPANY_TYPE_KEYWORDS) or "undecided"
        timeline = _first_match(lowered, _TIMELINE_KEYWORDS) or "undecided"
        needs_bank = bool(banks) or any(hint in lowered for hint in _BANK_ACCOUNT_HINTS)
        if needs_bank and "bank_account_assist" not in services:
            services.append("bank_account_assist")
        budget_max = _hkd_amount(text)

        filled = {
            "company_type": company_type != "undecided",
            "shareholder_nationalities": bool(nationalities),
            "services_needed": bool(services),
            "needs_bank_account": needs_bank,
            "preferred_bank_types": bool(banks),
            "timeline": timeline != "undecided",
            "budget": budget_max is not None,
            # Neither of these can be had from a keyword list: a title and a
            # line of business are summaries, and a summary is the thing rules
            # cannot write.
            "title": False,
            "business_nature": False,
        }
        return RfqIntakeOut(
            title="",
            company_type=company_type,  # type: ignore[arg-type]
            shareholder_nationalities=nationalities,
            business_nature="",
            services_needed=services,  # type: ignore[arg-type]
            needs_bank_account=needs_bank,
            preferred_bank_types=banks,  # type: ignore[arg-type]
            # Only ever a ceiling: a single figure a buyer names is the most
            # they mean to spend, and inventing a floor to go with it would be
            # exactly the fabrication this path exists to avoid.
            budget_min_hkd=None,
            budget_max_hkd=budget_max,
            timeline=timeline,  # type: ignore[arg-type]
            missing_fields=[field for field in _FORM_FIELDS if not filled[field]],
            # Left empty on purpose. A follow-up question is a sentence about
            # what this buyer said, and a template pretending to be one wastes
            # the buyer's answer.
            clarifying_questions=[],
            # Not a confidence in the reading so much as an instruction not to
            # lean on it: these are keywords, and nobody read the paragraph.
            confidence=0.3,
        )


def _matches(lowered: str, table: tuple[tuple[str, tuple[str, ...]], ...]) -> list[str]:
    """Every code in ``table`` whose keywords appear, in the table's order."""
    return [code for code, words in table if any(word in lowered for word in words)]


def _first_match(lowered: str, table: tuple[tuple[str, tuple[str, ...]], ...]) -> str | None:
    """The first code whose keywords appear. Tables are ordered most specific
    first, so 「分公司」 wins over the 「有限公司」 inside it."""
    found = _matches(lowered, table)
    return found[0] if found else None


def _hkd_amount(text: str) -> int | None:
    """The largest explicit HKD figure in the text, in whole dollars.

    Returns ``None`` unless the buyer wrote a currency next to the number. An
    amount whose currency was inferred is a fabricated fact about somebody's
    budget, and it would land in a form field they may not re-read.
    """
    amounts: list[int] = []
    for match in _HKD_AMOUNT.finditer(text):
        digits = match.group(1) or match.group(3)
        scale = match.group(2) or match.group(4) or ""
        if not digits:
            continue
        value = float(digits.replace(",", ""))
        if scale in ("万", "萬"):
            value *= 10_000
        elif scale.lower() == "k":
            value *= 1_000
        amounts.append(int(value))
    return max(amounts) if amounts else None
