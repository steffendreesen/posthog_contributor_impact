from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd
import pytest

from github_etl.client import QueryTimeoutError
from github_etl.extract import (
    GitHubExtractor,
    parse_commit_node,
    parse_history_commit_node,
    parse_pr_numbers_from_message,
    parse_pull_request_node,
)


def test_parse_commit_prefers_github_login() -> None:
    row = parse_commit_node(
        {
            "oid": "deadbeef",
            "committedDate": "2026-07-01T00:00:00Z",
            "author": {"name": "Alice Example", "user": {"login": "alice"}},
        }
    )
    assert row is not None
    assert row["contributor"] == "alice"
    assert row["committed_at"] == pd.Timestamp("2026-07-01T00:00:00Z", tz="UTC")


def test_parse_commit_falls_back_to_author_name() -> None:
    row = parse_commit_node(
        {
            "oid": "abcd",
            "committedDate": "2026-07-01T00:00:00Z",
            "author": {"name": "Alice Example", "user": None},
        }
    )
    assert row is not None
    assert row["contributor"] == "Alice Example"


def test_parse_pr_numbers_from_squash_title() -> None:
    assert parse_pr_numbers_from_message("feat(web): speed up query (#84094)") == [84094]
    assert parse_pr_numbers_from_message("chore: no pr here") == []
    assert parse_pr_numbers_from_message(None) == []
    assert parse_pr_numbers_from_message(
        "fix(experiments): Pass feature flag key to Max summary exposure query (#84038)\n\nCloses #99\n"
    ) == [84038]


def test_parse_history_commit_collects_pr_from_title() -> None:
    node = {
        "oid": "aaa",
        "committedDate": "2026-07-01T00:00:00Z",
        "messageHeadline": "feat: foo (#42)",
        "author": {"name": "a", "user": {"login": "alice"}},
    }
    parsed = parse_history_commit_node(node)
    assert parsed.commit_row["commit_id"] == "aaa"
    assert parsed.pr_commit_rows == [{"commit_id": "aaa", "pr_number": 42}]


def test_parse_history_commit_without_pr() -> None:
    node = {
        "oid": "bbb",
        "committedDate": "2026-07-01T00:00:00Z",
        "messageHeadline": "direct push",
        "author": {"name": "bot", "user": {"login": "bot"}},
    }
    parsed = parse_history_commit_node(node)
    assert parsed.pr_commit_rows == []


def test_parse_pull_request_node() -> None:
    pr_row, links, issues = parse_pull_request_node(
        {
            "number": 7,
            "headRefName": None,
            "baseRefName": "release",
            "merged": True,
            "state": "MERGED",
            "updatedAt": "2026-07-02T00:00:00Z",
            "totalCommentsCount": 1,
            "closingIssuesReferences": {
                "nodes": [{"number": 10, "comments": {"totalCount": 2}}]
            },
        },
        default_branch="master",
    )
    assert pr_row["merged_into_default"] is False
    assert links == [{"pr_number": 7, "issue_number": 10}]
    assert issues == [(10, 2)]


class _FakeClient:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def execute(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((query, variables))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _history_page(
    *,
    oid: str,
    committed_at: str,
    end_cursor: str | None = None,
    has_next: bool = False,
    default_branch: str = "master",
    message: str | None = None,
) -> dict[str, Any]:
    return {
        "repository": {
            "defaultBranchRef": {
                "name": default_branch,
                "target": {
                    "history": {
                        "pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor},
                        "nodes": [
                            {
                                "oid": oid,
                                "committedDate": committed_at,
                                "message": message,
                                "author": {"name": "dev", "user": {"login": "dev"}},
                            }
                        ],
                    }
                },
            }
        }
    }


def _commit_prs(*pairs: tuple[str, list[int]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for i, (sha, numbers) in enumerate(pairs):
        payload[f"c{i}"] = {
            "object": {
                "oid": sha,
                "associatedPullRequests": {
                    "nodes": [{"number": n} for n in numbers]
                },
            }
        }
    return payload


def _pr_details(*prs: dict[str, Any]) -> dict[str, Any]:
    return {
        f"p{i}": {"pullRequest": pr}
        for i, pr in enumerate(prs)
    }


def _pr(*, number: int, comments: int = 0, issue: int | None = None) -> dict[str, Any]:
    issues = []
    if issue is not None:
        issues.append({"number": issue, "comments": {"totalCount": 3}})
    return {
        "number": number,
        "headRefName": "feat",
        "baseRefName": "master",
        "merged": True,
        "state": "MERGED",
        "updatedAt": "2026-07-02T00:00:00Z",
        "totalCommentsCount": comments,
        "closingIssuesReferences": {"nodes": issues},
    }


def test_extractor_paginates_history_then_hydrates_prs() -> None:
    client = _FakeClient(
        [
            _history_page(
                oid="sha-new",
                committed_at="2026-07-20T00:00:00Z",
                end_cursor="c1",
                has_next=True,
                message="feat: new (#2)",
            ),
            _history_page(
                oid="sha-old",
                committed_at="2026-07-01T00:00:00Z",
                message="fix: old (#1)",
            ),
            _pr_details(_pr(number=1, comments=4), _pr(number=2, comments=9, issue=10)),
        ]
    )
    extractor = GitHubExtractor(client, "PostHog", "posthog")  # type: ignore[arg-type]
    tables = extractor.extract(datetime(2026, 6, 1, tzinfo=timezone.utc))
    assert sorted(tables.commits["commit_id"].tolist()) == ["sha-new", "sha-old"]
    assert sorted(tables.pull_requests["pr_number"].tolist()) == [1, 2]
    assert tables.issues["issue_number"].tolist() == [10]
    assert len(client.calls) == 3


def test_extractor_uses_graphql_fallback_when_title_has_no_pr() -> None:
    client = _FakeClient(
        [
            _history_page(
                oid="sha-bot",
                committed_at="2026-07-20T00:00:00Z",
                message="direct push",
            ),
            _commit_prs(("sha-bot", [9])),
            _pr_details(_pr(number=9, comments=1)),
        ]
    )
    extractor = GitHubExtractor(client, "PostHog", "posthog")  # type: ignore[arg-type]
    tables = extractor.extract(datetime(2026, 6, 1, tzinfo=timezone.utc))
    assert tables.pull_requests["pr_number"].tolist() == [9]
    assert len(client.calls) == 3


def test_extractor_shrinks_page_size_on_timeout() -> None:
    fresh = _history_page(oid="sha-9", committed_at="2026-07-20T00:00:00Z")
    client = _FakeClient(
        [
            QueryTimeoutError("boom"),
            fresh,
            _commit_prs(("sha-9", [])),
        ]
    )
    extractor = GitHubExtractor(client, "PostHog", "posthog")  # type: ignore[arg-type]
    tables = extractor.extract(datetime(2026, 6, 1, tzinfo=timezone.utc))
    assert list(tables.commits["commit_id"]) == ["sha-9"]
    assert client.calls[0][1]["pageSize"] == 100
    assert client.calls[1][1]["pageSize"] == 50


def test_extractor_empty_history_returns_typed_frames() -> None:
    client = _FakeClient(
        [
            {
                "repository": {
                    "defaultBranchRef": {
                        "name": "master",
                        "target": {
                            "history": {
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                                "nodes": [],
                            }
                        },
                    }
                }
            }
        ]
    )
    extractor = GitHubExtractor(client, "PostHog", "posthog")  # type: ignore[arg-type]
    tables = extractor.extract(datetime(2026, 6, 1, tzinfo=timezone.utc))
    assert tables.commits.empty
    assert tables.pull_requests.empty
    assert tables.default_branch == "master"


def test_extractor_raises_if_all_page_sizes_time_out() -> None:
    client = _FakeClient(
        [
            QueryTimeoutError("a"),
            QueryTimeoutError("b"),
            QueryTimeoutError("c"),
        ]
    )
    extractor = GitHubExtractor(client, "PostHog", "posthog")  # type: ignore[arg-type]
    with pytest.raises(QueryTimeoutError):
        extractor.extract(datetime(2026, 6, 1, tzinfo=timezone.utc))
