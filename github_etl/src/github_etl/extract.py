from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from github_etl.client import GraphQLClient, QueryTimeoutError
from github_etl.schemas import (
    CommitSchema,
    IssueSchema,
    PrCommitSchema,
    PrIssueLinkSchema,
    PullRequestSchema,
)

logger = logging.getLogger(__name__)

HISTORY_QUERY = """
query($owner: String!, $name: String!, $since: GitTimestamp!, $cursor: String, $pageSize: Int!) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef {
      name
      target {
        ... on Commit {
          history(first: $pageSize, after: $cursor, since: $since) {
            pageInfo { hasNextPage endCursor }
            nodes {
              oid
              committedDate
              message
              author { name user { login } }
            }
          }
        }
      }
    }
  }
}
"""

COMMIT_PRS_FIELDS = """
oid
associatedPullRequests(first: 5) {
  nodes { number }
}
"""

PR_DETAIL_FIELDS = """
number
headRefName
baseRefName
merged
state
updatedAt
totalCommentsCount
closingIssuesReferences(first: 10) {
  nodes {
    number
    comments(first: 1) { totalCount }
  }
}
"""

PAGE_SIZES = (100, 50, 25)
SHA_BATCH_SIZES = (20, 10, 5)
PR_BATCH_SIZES = (20, 10, 5)
SQUASH_PR_RE = re.compile(r"\(#(\d+)\)")


@dataclass
class ComponentTables:
    commits: pd.DataFrame
    pr_commits: pd.DataFrame
    pull_requests: pd.DataFrame
    pr_issue_links: pd.DataFrame
    issues: pd.DataFrame
    default_branch: str


@dataclass
class _Accumulators:
    commits: list[dict[str, Any]] = field(default_factory=list)
    pr_commits: list[dict[str, Any]] = field(default_factory=list)
    pull_requests: list[dict[str, Any]] = field(default_factory=list)
    pr_issue_links: list[dict[str, Any]] = field(default_factory=list)
    issues: dict[int, int] = field(default_factory=dict)
    seen_commits: set[str] = field(default_factory=set)


class GitHubExtractor:
    def __init__(self, client: GraphQLClient, owner: str, repo: str) -> None:
        self._client = client
        self.owner = owner
        self.repo = repo

    def extract(self, since: datetime) -> ComponentTables:
        since_utc = _as_utc(since)
        acc = _Accumulators()
        default_branch: str | None = None
        cursor: str | None = None
        page_size = PAGE_SIZES[0]
        page_idx = 0

        while True:
            data, page_size = self._fetch_history_page(cursor, page_size, since_utc)
            ref = (data.get("repository") or {}).get("defaultBranchRef") or {}
            if default_branch is None:
                default_branch = ref.get("name") or "master"
            history = ((ref.get("target") or {}).get("history")) or {}
            nodes = [node for node in (history.get("nodes") or []) if node]
            if not nodes:
                break

            page_idx += 1
            logger.info(
                "History page %s (%s commits, %s .. %s)",
                page_idx,
                len(nodes),
                nodes[0].get("committedDate"),
                nodes[-1].get("committedDate"),
            )
            for node in nodes:
                self._ingest_commit_node(node, acc)

            page_info = history.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")
            if not cursor:
                break

        if default_branch is None:
            default_branch = "master"
        linked = {row["commit_id"] for row in acc.pr_commits}
        missing = [row["commit_id"] for row in acc.commits if row["commit_id"] not in linked]
        logger.info(
            "Linked %s/%s commits to PRs from squash titles; GraphQL fallback for %s",
            len(linked),
            len(acc.commits),
            len(missing),
        )
        self._hydrate_commit_pull_request_numbers(acc, missing)
        self._hydrate_pull_requests(acc, default_branch)
        return _to_component_tables(acc, default_branch)

    def _fetch_history_page(
        self,
        cursor: str | None,
        page_size: int,
        since: pd.Timestamp,
    ) -> tuple[dict[str, Any], int]:
        sizes = [size for size in PAGE_SIZES if size <= page_size]
        last_error: Exception | None = None
        for size in sizes:
            try:
                data = self._client.execute(
                    HISTORY_QUERY,
                    {
                        "owner": self.owner,
                        "name": self.repo,
                        "since": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "cursor": cursor,
                        "pageSize": size,
                    },
                )
                return data, size
            except QueryTimeoutError as exc:
                last_error = exc
                logger.warning("History page timed out at pageSize=%s; shrinking", size)
        raise QueryTimeoutError(
            f"History page still timing out at pageSize={sizes[-1]}"
        ) from last_error

    def _ingest_commit_node(self, node: dict[str, Any], acc: _Accumulators) -> None:
        parsed = parse_history_commit_node(node)
        if parsed.commit_row["commit_id"] in acc.seen_commits:
            return
        acc.seen_commits.add(parsed.commit_row["commit_id"])
        acc.commits.append(parsed.commit_row)
        acc.pr_commits.extend(parsed.pr_commit_rows)

    def _hydrate_commit_pull_request_numbers(
        self,
        acc: _Accumulators,
        shas: list[str],
    ) -> None:
        if not shas:
            return
        logger.info("Fetching associated PRs for %s commits without a title PR", len(shas))
        batch_size = SHA_BATCH_SIZES[0]
        idx = 0
        batch_idx = 0
        while idx < len(shas):
            remaining = shas[idx:]
            data, batch_size = self._fetch_commit_pr_numbers(remaining, batch_size)
            chunk = remaining[:batch_size]
            batch_idx += 1
            if batch_idx == 1 or batch_idx % 10 == 0:
                logger.info("Commit-PR batch %s (%s SHAs)", batch_idx, len(chunk))
            for alias_i, sha in enumerate(chunk):
                obj = (data.get(f"c{alias_i}") or {}).get("object") or {}
                for pr in (obj.get("associatedPullRequests") or {}).get("nodes") or []:
                    if not pr or pr.get("number") is None:
                        continue
                    acc.pr_commits.append(
                        {"commit_id": sha, "pr_number": int(pr["number"])}
                    )
            idx += len(chunk)

    def _fetch_commit_pr_numbers(
        self,
        remaining: list[str],
        batch_size: int,
    ) -> tuple[dict[str, Any], int]:
        sizes = [size for size in SHA_BATCH_SIZES if size <= batch_size]
        last_error: Exception | None = None
        for size in sizes:
            chunk = remaining[:size]
            try:
                data = self._client.execute(
                    build_commit_prs_query(len(chunk)),
                    {
                        "owner": self.owner,
                        "name": self.repo,
                        **{f"s{i}": sha for i, sha in enumerate(chunk)},
                    },
                )
                return data, len(chunk)
            except QueryTimeoutError as exc:
                last_error = exc
                logger.warning("Commit-PR lookup timed out at batchSize=%s; shrinking", size)
        raise QueryTimeoutError(
            f"Commit-PR lookup still timing out at batchSize={sizes[-1]}"
        ) from last_error

    def _hydrate_pull_requests(self, acc: _Accumulators, default_branch: str) -> None:
        numbers = sorted({int(row["pr_number"]) for row in acc.pr_commits})
        if not numbers:
            return
        logger.info("Fetching details for %s unique PRs", len(numbers))
        batch_size = PR_BATCH_SIZES[0]
        idx = 0
        batch_idx = 0
        while idx < len(numbers):
            remaining = numbers[idx:]
            data, batch_size = self._fetch_pr_details(remaining, batch_size)
            chunk = remaining[:batch_size]
            batch_idx += 1
            logger.info("PR details batch %s (%s PRs)", batch_idx, len(chunk))
            for alias_i, _number in enumerate(chunk):
                repo = data.get(f"p{alias_i}") or {}
                pr = repo.get("pullRequest")
                if not pr:
                    continue
                pr_row, links, issues = parse_pull_request_node(pr, default_branch)
                acc.pull_requests.append(pr_row)
                acc.pr_issue_links.extend(links)
                for issue_number, comment_count in issues:
                    acc.issues[issue_number] = comment_count
            idx += len(chunk)

    def _fetch_pr_details(
        self,
        remaining: list[int],
        batch_size: int,
    ) -> tuple[dict[str, Any], int]:
        sizes = [size for size in PR_BATCH_SIZES if size <= batch_size]
        last_error: Exception | None = None
        for size in sizes:
            chunk = remaining[:size]
            try:
                data = self._client.execute(
                    build_pr_details_query(len(chunk)),
                    {
                        "owner": self.owner,
                        "name": self.repo,
                        **{f"n{i}": number for i, number in enumerate(chunk)},
                    },
                )
                return data, len(chunk)
            except QueryTimeoutError as exc:
                last_error = exc
                logger.warning("PR details timed out at batchSize=%s; shrinking", size)
        raise QueryTimeoutError(
            f"PR details still timing out at batchSize={sizes[-1]}"
        ) from last_error


@dataclass
class ParsedHistoryCommit:
    commit_row: dict[str, Any]
    pr_commit_rows: list[dict[str, Any]]


def parse_pr_numbers_from_message(message: str | None) -> list[int]:
    if not message:
        return []
    first_line = message.split("\n", 1)[0]
    return [int(match) for match in SQUASH_PR_RE.findall(first_line)]


def parse_history_commit_node(node: dict[str, Any]) -> ParsedHistoryCommit:
    commit_row = parse_commit_node(node)
    if commit_row is None:
        raise ValueError("history node missing oid")
    # Prefer `message` over `messageHeadline`: GitHub truncates headlines with
    # an ellipsis, which often cuts off the squash-merge "(#12345)" suffix.
    message = node.get("message") or node.get("messageHeadline")
    pr_commit_rows = [
        {"commit_id": commit_row["commit_id"], "pr_number": number}
        for number in parse_pr_numbers_from_message(message)
    ]
    return ParsedHistoryCommit(commit_row=commit_row, pr_commit_rows=pr_commit_rows)


def parse_commit_node(commit: dict[str, Any] | None) -> dict[str, Any] | None:
    if not commit or not commit.get("oid"):
        return None
    return {
        "commit_id": str(commit["oid"]),
        "contributor": _contributor(commit.get("author")),
        "committed_at": _parse_dt(commit.get("committedDate")),
    }


def parse_pull_request_node(
    node: dict[str, Any],
    default_branch: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[tuple[int, int]]]:
    pr_number = int(node["number"])
    base_ref = node.get("baseRefName")
    merged = bool(node.get("merged"))
    pr_row = {
        "pr_number": pr_number,
        "head_ref": node.get("headRefName"),
        "base_ref": base_ref,
        "merged": merged,
        "merged_into_default": merged and base_ref == default_branch,
        "state": str(node.get("state") or "OPEN"),
        "updated_at": _parse_dt(node.get("updatedAt")),
        "comment_count": int(node.get("totalCommentsCount") or 0),
    }
    link_rows: list[dict[str, Any]] = []
    issue_rows: list[tuple[int, int]] = []
    for issue in (node.get("closingIssuesReferences") or {}).get("nodes") or []:
        if not issue or issue.get("number") is None:
            continue
        issue_number = int(issue["number"])
        comments = issue.get("comments") or {}
        comment_count = int(comments.get("totalCount") or 0)
        link_rows.append({"pr_number": pr_number, "issue_number": issue_number})
        issue_rows.append((issue_number, comment_count))
    return pr_row, link_rows, issue_rows


def build_commit_prs_query(n: int) -> str:
    variables = ", ".join(
        ["$owner: String!", "$name: String!"] + [f"$s{i}: String!" for i in range(n)]
    )
    fields = "\n".join(
        f"c{i}: repository(owner: $owner, name: $name) {{ object(expression: $s{i}) {{ ... on Commit {{ {COMMIT_PRS_FIELDS} }} }} }}"
        for i in range(n)
    )
    return f"query({variables}) {{\n{fields}\n}}"


def build_pr_details_query(n: int) -> str:
    variables = ", ".join(
        ["$owner: String!", "$name: String!"] + [f"$n{i}: Int!" for i in range(n)]
    )
    fields = "\n".join(
        f"p{i}: repository(owner: $owner, name: $name) {{ pullRequest(number: $n{i}) {{ {PR_DETAIL_FIELDS} }} }}"
        for i in range(n)
    )
    return f"query({variables}) {{\n{fields}\n}}"


def _contributor(author: dict[str, Any] | None) -> str | None:
    if not author:
        return None
    user = author.get("user") or {}
    login = user.get("login") if isinstance(user, dict) else None
    if login:
        return str(login)
    name = author.get("name")
    return str(name) if name else None


def _parse_dt(value: object) -> pd.Timestamp | None:
    if value is None:
        return None
    return pd.to_datetime(value, utc=True)


def _as_utc(value: datetime) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _empty_commits() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "commit_id": pd.Series(dtype="string"),
            "contributor": pd.Series(dtype="string"),
            "committed_at": pd.Series(dtype="datetime64[ns, UTC]"),
        }
    )


def _to_component_tables(acc: _Accumulators, default_branch: str) -> ComponentTables:
    commits = pd.DataFrame(
        acc.commits,
        columns=["commit_id", "contributor", "committed_at"],
    )
    pr_commits = pd.DataFrame(acc.pr_commits, columns=["commit_id", "pr_number"])
    pull_requests = pd.DataFrame(
        acc.pull_requests,
        columns=[
            "pr_number",
            "head_ref",
            "base_ref",
            "merged",
            "merged_into_default",
            "state",
            "updated_at",
            "comment_count",
        ],
    )
    pr_issue_links = pd.DataFrame(
        acc.pr_issue_links,
        columns=["pr_number", "issue_number"],
    )
    issues = pd.DataFrame(
        [
            {"issue_number": number, "comment_count": count}
            for number, count in sorted(acc.issues.items())
        ],
        columns=["issue_number", "comment_count"],
    )

    if commits.empty:
        commits = _empty_commits()
    else:
        commits["committed_at"] = pd.to_datetime(commits["committed_at"], utc=True)
        commits["contributor"] = commits["contributor"].astype("string")
        commits["commit_id"] = commits["commit_id"].astype("string")

    if pr_commits.empty:
        pr_commits["commit_id"] = pd.Series(dtype="string")
        pr_commits["pr_number"] = pd.Series(dtype="int64")
    else:
        pr_commits = pr_commits.drop_duplicates(["commit_id", "pr_number"])
        pr_commits["commit_id"] = pr_commits["commit_id"].astype("string")

    if not pull_requests.empty:
        pull_requests["updated_at"] = pd.to_datetime(pull_requests["updated_at"], utc=True)
        pull_requests["head_ref"] = pull_requests["head_ref"].astype("string")
        pull_requests["base_ref"] = pull_requests["base_ref"].astype("string")
        pull_requests["state"] = pull_requests["state"].astype("string")
    else:
        pull_requests["updated_at"] = pd.Series(dtype="datetime64[ns, UTC]")
        pull_requests["head_ref"] = pd.Series(dtype="string")
        pull_requests["base_ref"] = pd.Series(dtype="string")
        pull_requests["state"] = pd.Series(dtype="string")
        pull_requests["pr_number"] = pd.Series(dtype="int64")
        pull_requests["comment_count"] = pd.Series(dtype="int64")
        pull_requests["merged"] = pd.Series(dtype=bool)
        pull_requests["merged_into_default"] = pd.Series(dtype=bool)

    if pr_issue_links.empty:
        pr_issue_links["pr_number"] = pd.Series(dtype="int64")
        pr_issue_links["issue_number"] = pd.Series(dtype="int64")
    else:
        pr_issue_links = pr_issue_links.drop_duplicates(["pr_number", "issue_number"])

    if issues.empty:
        issues["issue_number"] = pd.Series(dtype="int64")
        issues["comment_count"] = pd.Series(dtype="int64")

    return ComponentTables(
        commits=CommitSchema.validate(commits),
        pr_commits=PrCommitSchema.validate(pr_commits),
        pull_requests=PullRequestSchema.validate(pull_requests),
        pr_issue_links=PrIssueLinkSchema.validate(pr_issue_links),
        issues=IssueSchema.validate(issues),
        default_branch=default_branch,
    )
