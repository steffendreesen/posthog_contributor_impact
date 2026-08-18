from __future__ import annotations

import pandas as pd
import pytest
from pandera.errors import SchemaError

from github_etl.schemas import (
    CommitSchema,
    FinalCommitSchema,
    IssueSchema,
    PrCommitSchema,
    PrIssueLinkSchema,
    PullRequestSchema,
    empty_final_frame,
)


def test_empty_final_frame_validates() -> None:
    FinalCommitSchema.validate(empty_final_frame())


def test_commit_schema_accepts_null_contributor() -> None:
    frame = pd.DataFrame(
        {
            "commit_id": ["abc"],
            "contributor": [None],
            "committed_at": [pd.Timestamp("2026-08-01", tz="UTC")],
        }
    )
    CommitSchema.validate(frame)


def test_pr_commit_link_schema() -> None:
    PrCommitSchema.validate(pd.DataFrame({"commit_id": ["abc"], "pr_number": [1]}))


def test_pull_request_schema_rejects_unknown_state() -> None:
    frame = pd.DataFrame(
        {
            "pr_number": [1],
            "head_ref": ["feat"],
            "base_ref": ["master"],
            "merged": [False],
            "merged_into_default": [False],
            "state": ["DRAFT"],
            "updated_at": [pd.Timestamp("2026-08-01", tz="UTC")],
            "comment_count": [0],
        }
    )
    with pytest.raises(SchemaError):
        PullRequestSchema.validate(frame)


def test_issue_and_link_schemas() -> None:
    IssueSchema.validate(
        pd.DataFrame({"issue_number": [10], "comment_count": [3]})
    )
    PrIssueLinkSchema.validate(
        pd.DataFrame({"pr_number": [1], "issue_number": [10]})
    )


def test_final_schema_rejects_misaligned_pr_lists() -> None:
    frame = empty_final_frame()
    row = {
        "commit_id": "abc",
        "committed_at": pd.Timestamp("2026-08-01", tz="UTC"),
        "contributor": "alice",
        "branch": ["feat"],
        "pr": [1, 2],
        "pr_state": ["OPEN"],
        "number_of_comments_on_pr": [4],
        "has_pr_been_merged_into_main": False,
        "connected_issue": [],
        "number_of_comments_on_connected_issue": [],
    }
    frame = pd.concat([frame, pd.DataFrame([row])], ignore_index=True)
    with pytest.raises(SchemaError):
        FinalCommitSchema.validate(frame)
