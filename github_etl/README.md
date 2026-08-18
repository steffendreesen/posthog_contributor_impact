# github_etl

Extract of `PostHog/posthog` default-branch commits for the contributor-impact take-home.

How to run, schema, compromises, and the design log live in the [repo README](../README.md).

**Final table (local, gitignored):** `data/contributor_commits.parquet` after `uv run github-etl`, i.e. `github_etl/data/contributor_commits.parquet` from the repo root. Side tables: `data/pull_requests.parquet`, `data/issues.parquet`. `.env` and `data/` are gitignored.

```bash
uv run github-etl --check-auth
uv run github-etl --since-days 2 --out-dir data/smoke_2d_squash
uv run github-etl
uv run pytest
```
