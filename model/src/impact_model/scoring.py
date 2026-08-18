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
