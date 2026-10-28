"""Recovery decisions never authorize replacing an accepted image task."""

from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError

MAX_POLL_ERRORS = 3
MAX_RATE_LIMIT_RETRIES = 2
MAX_URL_REFRESHES = 1
MAX_DOWNLOAD_ATTEMPTS = 3


def retry_after(value: object, default: float) -> float:
    try:
        seconds = float(str(value))
    except (TypeError, ValueError):
        try:
            date = parsedate_to_datetime(str(value))
            if date.tzinfo is None:
                date = date.replace(tzinfo=UTC)
            seconds = (date - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return default
    # Invalid/unbounded values must not cause a tight loop or an early retry.
    return max(0.0, seconds) if 0 <= seconds <= 60 else 61.0


def no_acceptance_rate_limit(status: int, payload: Mapping) -> bool:
    if status != 429 or any(
        payload.get(key)
        for key in (
            "id",
            "task_id",
            "generation_id",
            "data",
            "images",
            "output",
            "url",
            "b64_json",
        )
    ):
        return False
    error = payload.get("error")
    if not isinstance(error, Mapping):
        return False
    code = str(error.get("code", "")).casefold()
    message = str(error.get("message", "")).casefold()
    return code in {
        "rate_limit_exceeded",
        "rate_limited",
        "too_many_requests",
        "channel_capacity_exceeded",
    } and not any(
        word in message
        for word in (
            "do not",
            "don't",
            "unknown",
            "settlement",
            "reconcil",
            "pending",
            "不要",
            "核对",
        )
    )


def transient_download(error: Exception) -> bool:
    if isinstance(error, HTTPError):
        return error.code in {408, 429, 500, 502, 503, 504}
    return isinstance(error, OSError)


def refreshable_url(error: Exception) -> bool:
    return isinstance(error, HTTPError) and error.code in {403, 404, 410}
