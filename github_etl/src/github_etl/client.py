from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from github_etl.auth import GitHubAuth

logger = logging.getLogger(__name__)

GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"


class RetryableGraphQLError(Exception):
    """Transient GitHub failure; wait and retry the same query."""

    def __init__(self, message: str, sleep_s: float | None = None) -> None:
        super().__init__(message)
        self.sleep_s = sleep_s


class QueryTimeoutError(Exception):
    """GitHub timed out or returned 502/504. Caller should shrink the page."""


class GraphQLClientError(Exception):
    """Fatal GraphQL or HTTP error."""


def _wait_retryable(retry_state: Any) -> float:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exc, RetryableGraphQLError) and exc.sleep_s is not None:
        return max(exc.sleep_s, 1.0)
    return wait_exponential(multiplier=1, min=1, max=60)(retry_state)


class GraphQLClient:
    def __init__(self, auth: GitHubAuth, timeout_s: float = 30.0) -> None:
        self._http = httpx.Client(
            headers=auth.headers(),
            timeout=timeout_s,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> GraphQLClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @retry(
        retry=retry_if_exception_type(RetryableGraphQLError),
        stop=stop_after_attempt(6),
        wait=_wait_retryable,
        reraise=True,
    )
    def execute(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = self._http.post(
                GITHUB_GRAPHQL_URL,
                json={"query": query, "variables": variables or {}},
            )
        except httpx.TimeoutException as exc:
            raise QueryTimeoutError("GitHub GraphQL request timed out") from exc

        if response.status_code in {502, 504}:
            raise QueryTimeoutError(
                f"GitHub GraphQL HTTP {response.status_code}: {response.text[:200]}"
            )

        retry_after = _retry_after_seconds(response)
        if response.status_code in {403, 429} or (
            response.status_code == 200 and _is_rate_limited(response)
        ):
            message = f"GitHub rate limited (HTTP {response.status_code})"
            remaining = response.headers.get("x-ratelimit-remaining")
            reset = response.headers.get("x-ratelimit-reset")
            sleep_s = retry_after
            if sleep_s is None and remaining == "0" and reset:
                sleep_s = max(int(reset) - int(time.time()), 1)
            if sleep_s is None:
                sleep_s = 60
            logger.warning("%s; sleeping %.0fs", message, sleep_s)
            raise RetryableGraphQLError(message, sleep_s=float(sleep_s))

        if response.status_code >= 500:
            raise RetryableGraphQLError(
                f"GitHub GraphQL HTTP {response.status_code}",
                sleep_s=5.0,
            )
        if response.status_code >= 400:
            raise GraphQLClientError(
                f"GitHub GraphQL HTTP {response.status_code}: {response.text[:500]}"
            )

        payload = response.json()
        errors = payload.get("errors") or []
        if errors:
            if _errors_look_like_timeout(errors):
                raise QueryTimeoutError(str(errors))
            if _errors_look_like_rate_limit(errors):
                raise RetryableGraphQLError(str(errors), sleep_s=60.0)
            if payload.get("data") is None:
                raise GraphQLClientError(f"GitHub GraphQL errors: {errors}")
            logger.warning("GraphQL returned partial data with errors: %s", errors)

        data = payload.get("data")
        if data is None:
            raise GraphQLClientError(f"GitHub GraphQL returned no data: {payload}")
        return data


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _is_rate_limited(response: httpx.Response) -> bool:
    remaining = response.headers.get("x-ratelimit-remaining")
    return remaining == "0"


def _errors_look_like_timeout(errors: list[dict[str, Any]]) -> bool:
    text = " ".join(str(err.get("message", "")) for err in errors).lower()
    return "couldn't respond" in text or "timeout" in text or "timed out" in text


def _errors_look_like_rate_limit(errors: list[dict[str, Any]]) -> bool:
    text = " ".join(str(err.get("message", "")) for err in errors).lower()
    return "rate limit" in text or "api rate limit" in text
