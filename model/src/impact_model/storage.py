"""Parquet read/write helpers that validate on the way in and out.

Parquet round-trips the ETL's list columns as NumPy arrays, which fails the
list checks on `FinalCommitSchema`. Reading through these helpers restores
Python lists first, so callers never have to remember that.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from impact_model.panel import DailyContributorPanelSchema
from impact_model.scoring import SCORED_COLUMNS, ScoredCommitSchema

LIST_COLUMNS = [
    "branch",
    "pr",
    "pr_state",
    "number_of_comments_on_pr",
    "connected_issue",
    "number_of_comments_on_connected_issue",
]


def to_python_list(value: object) -> list:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, list):
        return value
    return list(value)


def restore_list_columns(frame: pd.DataFrame) -> pd.DataFrame:
    restored = frame.copy()
    for column in LIST_COLUMNS:
        restored[column] = restored[column].map(to_python_list)
    restored["committed_at"] = pd.to_datetime(restored["committed_at"], utc=True)
    return restored


def write_scored_commits(scored: pd.DataFrame, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ScoredCommitSchema.validate(scored[SCORED_COLUMNS]).to_parquet(path, index=False)
    return path


def read_scored_commits(path: str | Path) -> pd.DataFrame:
    frame = restore_list_columns(pd.read_parquet(path))
    return ScoredCommitSchema.validate(frame[SCORED_COLUMNS])


def write_daily_panel(panel: pd.DataFrame, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    DailyContributorPanelSchema.validate(panel).to_parquet(path, index=False)
    return path


def read_daily_panel(path: str | Path) -> pd.DataFrame:
    frame = pd.read_parquet(path)
    frame["day"] = pd.to_datetime(frame["day"], utc=True)
    return DailyContributorPanelSchema.validate(frame)


def write_daily_theta(theta: pd.DataFrame, path: str | Path) -> Path:
    from impact_model.gibbs import DailyThetaSchema, THETA_COLUMNS

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    DailyThetaSchema.validate(theta[THETA_COLUMNS]).to_parquet(path, index=False)
    return path


def read_daily_theta(path: str | Path) -> pd.DataFrame:
    from impact_model.gibbs import DailyThetaSchema

    frame = pd.read_parquet(path)
    frame["day"] = pd.to_datetime(frame["day"], utc=True)
    return DailyThetaSchema.validate(frame)
