from datetime import datetime, timezone

from src.session_manager import is_session_valid


def test_session_is_invalid_at_expiry_boundary():
    expiry = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert is_session_valid(expiry, expiry) is False


def test_session_is_valid_before_expiry():
    expiry = datetime(2026, 1, 1, tzinfo=timezone.utc)
    now = datetime(2025, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    assert is_session_valid(now, expiry) is True


def test_session_is_invalid_after_expiry():
    expiry = datetime(2026, 1, 1, tzinfo=timezone.utc)
    now = datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc)
    assert is_session_valid(now, expiry) is False
