from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def baseline_csv() -> Path:
    """Six licensees, shaped like the real register."""
    return FIXTURE_DIR / "tcsp_baseline.csv"


@pytest.fixture
def changed_csv() -> Path:
    """One of every diff type against the baseline."""
    return FIXTURE_DIR / "tcsp_changed.csv"


@pytest.fixture
def collapsed_csv() -> Path:
    """Two of the six rows survive - must trip the sanity check."""
    return FIXTURE_DIR / "tcsp_collapsed.csv"
