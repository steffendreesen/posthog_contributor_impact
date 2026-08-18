from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from github_etl.auth import GitHubAuth
from github_etl.client import GraphQLClient, GraphQLClientError, QueryTimeoutError


def _response(
    status_code: int,
    json_body: dict | None = None,
    headers: dict[str, str] | None = None,
    text: str = "",
) -> httpx.Response:
    request = httpx.Request("POST", "https://api.github.com/graphql")
    kwargs: dict = {"status_code": status_code, "headers": headers or {}, "request": request}
    if json_body is not None:
        kwargs["json"] = json_body
    else:
        kwargs["text"] = text or "error"
    return httpx.Response(**kwargs)


def test_execute_returns_data() -> None:
    client = GraphQLClient(GitHubAuth(token="t"))
    client._http = MagicMock()
    client._http.post.return_value = _response(200, {"data": {"ok": True}})
    assert client.execute("query { viewer { login } }") == {"ok": True}


def test_execute_maps_502_to_timeout() -> None:
    client = GraphQLClient(GitHubAuth(token="t"))
    client._http = MagicMock()
    client._http.post.return_value = _response(502, text="bad gateway")
    with pytest.raises(QueryTimeoutError):
        client.execute("query { viewer { login } }")


def test_execute_maps_graphql_timeout_message() -> None:
    client = GraphQLClient(GitHubAuth(token="t"))
    client._http = MagicMock()
    client._http.post.return_value = _response(
        200,
        {"errors": [{"message": "We couldn't respond to your request in time"}]},
    )
    with pytest.raises(QueryTimeoutError):
        client.execute("query { viewer { login } }")


def test_execute_fatal_on_400() -> None:
    client = GraphQLClient(GitHubAuth(token="t"))
    client._http = MagicMock()
    client._http.post.return_value = _response(400, text="bad query")
    with pytest.raises(GraphQLClientError):
        client.execute("query { viewer { login } }")
