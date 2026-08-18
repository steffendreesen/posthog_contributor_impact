"""Per-day contributor panel of sufficient statistics.

For the hierarchical normal model in `model/README.md`, the per-group
sufficient statistics are (n_j, sum of y, sum of y squared). Every Gibbs full
conditional can be written in terms of those three numbers:

    theta_j   needs n_j and ybar_j = sum_impact / n_commits
    sigma^2   needs sum (y_ij - theta_j)^2, which expands to
              sum_sq_impact - 2 * theta_j * sum_impact + n_commits * theta_j^2
    mu, tau^2 need only the drawn theta_j

So a model can be fit for any day without touching the commit table, provided
the statistics are cumulative up to that day. That is what this panel stores:
day 1 covers day-1 commits, day t covers days 1 through t.
"""

from __future__ import annotations

import pandas as pd
import pandera.pandas as pa
from pandera.typing import Series

from impact_model.scoring import MAX_IMPACT, WEIGHT_BASE

PANEL_COLUMNS = [
    "day",
    "day_index",
    "contributor",
    "n_commits",
    "sum_impact",
    "sum_sq_impact",
    "mean_impact",
]


class DailyContributorPanelSchema(pa.DataFrameModel):
    """Cumulative per-contributor statistics, one row per (day, contributor).

    A contributor appears only from the day of their first commit onward, so a
    missing row means "no data yet", not "zero impact".
    """

    day: Series[pd.DatetimeTZDtype] = pa.Field(dtype_kwargs={"unit": "ns", "tz": "UTC"})
    day_index: Series[int] = pa.Field(ge=1)
    contributor: Series[str]
    n_commits: Series[int] = pa.Field(ge=1)
    sum_impact: Series[float] = pa.Field(ge=0.0)
    sum_sq_impact: Series[float] = pa.Field(ge=0.0)
    mean_impact: Series[float] = pa.Field(ge=WEIGHT_BASE, le=MAX_IMPACT)

    class Config:
        coerce = True
        strict = True
        unique = ["day", "contributor"]


def daily_contributor_panel(
    scored: pd.DataFrame, validate: bool = True
) -> pd.DataFrame:
    """Build cumulative (expanding-window) statistics per contributor per day.

    Commits with no `contributor` are dropped, per section 1 of the README.
    """
    attributed = scored.dropna(subset=["contributor"]).copy()
    attributed["day"] = attributed["committed_at"].dt.normalize()
    attributed["sq_impact"] = attributed["impact"] ** 2

    days = pd.date_range(
        attributed["day"].min(), attributed["day"].max(), freq="D", tz="UTC"
    )

    per_day = attributed.groupby(["contributor", "day"]).agg(
        n_commits=("impact", "size"),
        sum_impact=("impact", "sum"),
        sum_sq_impact=("sq_impact", "sum"),
    )

    cumulative = {}
    for column in ("n_commits", "sum_impact", "sum_sq_impact"):
        grid = per_day[column].unstack("day").reindex(columns=days).fillna(0.0)
        cumulative[column] = grid.cumsum(axis=1).stack()

    panel = pd.DataFrame(cumulative).reset_index()
    panel.columns = ["contributor", "day", *cumulative]
    panel = panel[panel["n_commits"] > 0].copy()

    panel["day_index"] = (panel["day"] - days[0]).dt.days + 1
    panel["mean_impact"] = panel["sum_impact"] / panel["n_commits"]
    panel = (
        panel[PANEL_COLUMNS]
        .sort_values(["day_index", "contributor"], kind="mergesort")
        .reset_index(drop=True)
    )
    return DailyContributorPanelSchema.validate(panel) if validate else panel


def groups_for_day(panel: pd.DataFrame, day_index: int) -> pd.DataFrame:
    """Statistics for the groups a model fit on `day_index` would use."""
    day_rows = panel[panel["day_index"] == day_index]
    if day_rows.empty:
        raise KeyError(f"no contributors have commits by day {day_index}")
    return day_rows.set_index("contributor")[
        ["n_commits", "sum_impact", "sum_sq_impact", "mean_impact"]
    ]
