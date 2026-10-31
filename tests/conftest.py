"""Shared fixtures."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from db.seed_synthetic import Dataset, generate_dataset

TZ = ZoneInfo("America/New_York")
# A Monday at 08:00 office time, fixed so tests are reproducible.
FIXED_NOW = datetime(2026, 11, 2, 8, 0, tzinfo=TZ)


@pytest.fixture(scope="session")
def now() -> datetime:
    return FIXED_NOW


@pytest.fixture(scope="session")
def dataset() -> Dataset:
    return generate_dataset(now=FIXED_NOW, seed=2026)
