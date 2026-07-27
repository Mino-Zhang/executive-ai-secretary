from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

from .security import as_utc, utc_now


class DomainStatusLike(Protocol):
    status: str
    source_data_as_of: datetime | None


def effective_domain_status(
    row: DomainStatusLike,
    stale_after_hours: int,
    *,
    now: datetime | None = None,
) -> str:
    """Derive wall-clock staleness without mutating the last ingestion outcome."""

    if row.status != "fresh" or row.source_data_as_of is None:
        return row.status
    current = as_utc(now or utc_now())
    if as_utc(row.source_data_as_of) < current - timedelta(hours=stale_after_hours):
        return "stale"
    return "fresh"
