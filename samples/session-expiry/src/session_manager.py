from datetime import datetime


def is_session_valid(now: datetime, expires_at: datetime) -> bool:
    if now >= expires_at:
        return False
    return True
