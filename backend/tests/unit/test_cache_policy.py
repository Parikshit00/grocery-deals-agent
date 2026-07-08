"""Cache-write policy: never serve stale weeks, never overwrite good rows with bad scans."""
from datetime import date, timedelta
from types import SimpleNamespace

from app.services.prospekt import _cache_decision, _is_current

TODAY = date.today()
MON = TODAY - timedelta(days=TODAY.weekday())


def _row(page_count: int, valid_from=MON, valid_to=MON + timedelta(days=6)):
    return SimpleNamespace(
        valid_from=valid_from, valid_to=valid_to, payload={"page_count": page_count}
    )


def test_is_current_within_week():
    assert _is_current(MON, MON + timedelta(days=6))


def test_is_current_rejects_expired():
    assert not _is_current(MON - timedelta(days=14), MON - timedelta(days=8))


def test_is_current_rejects_future_week():
    assert not _is_current(MON + timedelta(days=7), MON + timedelta(days=13))


def test_is_current_requires_valid_to():
    assert not _is_current(MON, None)


def test_is_current_tolerates_unknown_valid_from():
    assert _is_current(None, MON + timedelta(days=6))


def test_incomplete_capture_is_not_cached():
    assert _cache_decision(False, 30, _row(30)) == "partial"


def test_page_regression_keeps_existing_row():
    assert _cache_decision(True, 5, _row(30)) == "regression"


def test_at_60_percent_boundary_writes():
    assert _cache_decision(True, 18, _row(30)) is None


def test_no_existing_row_writes():
    assert _cache_decision(True, 3, None) is None


def test_expired_existing_row_is_ignored():
    old = _row(30, valid_from=MON - timedelta(days=14), valid_to=MON - timedelta(days=8))
    assert _cache_decision(True, 3, old) is None
