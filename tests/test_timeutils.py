from datetime import timezone

import pytest

from changelorg.timeutils import parse_datetime, parse_duration, parse_window


def test_parse_datetime_accepts_date() -> None:
    parsed = parse_datetime("2026-07-04")

    assert parsed.tzinfo == timezone.utc
    assert parsed.hour == 0


def test_parse_duration_rejects_invalid_unit() -> None:
    with pytest.raises(ValueError):
        parse_duration("3x")


def test_parse_window_relative_since() -> None:
    window = parse_window(since="2d", until="2026-07-04T12:00:00Z")

    assert window.start.isoformat() == "2026-07-02T12:00:00+00:00"
    assert window.end.isoformat() == "2026-07-04T12:00:00+00:00"
