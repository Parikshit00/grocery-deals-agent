"""ISO week keys and printed date ranges (the source of leaflet validity, never the calendar)."""
from datetime import date, timedelta

from app.agents.base import iso_key, iso_week, parse_validity


def test_iso_key_uses_iso_year_across_new_year():
    assert iso_key(date(2026, 12, 28)) == "2026-W53"
    assert iso_key(date(2026, 12, 28) + timedelta(days=7)) == "2027-W01"
    assert iso_key(date(2027, 1, 1)) == "2026-W53"


def test_iso_week_bounds():
    assert iso_week(date(2026, 10, 14)) == (date(2026, 10, 12), date(2026, 10, 18))


def test_range_with_year_on_end_only():
    text = "Gültig vom 02.07. bis 08.07.2026 in allen Filialen"
    assert parse_validity(text, date(2026, 7, 3)) == (date(2026, 7, 2), date(2026, 7, 8))


def test_range_without_year_uses_nearest_year():
    assert parse_validity("13.10.-18.10.", date(2026, 10, 12)) == (
        date(2026, 10, 13), date(2026, 10, 18))


def test_range_with_en_dash_and_spaces():
    assert parse_validity("Angebote 01.10. – 07.10.", date(2026, 9, 30)) == (
        date(2026, 10, 1), date(2026, 10, 7))


def test_range_across_new_year():
    assert parse_validity("29.12.-03.01.", date(2026, 12, 28)) == (
        date(2026, 12, 29), date(2027, 1, 3))


def test_no_range_or_impossible_date():
    assert parse_validity("Nur solange der Vorrat reicht", date(2026, 10, 12)) is None
    assert parse_validity("31.02.-03.03.", date(2026, 2, 20)) is None
