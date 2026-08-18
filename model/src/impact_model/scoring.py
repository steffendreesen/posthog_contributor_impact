"""Per-commit impact score.

Implements the heuristic specified in `model/README.md` section 10: an additive
point score where each component is scaled to [0, 1] and then multiplied by a
weight equal to the maximum points that component can contribute.
"""

from __future__ import annotations

import pandas as pd
import pandera.pandas as pa
from github_etl.schemas import FINAL_COLUMNS, FinalCommitSchema
from pandera.typing import Series

WEIGHT_BASE = 1.0
WEIGHT_LANDING = 3.0
WEIGHT_REVIEW = 2.0
WEIGHT_ATTACH = 1.5
WEIGHT_COMPLEXITY = 2.5
MAX_IMPACT = (
    WEIGHT_BASE + WEIGHT_LANDING + WEIGHT_REVIEW + WEIGHT_ATTACH + WEIGHT_COMPLEXITY
)

# Half-saturation points: at c == k a count component earns half its weight.
K_PR = 4.0
K_ISSUE = 6.0

LANDING_LADDER = {"MERGED": 0.5, "OPEN": 0.25, "CLOSED": 0.0}

SCORE_COLUMNS = [
    "landing",
    "c_pr",
    "c_iss",
    "attached",
    "review",
    "complexity",
    "landing_points",
    "review_points",
    "attach_points",
    "complexity_points",
    "impact",
]

SCORED_COLUMNS = [*FINAL_COLUMNS, *SCORE_COLUMNS]

# Contributor-level means of the three score terms shown on the dashboard.
# Base is constant (1.0); issue complexity is omitted (near-zero on this extract,
# collinear with attachment). Weights are the per-commit ceilings.
DISPLAY_COMPONENT_WEIGHTS = {
    "landing": WEIGHT_LANDING,
    "review": WEIGHT_REVIEW,
    "attach": WEIGHT_ATTACH,
}

COMPONENT_MEAN_COLUMNS = [
    "contributor",
    "n_commits",
    "landing_points",
    "review_points",
    "attach_points",
]

LAST_DAY_ENGINEER_COLUMNS = [
    "contributor",
    "n_commits",
    "mean_impact",
    "theta_mean",
    "theta_ci_5",
    "theta_ci_95",
    "landing_points",
    "review_points",
    "attach_points",
]


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    # Parquet round-trips absent PR states as the string "nan".
    return isinstance(value, str) and value.lower() == "nan"


def landing_level(merged_into_main: bool, states: list) -> float:
    """Landing ladder L in [0, 1]. With several PRs, take the best rung."""
    if bool(merged_into_main):
        return 1.0
    best = 0.0
    for state in states:
        if _is_missing(state):
            continue
        best = max(best, LANDING_LADDER.get(str(state), 0.0))
    return best


def max_count(values: list) -> float:
    numbers = [float(value) for value in values if not _is_missing(value)]
    return max(numbers) if numbers else 0.0


def sum_count(values: list) -> float:
    return float(sum(float(value) for value in values if not _is_missing(value)))


def saturate(count: float, half_saturation: float) -> float:
    return count / (count + half_saturation)


class ScoredCommitSchema(FinalCommitSchema):
    """`FinalCommitSchema` plus the section 10 score columns.

    Subclassing keeps the ETL columns under the same checks the pipeline writes
    against, so the two schemas cannot drift apart.
    """

    landing: Series[float] = pa.Field(ge=0.0, le=1.0)
    c_pr: Series[float] = pa.Field(ge=0.0)
    c_iss: Series[float] = pa.Field(ge=0.0)
    attached: Series[float] = pa.Field(ge=0.0, le=1.0)
    review: Series[float] = pa.Field(ge=0.0, le=1.0)
    complexity: Series[float] = pa.Field(ge=0.0, le=1.0)
    landing_points: Series[float] = pa.Field(ge=0.0, le=WEIGHT_LANDING)
    review_points: Series[float] = pa.Field(ge=0.0, le=WEIGHT_REVIEW)
    attach_points: Series[float] = pa.Field(ge=0.0, le=WEIGHT_ATTACH)
    complexity_points: Series[float] = pa.Field(ge=0.0, le=WEIGHT_COMPLEXITY)
    impact: Series[float] = pa.Field(ge=WEIGHT_BASE, le=MAX_IMPACT)

    class Config:
        coerce = True
        strict = True


def score_commits(commits: pd.DataFrame, validate: bool = True) -> pd.DataFrame:
    """Add impact score columns to a `FinalCommitSchema` frame, one row per commit."""
    scored = commits.copy()

    scored["landing"] = [
        landing_level(merged, states)
        for merged, states in zip(
            scored["has_pr_been_merged_into_main"], scored["pr_state"], strict=True
        )
    ]
    scored["c_pr"] = scored["number_of_comments_on_pr"].map(max_count)
    scored["c_iss"] = scored["number_of_comments_on_connected_issue"].map(sum_count)
    scored["attached"] = scored["connected_issue"].map(
        lambda values: 1.0 if len(values) else 0.0
    )
    scored["review"] = scored["c_pr"].map(lambda count: saturate(count, K_PR))
    scored["complexity"] = scored["c_iss"].map(lambda count: saturate(count, K_ISSUE))

    scored["landing_points"] = WEIGHT_LANDING * scored["landing"]
    scored["review_points"] = WEIGHT_REVIEW * scored["review"]
    scored["attach_points"] = WEIGHT_ATTACH * scored["attached"]
    scored["complexity_points"] = WEIGHT_COMPLEXITY * scored["complexity"]
    scored["impact"] = (
        WEIGHT_BASE
        + scored["landing_points"]
        + scored["review_points"]
        + scored["attach_points"]
        + scored["complexity_points"]
    )

    scored = scored[SCORED_COLUMNS]
    return ScoredCommitSchema.validate(scored) if validate else scored


class ContributorComponentSchema(pa.DataFrameModel):
    """Full-window mean of the three display components, one row per contributor.

    Points are averages of the per-commit `*_points` columns, so each lies in
    `[0, weight]`. `n_commits` is the number of attributed commits in the
    scored table (same grain as last-day θ).
    """

    contributor: Series[str]
    n_commits: Series[int] = pa.Field(ge=1)
    landing_points: Series[float] = pa.Field(ge=0.0, le=WEIGHT_LANDING)
    review_points: Series[float] = pa.Field(ge=0.0, le=WEIGHT_REVIEW)
    attach_points: Series[float] = pa.Field(ge=0.0, le=WEIGHT_ATTACH)

    class Config:
        coerce = True
        strict = True
        unique = ["contributor"]


class LastDayEngineerSchema(pa.DataFrameModel):
    """Last expanding-window day: posterior θ plus score-component means.

    `theta_ci_5` / `theta_ci_95` are the 5th and 95th posterior percentiles
    (a 90% central credible interval). Component columns decompose the raw
    mean `mean_impact` (plus the constant base 1.0 and omitted complexity),
    not θ.
    """

    contributor: Series[str]
    n_commits: Series[int] = pa.Field(ge=1)
    mean_impact: Series[float] = pa.Field(ge=WEIGHT_BASE, le=MAX_IMPACT)
    theta_mean: Series[float]
    theta_ci_5: Series[float]
    theta_ci_95: Series[float]
    landing_points: Series[float] = pa.Field(ge=0.0, le=WEIGHT_LANDING)
    review_points: Series[float] = pa.Field(ge=0.0, le=WEIGHT_REVIEW)
    attach_points: Series[float] = pa.Field(ge=0.0, le=WEIGHT_ATTACH)

    class Config:
        coerce = True
        strict = True
        unique = ["contributor"]

    @pa.dataframe_check
    def credible_interval_ordered(cls, df: pd.DataFrame) -> Series[bool]:
        return (df["theta_ci_5"] <= df["theta_mean"]) & (
            df["theta_mean"] <= df["theta_ci_95"]
        )


def contributor_component_means(
    scored: pd.DataFrame, validate: bool = True
) -> pd.DataFrame:
    """Mean landing / review / attach points per contributor over all scored commits."""
    attributed = scored.dropna(subset=["contributor"]).copy()
    if attributed.empty:
        raise ValueError("scored commits has no attributed contributors")

    frame = (
        attributed.groupby("contributor", sort=False)
        .agg(
            n_commits=("impact", "size"),
            landing_points=("landing_points", "mean"),
            review_points=("review_points", "mean"),
            attach_points=("attach_points", "mean"),
        )
        .reset_index()
    )
    frame = frame[COMPONENT_MEAN_COLUMNS]
    return ContributorComponentSchema.validate(frame) if validate else frame


def last_day_engineer_snapshot(
    theta: pd.DataFrame, scored: pd.DataFrame, validate: bool = True
) -> pd.DataFrame:
    """Inner-join last-day θ rows to full-window component means on `contributor`."""
    if theta.empty:
        raise ValueError("theta panel is empty")

    max_day = int(theta["day_index"].max())
    last = theta.loc[
        theta["day_index"] == max_day,
        [
            "contributor",
            "n_commits",
            "mean_impact",
            "theta_mean",
            "theta_ci_5",
            "theta_ci_95",
        ],
    ].copy()
    parts = contributor_component_means(scored, validate=validate)

    last_ids = set(last["contributor"])
    part_ids = set(parts["contributor"])
    missing = sorted(last_ids - part_ids)
    extra = sorted(part_ids - last_ids)
    if missing or extra:
        raise ValueError(
            "contributor mismatch between last-day θ and scored commits: "
            f"missing_from_scored={missing[:8]!r} extra_in_scored={extra[:8]!r}"
        )

    merged = last.merge(parts, on="contributor", how="inner", validate="one_to_one")
    count_mismatch = merged.loc[
        merged["n_commits_x"] != merged["n_commits_y"], "contributor"
    ].tolist()
    if count_mismatch:
        raise ValueError(
            "n_commits differs between last-day θ and scored commits for "
            f"{count_mismatch[:8]!r}"
        )

    merged["n_commits"] = merged["n_commits_x"]
    merged = merged[LAST_DAY_ENGINEER_COLUMNS]
    return LastDayEngineerSchema.validate(merged) if validate else merged
