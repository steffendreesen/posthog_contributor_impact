from __future__ import annotations

import pandas as pd

from github_etl.schemas import FINAL_COLUMNS, FinalCommitSchema, empty_final_frame


def join_commit_table(
    commits: pd.DataFrame,
    pr_commits: pd.DataFrame,
    pull_requests: pd.DataFrame,
    pr_issue_links: pd.DataFrame,
    issues: pd.DataFrame,
) -> pd.DataFrame:
    if commits.empty:
        return FinalCommitSchema.validate(empty_final_frame())

    pr_level = (
        commits.merge(pr_commits, on="commit_id", how="left")
        .merge(pull_requests, on="pr_number", how="left")
        .sort_values(["commit_id", "pr_number"], kind="mergesort")
    )

    pr_agg = (
        pr_level.groupby("commit_id", sort=False)
        .apply(_aggregate_prs, include_groups=False)
        .reset_index()
    )

    issue_level = (
        commits[["commit_id"]]
        .merge(pr_commits, on="commit_id", how="left")
        .merge(pr_issue_links, on="pr_number", how="left")
        .merge(issues, on="issue_number", how="left")
        .dropna(subset=["issue_number"])
        .drop_duplicates(["commit_id", "issue_number"])
        .sort_values(["commit_id", "issue_number"], kind="mergesort")
    )

    if issue_level.empty:
        issue_agg = pd.DataFrame(
            {
                "commit_id": pd.Series(dtype="string"),
                "connected_issue": pd.Series(dtype=object),
                "number_of_comments_on_connected_issue": pd.Series(dtype=object),
            }
        )
    else:
        issue_agg = (
            issue_level.groupby("commit_id", sort=False)
            .apply(_aggregate_issues, include_groups=False)
            .reset_index()
        )

    final = pr_agg.merge(issue_agg, on="commit_id", how="left")
    final["connected_issue"] = final["connected_issue"].apply(
        lambda value: value if isinstance(value, list) else []
    )
    final["number_of_comments_on_connected_issue"] = final[
        "number_of_comments_on_connected_issue"
    ].apply(lambda value: value if isinstance(value, list) else [])
    final["contributor"] = final["contributor"].astype("string")
    final["commit_id"] = final["commit_id"].astype("string")
    final["committed_at"] = pd.to_datetime(final["committed_at"], utc=True)
    return FinalCommitSchema.validate(final[FINAL_COLUMNS])


def _aggregate_prs(group: pd.DataFrame) -> pd.Series:
    with_pr = group.dropna(subset=["pr_number"])
    if with_pr.empty:
        return pd.Series(
            {
                "committed_at": group["committed_at"].iloc[0],
                "contributor": group["contributor"].iloc[0],
                "branch": [],
                "pr": [],
                "pr_state": [],
                "number_of_comments_on_pr": [],
                "has_pr_been_merged_into_main": False,
            }
        )

    with_pr = with_pr.drop_duplicates(["pr_number"]).sort_values(
        "pr_number", kind="mergesort"
    )
    merged_into_default = with_pr["merged_into_default"].fillna(False).astype(bool)
    return pd.Series(
        {
            "committed_at": group["committed_at"].iloc[0],
            "contributor": group["contributor"].iloc[0],
            "branch": _as_list(with_pr["head_ref"]),
            "pr": [int(number) for number in with_pr["pr_number"].tolist()],
            "pr_state": [str(state) for state in with_pr["state"].tolist()],
            "number_of_comments_on_pr": [
                int(count) if pd.notna(count) else 0
                for count in with_pr["comment_count"].tolist()
            ],
            "has_pr_been_merged_into_main": bool(merged_into_default.any()),
        }
    )


def _aggregate_issues(group: pd.DataFrame) -> pd.Series:
    return pd.Series(
        {
            "connected_issue": [int(number) for number in group["issue_number"].tolist()],
            "number_of_comments_on_connected_issue": [
                int(count) if pd.notna(count) else 0
                for count in group["comment_count"].tolist()
            ],
        }
    )


def _as_list(series: pd.Series) -> list[str | None]:
    values: list[str | None] = []
    for value in series.tolist():
        if pd.isna(value):
            values.append(None)
        else:
            values.append(str(value))
    return values
