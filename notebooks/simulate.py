"""Simulate a 90-day PostHog-like commit table matching FinalCommitSchema.

Run from this directory:

    uv run python simulate.py
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from github_etl.schemas import FINAL_COLUMNS, FinalCommitSchema

SEED = 20260818
WINDOW_DAYS = 90
WINDOW_END = datetime(2026, 8, 18, 23, 59, 59, tzinfo=UTC)
WINDOW_START = WINDOW_END - timedelta(days=WINDOW_DAYS - 1)
OUTPUT_PATH = Path(__file__).resolve().parent / "data" / "simulated_commits.parquet"

CONTRIBUTORS = [
    "mariusandra",
    "timgl",
    "pauldambra",
    "benjackwhite",
    "jamesgreenbank",
    "ericduke",
    "neilkilfoyle",
    "harrywaite",
    "lottiehampton",
    "raquelvega",
    "julianmoreno",
    "manoelreis",
    "tiinasalo",
    "michaelmatloka",
    "sandyreid",
    "thomasobrien",
    "annalang",
    "davidchen",
    "karlnovak",
    "yakkowarshaw",
    "zachferrer",
    "leonkim",
    "robhaines",
    "jurajpetrov",
    "frankhazel",
    "ninaiyer",
    "oliverbrandt",
    "samokafor",
]

BRANCH_PREFIXES = ("feat", "fix", "chore", "refactor", "perf", "test")
BRANCH_SLUGS = (
    "funnels",
    "session-replay",
    "feature-flags",
    "ingestion",
    "query-hogql",
    "billing",
    "surveys",
    "web-analytics",
    "error-tracking",
    "cdp",
    "notebooks",
    "auth",
    "experiments",
    "data-warehouse",
    "llm-analytics",
)


@dataclass
class PullRequest:
    number: int
    branch: str
    state: str
    comment_count: int
    merged_into_main: bool
    commit_ids: list[str] = field(default_factory=list)
    issue_numbers: list[int] = field(default_factory=list)


def _sha(index: int) -> str:
    return hashlib.sha1(f"posthog-sim-{index}".encode()).hexdigest()


def _skewed_count(rng: np.random.Generator, mean: float, max_value: int) -> int:
    """Non-negative integer with a long right tail, clipped."""
    value = int(rng.negative_binomial(max(mean / 2.0, 0.5), 0.45))
    return int(min(max(value, 0), max_value))


def simulate(seed: int = SEED) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n_contributors = len(CONTRIBUTORS)
    weights = np.exp(-np.arange(n_contributors) / 7.5)
    weights /= weights.sum()

    days = [WINDOW_START.date() + timedelta(days=offset) for offset in range(WINDOW_DAYS)]

    commits: list[dict] = []
    commit_index = 0
    for day in days:
        weekday = datetime(day.year, day.month, day.day, tzinfo=UTC).weekday()
        expected = 14.0 if weekday >= 5 else 58.0
        n_commits = int(rng.poisson(expected))
        for _ in range(n_commits):
            hour = int(np.clip(rng.normal(15, 4), 0, 23))
            minute = int(rng.integers(0, 60))
            second = int(rng.integers(0, 60))
            committed_at = datetime(
                day.year, day.month, day.day, hour, minute, second, tzinfo=UTC
            )
            if rng.random() < 0.025:
                contributor = None
            else:
                contributor = str(rng.choice(CONTRIBUTORS, p=weights))
            commits.append(
                {
                    "commit_id": _sha(commit_index),
                    "committed_at": committed_at,
                    "contributor": contributor,
                }
            )
            commit_index += 1

    commits.sort(key=lambda row: (row["contributor"] or "", row["committed_at"]))

    prs: dict[int, PullRequest] = {}
    commit_to_prs: dict[str, list[int]] = {row["commit_id"]: [] for row in commits}
    next_pr = 18400
    next_issue = 26100

    by_contributor: dict[str | None, list[dict]] = {}
    for row in commits:
        by_contributor.setdefault(row["contributor"], []).append(row)

    for contributor, rows in by_contributor.items():
        i = 0
        while i < len(rows):
            if rng.random() < 0.045:
                i += 1
                continue

            remaining = len(rows) - i
            size = min(int(rng.integers(1, 9)), remaining)
            pr_number = next_pr
            next_pr += 1

            prefix = BRANCH_PREFIXES[int(rng.integers(0, len(BRANCH_PREFIXES)))]
            slug = BRANCH_SLUGS[int(rng.integers(0, len(BRANCH_SLUGS)))]
            owner = contributor or "bot"
            branch = f"{prefix}/{owner}-{slug}-{pr_number}"

            state = str(rng.choice(["MERGED", "OPEN", "CLOSED"], p=[0.74, 0.16, 0.10]))
            merged_into_main = state == "MERGED" and rng.random() < 0.88
            comment_count = _skewed_count(rng, mean=4.5, max_value=40)

            pr = PullRequest(
                number=pr_number,
                branch=branch,
                state=state,
                comment_count=comment_count,
                merged_into_main=merged_into_main,
            )
            for row in rows[i : i + size]:
                pr.commit_ids.append(row["commit_id"])
                commit_to_prs[row["commit_id"]].append(pr_number)
            prs[pr_number] = pr
            i += size

    issue_comments: dict[int, int] = {}
    for pr in prs.values():
        n_issues = int(rng.choice([0, 1, 2, 3], p=[0.48, 0.38, 0.11, 0.03]))
        for _ in range(n_issues):
            issue_number = next_issue
            next_issue += 1
            issue_comments[issue_number] = _skewed_count(rng, mean=7.5, max_value=60)
            pr.issue_numbers.append(issue_number)

    # A small share of commits also appear on a second PR (cherry-pick / stacked).
    linked_ids = [cid for cid, numbers in commit_to_prs.items() if numbers]
    n_second = max(int(len(linked_ids) * 0.03), 0)
    if n_second and prs:
        extra_ids = rng.choice(linked_ids, size=n_second, replace=False)
        existing_prs = list(prs.values())
        for commit_id in extra_ids:
            donor = existing_prs[int(rng.integers(0, len(existing_prs)))]
            if donor.number in commit_to_prs[commit_id]:
                continue
            commit_to_prs[commit_id].append(donor.number)
            donor.commit_ids.append(str(commit_id))

    records: list[dict] = []
    for row in commits:
        pr_numbers = sorted(set(commit_to_prs[row["commit_id"]]))
        if not pr_numbers:
            records.append(
                {
                    "commit_id": row["commit_id"],
                    "committed_at": row["committed_at"],
                    "contributor": row["contributor"],
                    "branch": [],
                    "pr": [],
                    "pr_state": [],
                    "number_of_comments_on_pr": [],
                    "has_pr_been_merged_into_main": False,
                    "connected_issue": [],
                    "number_of_comments_on_connected_issue": [],
                }
            )
            continue

        linked = [prs[number] for number in pr_numbers]
        issue_numbers: list[int] = []
        for pr in linked:
            for issue_number in pr.issue_numbers:
                if issue_number not in issue_numbers:
                    issue_numbers.append(issue_number)

        records.append(
            {
                "commit_id": row["commit_id"],
                "committed_at": row["committed_at"],
                "contributor": row["contributor"],
                "branch": [pr.branch for pr in linked],
                "pr": [pr.number for pr in linked],
                "pr_state": [pr.state for pr in linked],
                "number_of_comments_on_pr": [pr.comment_count for pr in linked],
                "has_pr_been_merged_into_main": any(pr.merged_into_main for pr in linked),
                "connected_issue": issue_numbers,
                "number_of_comments_on_connected_issue": [
                    issue_comments[number] for number in issue_numbers
                ],
            }
        )

    frame = pd.DataFrame.from_records(records)
    frame["commit_id"] = frame["commit_id"].astype("string")
    frame["contributor"] = frame["contributor"].astype("string")
    frame["committed_at"] = pd.to_datetime(frame["committed_at"], utc=True)
    frame["has_pr_been_merged_into_main"] = frame["has_pr_been_merged_into_main"].astype(bool)
    frame = frame.sort_values("committed_at", kind="mergesort").reset_index(drop=True)
    return FinalCommitSchema.validate(frame[FINAL_COLUMNS])


def main() -> None:
    frame = simulate()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(OUTPUT_PATH, index=False)
    n_prs = frame["pr"].explode().dropna().nunique()
    n_issues = frame["connected_issue"].explode().dropna().nunique()
    n_contributors = frame["contributor"].nunique(dropna=True)
    print(
        f"wrote {OUTPUT_PATH} "
        f"({len(frame)} commits, {n_contributors} contributors, "
        f"{n_prs} PRs, {n_issues} issues, {WINDOW_DAYS} days)"
    )


if __name__ == "__main__":
    main()
