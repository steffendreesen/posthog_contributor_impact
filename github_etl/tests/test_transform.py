from __future__ import annotations

import pandas as pd

from github_etl.transform import join_commit_table


def _ts(stamp: str) -> pd.Timestamp:
    return pd.Timestamp(stamp, tz="UTC")


def _commits(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_empty_components_yield_empty_final_table() -> None:
    result = join_commit_table(
        pd.DataFrame(columns=["commit_id", "contributor", "committed_at"]),
        pd.DataFrame(columns=["commit_id", "pr_number"]),
        pd.DataFrame(
            columns=[
                "pr_number",
                "head_ref",
                "base_ref",
                "merged",
                "merged_into_default",
                "state",
                "updated_at",
                "comment_count",
            ]
        ),
        pd.DataFrame(columns=["pr_number", "issue_number"]),
        pd.DataFrame(columns=["issue_number", "comment_count"]),
    )
    assert result.empty
    assert list(result.columns) == [
        "commit_id",
        "committed_at",
        "contributor",
        "branch",
        "pr",
        "pr_state",
        "number_of_comments_on_pr",
        "has_pr_been_merged_into_main",
        "connected_issue",
        "number_of_comments_on_connected_issue",
    ]


def test_one_commit_one_pr_two_issues() -> None:
    commits = _commits(
        [
            {
                "commit_id": "aaa",
                "contributor": "alice",
                "committed_at": _ts("2026-07-01T12:00:00Z"),
            }
        ]
    )
    pr_commits = pd.DataFrame({"commit_id": ["aaa"], "pr_number": [1]})
    pull_requests = pd.DataFrame(
        {
            "pr_number": [1],
            "head_ref": ["feat-x"],
            "base_ref": ["master"],
            "merged": [True],
            "merged_into_default": [True],
            "state": ["MERGED"],
            "updated_at": [_ts("2026-07-02T00:00:00Z")],
            "comment_count": [7],
        }
    )
    pr_issue_links = pd.DataFrame(
        {"pr_number": [1, 1], "issue_number": [20, 10]}
    )
    issues = pd.DataFrame(
        {"issue_number": [10, 20], "comment_count": [3, 5]}
    )

    result = join_commit_table(
        commits, pr_commits, pull_requests, pr_issue_links, issues
    )
    assert len(result) == 1
    row = result.iloc[0]
    assert row["commit_id"] == "aaa"
    assert row["contributor"] == "alice"
    assert row["pr"] == [1]
    assert row["pr_state"] == ["MERGED"]
    assert row["branch"] == ["feat-x"]
    assert row["number_of_comments_on_pr"] == [7]
    assert bool(row["has_pr_been_merged_into_main"]) is True
    assert row["connected_issue"] == [10, 20]
    assert row["number_of_comments_on_connected_issue"] == [3, 5]


def test_one_commit_on_two_prs() -> None:
    commits = _commits(
        [
            {
                "commit_id": "aaa",
                "contributor": "bob",
                "committed_at": _ts("2026-07-01T12:00:00Z"),
            }
        ]
    )
    pr_commits = pd.DataFrame({"commit_id": ["aaa", "aaa"], "pr_number": [2, 1]})
    pull_requests = pd.DataFrame(
        {
            "pr_number": [1, 2],
            "head_ref": ["feat-a", "feat-b"],
            "base_ref": ["master", "master"],
            "merged": [False, True],
            "merged_into_default": [False, True],
            "state": ["OPEN", "MERGED"],
            "updated_at": [_ts("2026-07-02T00:00:00Z")] * 2,
            "comment_count": [1, 9],
        }
    )
    result = join_commit_table(
        commits,
        pr_commits,
        pull_requests,
        pd.DataFrame(columns=["pr_number", "issue_number"]),
        pd.DataFrame(columns=["issue_number", "comment_count"]),
    )
    row = result.iloc[0]
    assert row["pr"] == [1, 2]
    assert row["pr_state"] == ["OPEN", "MERGED"]
    assert row["branch"] == ["feat-a", "feat-b"]
    assert row["number_of_comments_on_pr"] == [1, 9]
    assert bool(row["has_pr_been_merged_into_main"]) is True
    assert row["connected_issue"] == []


def test_commit_with_no_pr_keeps_empty_lists() -> None:
    commits = _commits(
        [
            {
                "commit_id": "ddd",
                "contributor": "bot",
                "committed_at": _ts("2026-07-03T00:00:00Z"),
            }
        ]
    )
    result = join_commit_table(
        commits,
        pd.DataFrame(columns=["commit_id", "pr_number"]),
        pd.DataFrame(
            columns=[
                "pr_number",
                "head_ref",
                "base_ref",
                "merged",
                "merged_into_default",
                "state",
                "updated_at",
                "comment_count",
            ]
        ),
        pd.DataFrame(columns=["pr_number", "issue_number"]),
        pd.DataFrame(columns=["issue_number", "comment_count"]),
    )
    row = result.iloc[0]
    assert row["commit_id"] == "ddd"
    assert row["pr"] == []
    assert row["connected_issue"] == []
    assert bool(row["has_pr_been_merged_into_main"]) is False


def test_pr_with_no_issues_uses_empty_lists() -> None:
    commits = _commits(
        [
            {
                "commit_id": "ccc",
                "contributor": None,
                "committed_at": _ts("2026-07-03T00:00:00Z"),
            }
        ]
    )
    pr_commits = pd.DataFrame({"commit_id": ["ccc"], "pr_number": [3]})
    pull_requests = pd.DataFrame(
        {
            "pr_number": [3],
            "head_ref": [None],
            "base_ref": ["release"],
            "merged": [True],
            "merged_into_default": [False],
            "state": ["MERGED"],
            "updated_at": [_ts("2026-07-03T00:00:00Z")],
            "comment_count": [0],
        }
    )
    result = join_commit_table(
        commits,
        pr_commits,
        pull_requests,
        pd.DataFrame(columns=["pr_number", "issue_number"]),
        pd.DataFrame(columns=["issue_number", "comment_count"]),
    )
    row = result.iloc[0]
    assert row["connected_issue"] == []
    assert bool(row["has_pr_been_merged_into_main"]) is False
    assert row["branch"] == [None]
    assert pd.isna(row["contributor"])
