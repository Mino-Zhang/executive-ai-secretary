from __future__ import annotations

from typing import Any

ALLOWED_FILTER_OPERATORS = {"eq", "neq", "in", "gte", "lte", "contains"}
ALLOWED_SORT_DIRECTIONS = {"asc", "desc"}


def _strings(value: Any, *, limit: int, width: int = 160) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value[:limit]:
        text = str(item).strip()[:width]
        if text and text not in result:
            result.append(text)
    return result


def normalize_query_spec(
    raw: Any,
    *,
    question: str,
    organization_scope: dict[str, Any],
) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    entities: dict[str, list[str]] = {}
    if isinstance(source.get("entities"), dict):
        for kind, values in list(source["entities"].items())[:12]:
            name = str(kind).strip()[:80]
            if name:
                entities[name] = _strings(values, limit=20)

    raw_time = source.get("time_range") if isinstance(source.get("time_range"), dict) else {}
    time_range = {
        "start": str(raw_time.get("start") or "")[:20] or None,
        "end": str(raw_time.get("end") or "")[:20] or None,
        "grain": str(raw_time.get("grain") or "")[:20] or None,
        "expression": str(raw_time.get("expression") or "")[:120] or None,
    }
    raw_comparison = (
        source.get("comparison") if isinstance(source.get("comparison"), dict) else {}
    )
    comparison = {
        "type": str(raw_comparison.get("type") or "none")[:40],
        "baseline": str(raw_comparison.get("baseline") or "")[:120] or None,
    }
    filters: list[dict[str, Any]] = []
    if isinstance(source.get("filters"), list):
        for item in source["filters"][:20]:
            if not isinstance(item, dict) or item.get("operator") not in ALLOWED_FILTER_OPERATORS:
                continue
            field = str(item.get("field") or "").strip()[:80]
            if field:
                filters.append(
                    {"field": field, "operator": item["operator"], "value": item.get("value")}
                )
    sort: list[dict[str, str]] = []
    if isinstance(source.get("sort"), list):
        for item in source["sort"][:5]:
            if not isinstance(item, dict) or item.get("direction") not in ALLOWED_SORT_DIRECTIONS:
                continue
            field = str(item.get("field") or "").strip()[:80]
            if field:
                sort.append({"field": field, "direction": item["direction"]})
    try:
        limit = max(1, min(int(source.get("limit", 20)), 100))
    except (TypeError, ValueError):
        limit = 20
    return {
        "normalized_question": str(source.get("normalized_question") or question).strip()[:12000],
        "metrics": _strings(source.get("metrics"), limit=20),
        "analysis_goals": _strings(source.get("analysis_goals"), limit=12),
        "entities": entities,
        "time_range": time_range,
        "comparison": comparison,
        # Always inject the server-authorized scope; model output is ignored here.
        "organization_scope": organization_scope,
        "filters": filters,
        "sort": sort,
        "limit": limit,
        "reference_sources": _strings(source.get("reference_sources"), limit=12),
        "unresolved_ambiguities": _strings(
            source.get("unresolved_ambiguities"), limit=12, width=240
        ),
    }
