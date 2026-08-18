"""Name -> agent class, for callers that only have a string.

ARCHITECTURE section 4 asks for this so that tasks and admin actions can name
an agent without importing it, and - more usefully - so that the kill-switch
listing on the compliance checklist ("Agent kill switch 全部可用", COMPLIANCE
section 8) can be checked against something enumerable rather than against
whatever happens to be imported.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from apps.agents.advisor import AdvisorAgent
from apps.agents.matching import MatchingAgent
from apps.agents.nnc1_extraction import Nnc1ExtractionAgent
from apps.agents.quote_analysis import QuoteAnalysisAgent
from apps.agents.review_moderation import ReviewModerationAgent
from apps.agents.rfq_intake import RfqIntakeAgent

if TYPE_CHECKING:
    from apps.agents.base import BaseAgent

AGENTS: dict[str, type[BaseAgent]] = {
    ReviewModerationAgent.name: ReviewModerationAgent,
    Nnc1ExtractionAgent.name: Nnc1ExtractionAgent,
    RfqIntakeAgent.name: RfqIntakeAgent,
    QuoteAnalysisAgent.name: QuoteAnalysisAgent,
    MatchingAgent.name: MatchingAgent,
    AdvisorAgent.name: AdvisorAgent,
}


def get_agent(name: str) -> BaseAgent:
    """Instantiate an agent by name. ``KeyError`` if it does not exist."""
    return AGENTS[name]()


def kill_switch_setting(name: str) -> str:
    """The setting that switches one agent off, e.g. ``AGENT_ENABLED_MATCHING``."""
    return f"AGENT_ENABLED_{name.upper()}"
