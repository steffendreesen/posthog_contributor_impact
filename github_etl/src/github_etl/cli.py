from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from github_etl.auth import GitHubAuth
from github_etl.orchestrator import GitHubContributorETL


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PostHog GitHub contributor-impact ETL")
    parser.add_argument(
        "--check-auth",
        action="store_true",
        help="Verify GITHUB_TOKEN / gh auth and print the GitHub login, then exit.",
    )
    parser.add_argument("--since-days", type=int, default=90)
    parser.add_argument("--out-dir", type=Path, default=Path("data"))
    parser.add_argument(
        "--owner",
        default="PostHog",
        help="GitHub owner (default: PostHog)",
    )
    parser.add_argument(
        "--repo",
        default="posthog",
        help="GitHub repository name (default: posthog)",
    )
    parser.add_argument(
        "--skip-components",
        action="store_true",
        help="Write only contributor_commits.parquet, not PR/issue side tables.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    args = _parse_args(argv)
    auth = GitHubAuth.from_env()
    if args.check_auth:
        login = auth.verify_login()
        print(f"Authenticated as {login}")
        return

    etl = GitHubContributorETL(auth=auth, owner=args.owner, repo=args.repo)
    result = etl.run(since_days=args.since_days)
    etl.write(result, args.out_dir, write_components=not args.skip_components)
    out = args.out_dir / "contributor_commits.parquet"
    print(f"Wrote {len(result.commits)} commits to {out}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc
