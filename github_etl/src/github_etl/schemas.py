from __future__ import annotations

from collections.abc import Sequence

import pandas as pd
import pandera.pandas as pa
from pandera.typing import Series


def _is_list(value: object) -> bool:
    return isinstance(value, list)


def _lists_same_length(*seqs: Sequence[object]) -> bool:
    lengths = [len(seq) for seq in seqs]
    return len(set(lengths)) == 1


class CommitSchema(pa.DataFrameModel):
    commit_id: Series[str] = pa.Field(unique=True)
    contributor: Series[str] = pa.Field(nullable=True)
    committed_at: Series[pd.DatetimeTZDtype] = pa.Field(
        dtype_kwargs={"unit": "ns", "tz": "UTC"}
    )

    class Config:
        coerce = True
        strict = True


class PrCommitSchema(pa.DataFrameModel):
    commit_id: Series[str]
    pr_number: Series[int] = pa.Field(ge=1)

    class Config:
        coerce = True
        strict = True


class PullRequestSchema(pa.DataFrameModel):
    pr_number: Series[int] = pa.Field(ge=1, unique=True)
    head_ref: Series[str] = pa.Field(nullable=True)
    base_ref: Series[str] = pa.Field(nullable=True)
    merged: Series[bool]
    merged_into_default: Series[bool]
    state: Series[str] = pa.Field(isin=["OPEN", "MERGED", "CLOSED"])
    updated_at: Series[pd.DatetimeTZDtype] = pa.Field(
        dtype_kwargs={"unit": "ns", "tz": "UTC"}
    )
    comment_count: Series[int] = pa.Field(ge=0)

    class Config:
        coerce = True
        strict = True


class PrIssueLinkSchema(pa.DataFrameModel):
    pr_number: Series[int] = pa.Field(ge=1)
    issue_number: Series[int] = pa.Field(ge=1)

    class Config:
        coerce = True
        strict = True


class IssueSchema(pa.DataFrameModel):
    issue_number: Series[int] = pa.Field(ge=1, unique=True)
    comment_count: Series[int] = pa.Field(ge=0)

    class Config:
        coerce = True
        strict = True


class FinalCommitSchema(pa.DataFrameModel):
    commit_id: Series[str] = pa.Field(unique=True)
    committed_at: Series[pd.DatetimeTZDtype] = pa.Field(
        dtype_kwargs={"unit": "ns", "tz": "UTC"}
    )
    contributor: Series[str] = pa.Field(nullable=True)
    branch: Series[object]
    pr: Series[object]
    pr_state: Series[object]
    number_of_comments_on_pr: Series[object]
    has_pr_been_merged_into_main: Series[bool]
    connected_issue: Series[object]
    number_of_comments_on_connected_issue: Series[object]

    class Config:
        coerce = True
        strict = True

    @pa.check("branch")
    def branch_is_list(cls, series: Series[object]) -> Series[bool]:
        return series.map(_is_list)

    @pa.check("pr")
    def pr_is_list(cls, series: Series[object]) -> Series[bool]:
        return series.map(_is_list)

    @pa.check("pr_state")
    def pr_state_is_list(cls, series: Series[object]) -> Series[bool]:
        return series.map(_is_list)

    @pa.check("number_of_comments_on_pr")
    def pr_comments_is_list(cls, series: Series[object]) -> Series[bool]:
        return series.map(_is_list)

    @pa.check("connected_issue")
    def issues_is_list(cls, series: Series[object]) -> Series[bool]:
        return series.map(_is_list)

    @pa.check("number_of_comments_on_connected_issue")
    def issue_comments_is_list(cls, series: Series[object]) -> Series[bool]:
        return series.map(_is_list)

    @pa.dataframe_check
    def pr_lists_aligned(cls, df: pd.DataFrame) -> Series[bool]:
        return df.apply(
            lambda row: _lists_same_length(
                row["pr"],
                row["pr_state"],
                row["branch"],
                row["number_of_comments_on_pr"],
            ),
            axis=1,
        )

    @pa.dataframe_check
    def issue_lists_aligned(cls, df: pd.DataFrame) -> Series[bool]:
        return df.apply(
            lambda row: _lists_same_length(
                row["connected_issue"],
                row["number_of_comments_on_connected_issue"],
            ),
            axis=1,
        )


FINAL_COLUMNS = [
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


def empty_final_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "commit_id": pd.Series(dtype="string"),
            "committed_at": pd.Series(dtype="datetime64[ns, UTC]"),
            "contributor": pd.Series(dtype="string"),
            "branch": pd.Series(dtype=object),
            "pr": pd.Series(dtype=object),
            "pr_state": pd.Series(dtype=object),
            "number_of_comments_on_pr": pd.Series(dtype=object),
            "has_pr_been_merged_into_main": pd.Series(dtype=bool),
            "connected_issue": pd.Series(dtype=object),
            "number_of_comments_on_connected_issue": pd.Series(dtype=object),
        }
    )
