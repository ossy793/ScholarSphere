from datetime import UTC, datetime


def utcnow() -> datetime:
    """All times are stored in UTC."""
    return datetime.now(UTC)
