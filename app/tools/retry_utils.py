"""Shared retry utilities for external API calls."""

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

import httpx

from app.config import settings


class RetryableHTTPError(Exception):
    """Raised when an HTTP response indicates a transient server error (5xx)."""


api_retry = retry(
    # MAX_TOOL_RETRIES counts retries after the initial request.
    stop=stop_after_attempt(settings.max_tool_retries + 1),
    wait=wait_exponential(multiplier=1, min=1, max=4),
    retry=retry_if_exception_type((httpx.TimeoutException, RetryableHTTPError)),
    reraise=True,
)
