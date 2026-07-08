from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from changelorg.models import TimeWindow, ensure_utc, utc_now


_DURATION_UNITS = {
    "m": timedelta(minutes=1),
    "h": timedelta(hours=1),
    "d": timedelta(days=1),
    "w": timedelta(weeks=1),
}


def parse_datetime(value: str) -> datetime:
    normalized = value.strip()
    if not normalized:
        raise ValueError("datetime value cannot be empty")

    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed_date = date.fromisoformat(normalized)
        parsed = datetime.combine(parsed_date, time.min, tzinfo=timezone.utc)

    return ensure_utc(parsed)


def parse_duration(value: str) -> timedelta:
    normalized = value.strip().lower()
    if len(normalized) < 2:
        raise ValueError("duration must look like 30m, 24h, 7d, or 2w")

    unit = normalized[-1]
    amount_text = normalized[:-1]
    if unit not in _DURATION_UNITS:
        raise ValueError("duration unit must be one of m, h, d, or w")

    try:
        amount = int(amount_text)
    except ValueError as exc:
        raise ValueError("duration amount must be an integer") from exc

    if amount <= 0:
        raise ValueError("duration amount must be positive")

    return _DURATION_UNITS[unit] * amount


def parse_window(since: str | None = None, until: str | None = None) -> TimeWindow:
    end = parse_datetime(until) if until else utc_now()
    if since is None:
        start = end - timedelta(days=7)
    else:
        stripped = since.strip()
        if stripped[-1:].lower() in _DURATION_UNITS:
            start = end - parse_duration(stripped)
        else:
            start = parse_datetime(stripped)
    return TimeWindow(start=start, end=end)
