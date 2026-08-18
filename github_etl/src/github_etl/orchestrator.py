from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from github_etl.auth import GitHubAuth
from github_etl.client import GraphQLClient
from github_etl.extract import GitHubExtractor
from github_etl.transform import join_commit_table

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ETLResult:
    commits: pd.DataFrame
    pull_requests: pd.DataFrame
    issues: pd.DataFrame
    pr_commits: pd.DataFrame
    pr_issue_links: pd.DataFrame
    default_branch: str


class GitHubContributorETL:
    def __init__(
        self,
        auth: GitHubAuth,
        owner: str = "PostHog",
        repo: str = "posthog",
    ) -> None:
        self.auth = auth
        self.owner = owner
        self.repo = repo
        self.client = GraphQLClient(auth)
        self.extractor = GitHubExtractor(self.client, owner, repo)

    def run(self, since_days: int = 90) -> ETLResult:
        since = datetime.now(timezone.utc) - timedelta(days=since_days)
        logger.info(
            "Extracting %s/%s default-branch commits since %s",
            self.owner,
            self.repo,
            since.isoformat(),
        )
        components = self.extractor.extract(since)
        commits = join_commit_table(
            components.commits,
            components.pr_commits,
            components.pull_requests,
            components.pr_issue_links,
            components.issues,
        )
        logger.info(
            "Built %s commits, %s PRs, %s issues",
            len(commits),
            len(components.pull_requests),
            len(components.issues),
        )
        return ETLResult(
            commits=commits,
            pull_requests=components.pull_requests,
            issues=components.issues,
            pr_commits=components.pr_commits,
            pr_issue_links=components.pr_issue_links,
            default_branch=components.default_branch,
        )

    def write(self, result: ETLResult, out_dir: Path, write_components: bool = True) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        result.commits.to_parquet(out_dir / "contributor_commits.parquet", index=False)
        if write_components:
            result.pull_requests.to_parquet(out_dir / "pull_requests.parquet", index=False)
            result.issues.to_parquet(out_dir / "issues.parquet", index=False)
