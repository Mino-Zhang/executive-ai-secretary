from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from executive_ai_api.data_freshness import effective_domain_status


@dataclass
class DomainRow:
    status: str
    source_data_as_of: datetime | None


def test_wall_clock_age_marks_only_successful_old_data_as_stale() -> None:
    now = datetime(2026, 7, 28, 2, 0, tzinfo=UTC)
    assert effective_domain_status(
        DomainRow("fresh", now - timedelta(hours=31)), 30, now=now
    ) == "stale"
    assert effective_domain_status(
        DomainRow("fresh", now - timedelta(hours=29)), 30, now=now
    ) == "fresh"
    assert effective_domain_status(
        DomainRow("failed", now - timedelta(hours=100)), 30, now=now
    ) == "failed"
    assert effective_domain_status(DomainRow("fresh", None), 30, now=now) == "fresh"
