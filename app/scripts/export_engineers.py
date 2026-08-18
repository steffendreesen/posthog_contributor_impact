"""Build public/data/engineers.json from last-day θ plus score components.

Each engineer series is the expanding-window posterior mean of θ_j. Last-day
fields add the 90% central credible interval and the mean landing / review /
attach points over the full window. Component weights live once at the payload
root.

Run from the repo root with the notebooks environment:

    notebooks/.venv/bin/python app/scripts/export_engineers.py
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd
from impact_model.scoring import (
    DISPLAY_COMPONENT_WEIGHTS,
    WEIGHT_BASE,
    last_day_engineer_snapshot,
)
from impact_model.storage import read_daily_theta, read_scored_commits

APP_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_DIR.parent
SOURCE_THETA = REPO_ROOT / "model" / "data" / "theta_by_day.parquet"
SOURCE_SCORED = REPO_ROOT / "model" / "data" / "scored_commits.parquet"
APP_THETA = APP_DIR / "data" / "theta_by_day.parquet"
OUTPUT_JSON = APP_DIR / "public" / "data" / "engineers.json"
REPOSITORY = "posthog/posthog"


def round_or_none(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    return round(float(value), 4)


def series_for(row: pd.Series, days: pd.DatetimeIndex) -> list[float | None]:
    return [round_or_none(row[day]) if day in row.index else None for day in days]


def main() -> None:
    theta_source = APP_THETA if APP_THETA.exists() else SOURCE_THETA
    if not theta_source.exists():
        raise FileNotFoundError(
            f"missing {SOURCE_THETA} (and no copy at {APP_THETA})"
        )
    if not SOURCE_SCORED.exists():
        raise FileNotFoundError(f"missing {SOURCE_SCORED}")

    if theta_source != APP_THETA:
        APP_THETA.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(theta_source, APP_THETA)
        print(f"copied {theta_source} -> {APP_THETA}")

    theta = read_daily_theta(theta_source)
    scored = read_scored_commits(SOURCE_SCORED)
    snapshot = last_day_engineer_snapshot(theta, scored)
    snapshot = snapshot.sort_values("theta_mean", ascending=False)

    days = pd.date_range(theta["day"].min(), theta["day"].max(), freq="D", tz="UTC")
    dates = [day.date().isoformat() for day in days]
    wide = theta.pivot(index="contributor", columns="day", values="theta_mean")
    last_by_login = snapshot.set_index("contributor")

    engineers = []
    for login in snapshot["contributor"]:
        last = last_by_login.loc[login]
        engineers.append(
            {
                "login": login,
                "series": series_for(wide.loc[login], days),
                "total_commits": int(last["n_commits"]),
                "mean_impact": round_or_none(last["mean_impact"]),
                "theta_mean": round_or_none(last["theta_mean"]),
                "theta_ci_5": round_or_none(last["theta_ci_5"]),
                "theta_ci_95": round_or_none(last["theta_ci_95"]),
                "components": {
                    "landing": round_or_none(last["landing_points"]),
                    "review": round_or_none(last["review_points"]),
                    "attach": round_or_none(last["attach_points"]),
                },
            }
        )

    mu_by_day = theta.drop_duplicates("day").set_index("day")["mu_mean"].reindex(days)

    payload = {
        "generated_at": dates[-1],
        "source": "gibbs",
        "repository": REPOSITORY,
        "window_days": len(dates),
        "metric": "Posterior mean of θ",
        "interval": "90% central credible interval (5th and 95th posterior percentiles)",
        "score_range": [1, 10],
        "weights": {
            "base": WEIGHT_BASE,
            **DISPLAY_COMPONENT_WEIGHTS,
        },
        "dates": dates,
        "mu": [round_or_none(value) for value in mu_by_day.to_numpy()],
        "engineers": engineers,
    }

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(payload, separators=(",", ":")) + "\n")
    print(
        f"wrote {OUTPUT_JSON} ({len(engineers)} engineers, {len(dates)} days, "
        f"{OUTPUT_JSON.stat().st_size} bytes)"
    )


if __name__ == "__main__":
    main()
