# posthog_contributor_impact

Take-home for the Founding ML Engineer role at Weave: last 90 days of [`PostHog/posthog`](https://github.com/PostHog/posthog) GitHub activity, then EDA and a hierarchical impact model.

| Path | Role |
| --- | --- |
| [`github_etl/`](github_etl/) | Typed GitHub GraphQL extract. Final table on disk: `github_etl/data/contributor_commits.parquet` (gitignored; see [Where the data lives](#where-the-data-lives)) |
| [`notebooks/`](notebooks/) | EDA (swap in the 90-day parquet; still documented against simulated data) |
| [`model/`](model/) | Hierarchical model spec for contributor-level mean commit impact |
| [`app/`](app/) | Static dashboard scaffold |

Tradeoffs that define what this table *is* and *is not* are in [Compromises](#compromises) at the end.

---

## `github_etl` — grain and schema

One row per **commit SHA on the default branch** (`master`) whose committer timestamp falls in the window (`--since-days`, default 90). PostHog squash-merges through a merge queue, so these SHAs are the landed squash commits, not the PR-head SHAs.

| Column | Meaning |
| --- | --- |
| `commit_id` | SHA |
| `committed_at` | Committer datetime, UTC |
| `contributor` | GitHub login, else author name |
| `branch` | List of linked PR head refs (aligned with `pr`) |
| `pr` | List of linked PR numbers |
| `pr_state` | List of `OPEN` / `MERGED` / `CLOSED` |
| `number_of_comments_on_pr` | List of PR comment counts |
| `has_pr_been_merged_into_main` | True if **any** linked PR merged into **default branch `master`** |
| `connected_issue` | List of issues linked from those PRs (`closingIssuesReferences`) |
| `number_of_comments_on_connected_issue` | List of issue comment counts, aligned with `connected_issue` |

For EDA on PR mix, explode `pr` + `pr_state` and **dedupe by PR number**.

---

## How to run

```bash
cd github_etl
cp .env.example .env   # then set GITHUB_TOKEN, or rely on `gh auth token`
uv sync
uv run github-etl --check-auth
uv run github-etl --since-days 2 --out-dir data/smoke_2d_squash   # smoke
uv run github-etl                                                 # 90 days → data/contributor_commits.parquet
```

Auth lookup order: `GITHUB_TOKEN` / `GH_TOKEN` in `github_etl/.env`, then `gh auth token`. `.env` is gitignored; do not commit tokens.

Writes `contributor_commits.parquet`, plus `pull_requests.parquet` and `issues.parquet` unless `--skip-components`. Tests (`uv run pytest`) never call GitHub.

---

## Where the data lives

The extract is **not in git**. `github_etl/.gitignore` excludes `data/` and `.env`. After you run the ETL from `github_etl/`, files appear on disk here:

| File | What it is |
| --- | --- |
| **`github_etl/data/contributor_commits.parquet`** | **Final 90-day table** (default `--out-dir data`). This is the dataset for EDA and the model. 13,202 rows from the 2026-05-20 → 2026-08-18 run. |
| `github_etl/data/pull_requests.parquet` | PR side table (state, comments, head/base, merge flags) |
| `github_etl/data/issues.parquet` | Issue side table (number, comment count) |

`--out-dir` is relative to the working directory. Commands above assume `cd github_etl`, so `--out-dir data` is `github_etl/data/` from the repo root.

Smoke / earlier attempts (also gitignored, not the final table):

| Path | Run |
| --- | --- |
| `github_etl/data/smoke_2d/` | Attempt 1: PRs updated in 2 days |
| `github_etl/data/smoke_2d_master/` | Attempt 3: master history + per-SHA GraphQL |
| `github_etl/data/smoke_2d_squash/` | Attempt 4: 2-day squash-title smoke |

To rebuild the final table: `cd github_etl && uv run github-etl`.

---

## Design log

The assignment budget is **under 10 minutes** for 90 days. This is how the extract changed to meet that. Each attempt's *why we left it* is restated in [Compromises](#compromises).

### Attempt 1 — PRs `updatedAt` DESC, then every commit on those PRs

Grain was “commits on PRs updated in the last N days” (open / merged / closed). Old PRs re-entered the window via comments or CI; many had huge leftover commit lists.

2-day smoke: **~10 minutes**, 7,980 commits, 2,285 PRs, 92 pages. 90 days would have been hours. Output: `github_etl/data/smoke_2d/`.

### Attempt 2 — Nested `history` + `associatedPullRequests` + issues

Default-branch history with PR and issue fields in the same query. Timed out at page sizes 50 and 25; killed.

### Attempt 3 — Slim history + batched SHA → PR GraphQL + batched PR details

History of oid / date / author only, then `associatedPullRequests` per SHA, then PR details.

2-day smoke **succeeded in 158s** (2026-08-16 20:24 UTC → 2026-08-18 19:44 UTC):

| | |
| --- | --- |
| Commits | 309 |
| Unique PRs | 306 (all `MERGED`) |
| Linked issues | 9 |
| Contributors | 78 |
| Commits with a PR | 306 / 309 |
| `has_pr_been_merged_into_main` | 301 |

Where the time went:

1. History: **~3.5s** for 309 commits at 100/page (fine).
2. SHA → PR: **~2.3 min**. Batches of 20 timed out; 10 SHAs × ~31 calls was the bottleneck.
3. PR details: **~7s** for 306 PRs at 20/batch (fine).

90-day extrapolation at 10 SHAs/query: **~1 hour**, not under 10 minutes. Output: `github_etl/data/smoke_2d_master/`.

### Attempt 4 — Parse squash titles `(#12345)`, GraphQL only as fallback (current)

PostHog squash-merge subjects look like `feat(web): … (#84094)`. History now fetches the full `message` and takes PR numbers from the **first line** with `\(#(\d+)\)`. Commits without a title PR still use batched `associatedPullRequests`. Then the same PR-details batch as attempt 3.

**Why not `messageHeadline`?** GitHub truncates that field with an ellipsis. On a 100-commit sample, 46 headlines differed from the first line of `message`, and 48/100 missed the PR because `(#84094)` was cut off (`…`). Using the full first line, only 2/100 missed (release commits with no PR). Issue numbers in the commit *body* are ignored.

2-day smoke after that fix: **~16s**, 306/309 titles linked, GraphQL fallback for 3 SHAs.

| Step | 2-day |
| --- | --- |
| History (4 pages, 309 commits) | ~4.5s |
| Title parse + 3-SHA GraphQL fallback | ~1.3s |
| PR details (306 PRs) | ~8s |
| **Total** | **~16s** |

90-day extract **succeeded in 7.8 minutes** (466s), 2026-05-20 → 2026-08-18:

| | |
| --- | --- |
| Commits | 13,202 |
| Unique PRs | 13,169 |
| Linked issues | 241 |
| Contributors | 210 |
| Title-linked | 13,166 / 13,202 (GraphQL fallback for 36) |
| `has_pr_been_merged_into_main` | 13,134 |

History ~2.2 min; PR details ~5.3 min. Output: `github_etl/data/contributor_commits.parquet`.

Join remains: `commits` ⟕ `pr_commits` ⟕ `pull_requests` ⟕ issues, grouped by `commit_id` into the list columns above.

---

## Compromises

These are the choices that made a 90-day extract possible in under 10 minutes, and the coverage we gave up for each. They are **dataset definition**, not implementation footnotes. Ranking “highest-impact engineers” on this table is a ranking on *landed default-branch work with a recoverable PR*, not on all GitHub activity.

### 1. Universe is `master` history, not “every commit in 90 days”

**What we kept.** Commits whose SHA currently sits on the default branch (`master`) and whose committer time is in the window. One row per landed squash SHA.

**What we dropped.** Three other universes were considered:

| Universe | Why not |
| --- | --- |
| All PRs *updated* in 90 days (open / merged / closed), then every commit on those PRs | Attempt 1. A comment or CI run on an old PR pulls its entire commit list into the window. 2-day smoke: 7,980 commits in ~10 min; 90 days would be hours. |
| Nested GraphQL: history + `associatedPullRequests` + issues in one query | Attempt 2. Timed out at page sizes 50 and 25. |
| Every commit on every ref (open branches, closed branches, no PR yet) | GitHub has no “all commits since date” endpoint. The repo had ~13,828 branch refs. Walking them is a different extract. Deleted branches drop out of `refs/heads/*` unless a PR kept `refs/pull/N/head`. |

**Failure mode.** Open, unmerged, or abandoned work does not appear. Direct pushes without a PR appear as commits with empty `pr` lists. A contributor who reviews, comments, or iterates on feature branches but lands little on `master` looks small.

### 2. PR numbers come from squash titles, not per-SHA GraphQL

**What we kept.** Parse `\(#(\d+)\)` on the **first line** of the full commit `message`. Only SHAs with no title PR call `associatedPullRequests` (36 of 13,202 on the 90-day run).

**Why.** Per-SHA GraphQL was the Attempt 3 bottleneck (~2.3 min for 309 commits; ~1 hour projected for 90 days). PostHog squash-merges, so almost every landed subject is `… (#12345)`.

**Further constraint: use `message`, not `messageHeadline`.** GitHub truncates `messageHeadline` with `…`, which often cuts off the PR suffix. On a 100-commit sample, 48/100 missed under the headline field and 2/100 under the full first line. Issue/`#` references in the commit *body* are ignored so a `Closes #99` footer does not become a PR number.

**Failure mode.** A landed commit whose first line has no `(#N)` and whose `associatedPullRequests` is empty stays unlinked. A non-PR `(#digits)` in the subject would be treated as a PR (not observed on PostHog’s squash convention). Coverage on the 90-day extract: 13,166 title links + 36 GraphQL fallbacks; 27 rows still have an empty `pr` list.

### 3. `main` in the schema means default branch `master`

**What we kept.** Column name `has_pr_been_merged_into_main` as specified. Value is true iff any linked PR has `merged` and `baseRefName == defaultBranchRef` (`master` here).

**What we dropped.** Interpreting the column as “this SHA is reachable from a branch named `main`.” PostHog’s default branch is `master`. Squash + merge-queue means the PR-head SHA usually does **not** survive onto `master`; the row SHA *is* the landed squash.

**Failure mode.** Treating `False` as “not on trunk” is wrong for a missing PR link. On the 90-day extract, 13,134 / 13,202 are true.

### 4. Line counts are omitted

**What we dropped.** `additions` / `deletions` (and any size-based impact proxy from them).

**Why.** GraphQL patch stats timed out even on small history pages. Adding them back would re-introduce Attempt 2-style timeouts.

**Failure mode.** Impact cannot be “lines changed” on this table. Comment counts and merge-to-default remain the size-like signals.

### 5. Issues are only those that close from a linked PR

**What we kept.** `closingIssuesReferences` on PRs we already fetched for comment/state/base-ref.

**What we dropped.** Issues updated in the window with no linked landed PR; issue events that are not GitHub “closing” references; commits that mention an issue only in the body.

**Failure mode.** Product-work tracked in issues but landed without a closing link looks like “no connected issue.” The 90-day table has 241 unique issues vs 13,169 PRs.

### 6. PRs and issues are lists on the commit row

**What we kept.** One commit can sit on more than one PR; one PR can close more than one issue. Aligned list columns preserve that without duplicating `commit_id`.

**Cost.** Commit-level EDA that `sum`s comment counts will double-count a PR shared by two SHAs. For PR-status mix, explode `pr` + `pr_state` and **dedupe by PR number**.

### 7. Runtime budget over completeness of GitHub objects

**What we kept.** Sequential batched PR-detail queries (state, comments, head/base, closing issues) after the cheap history+title pass. 90 days: ~2.2 min history, ~5.3 min PR details, **7.8 min** total.

**What we dropped.** Nested PR fields on history (timeout), per-SHA PR lookup as the default path (too slow), walking all refs, line stats, and any extra GraphQL objects (reviews, requested reviewers, files, check runs).

**Failure mode.** The table is a typed, joinable commit spine for EDA and a hierarchical model. It is not a full GitHub warehouse. New fields need a cost check against the 10-minute cap.
