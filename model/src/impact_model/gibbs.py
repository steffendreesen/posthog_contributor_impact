"""Hoff Chapter 8 hierarchical-normal Gibbs sampler.

Every full conditional is a named family (normal or inverse-gamma), so a
hand-written NumPy Gibbs step is both the textbook algorithm and the fastest
way to run it. PyMC / NumPyro would replace this with NUTS, which is slower
here and is not Gibbs.

The per-group sufficient statistics (n_j, sum y, sum y^2) come from
`daily_contributor_panel`; a model for day t never re-reads the commit table.
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd
import pandera.pandas as pa
from pandera.typing import Series

from impact_model.panel import groups_for_day

THETA_COLUMNS = [
    "day",
    "day_index",
    "contributor",
    "n_commits",
    "mean_impact",
    "theta_mean",
    "theta_ci_5",
    "theta_ci_95",
    "mu_mean",
    "tau2_mean",
    "sigma2_mean",
]


class DailyThetaSchema(pa.DataFrameModel):
    """Posterior summaries, one row per (day, contributor) with data by that day.

    `theta_ci_5` / `theta_ci_95` are 5% and 95% posterior credible intervals for
    contributor mean impact θ_j. A missing row means the contributor has no
    commits yet, not that impact is zero.
    """

    day: Series[pd.DatetimeTZDtype] = pa.Field(dtype_kwargs={"unit": "ns", "tz": "UTC"})
    day_index: Series[int] = pa.Field(ge=1)
    contributor: Series[str]
    n_commits: Series[int] = pa.Field(ge=1)
    mean_impact: Series[float]
    theta_mean: Series[float]
    theta_ci_5: Series[float]
    theta_ci_95: Series[float]
    mu_mean: Series[float]
    tau2_mean: Series[float] = pa.Field(gt=0.0)
    sigma2_mean: Series[float] = pa.Field(gt=0.0)

    class Config:
        coerce = True
        strict = True
        unique = ["day", "contributor"]

    @pa.dataframe_check
    def credible_interval_ordered(cls, df: pd.DataFrame) -> Series[bool]:
        return (df["theta_ci_5"] <= df["theta_mean"]) & (
            df["theta_mean"] <= df["theta_ci_95"]
        )

# README section 10.6, on the 1–10 impact scale.
MU0 = 5.0
GAMMA0_SQ = 4.0
TAU0_SQ = 0.5
SIGMA0_SQ = 3.0
ETA0 = 1.0
NU0 = 1.0


@dataclass(frozen=True)
class GibbsFit:
    """Draws after burn-in for one expanding-window day."""

    day_index: int
    day: pd.Timestamp
    contributors: np.ndarray
    n_j: np.ndarray
    ybar: np.ndarray
    theta: np.ndarray  # (draws, m)
    mu: np.ndarray
    tau2: np.ndarray
    sigma2: np.ndarray
    n_iter: int
    burn_in: int
    elapsed_s: float
    seed: int

    @property
    def icc(self) -> np.ndarray:
        return self.tau2 / (self.tau2 + self.sigma2)

    @property
    def theta_mean(self) -> np.ndarray:
        return self.theta.mean(axis=0)

    @property
    def n_draws(self) -> int:
        return int(self.mu.shape[0])


def _invgamma(rng: np.random.Generator, shape: float, scale: float) -> float:
    """Inverse-gamma(shape, scale) in the rate/scale form Hoff uses.

    Density ∝ x^{-shape-1} exp(-scale / x). Sampled as 1 / Gamma(shape, scale=1/scale).
    """
    return float(1.0 / rng.gamma(shape, 1.0 / scale))


def fit_day(
    panel: pd.DataFrame,
    day_index: int,
    *,
    n_iter: int = 5000,
    burn_in: int = 1000,
    seed: int = 20260818,
) -> GibbsFit:
    """Run Gibbs on the cumulative groups observed by `day_index`."""
    if n_iter <= 0:
        raise ValueError("n_iter must be positive")
    if not 0 <= burn_in < n_iter:
        raise ValueError("burn_in must satisfy 0 <= burn_in < n_iter")

    groups = groups_for_day(panel, day_index)
    contributors = groups.index.to_numpy()
    n_j = groups["n_commits"].to_numpy(dtype=np.float64)
    sums = groups["sum_impact"].to_numpy(dtype=np.float64)
    sumsq = groups["sum_sq_impact"].to_numpy(dtype=np.float64)
    ybar = groups["mean_impact"].to_numpy(dtype=np.float64)
    m = n_j.size
    n_total = float(n_j.sum())
    day = panel.loc[panel["day_index"] == day_index, "day"].iloc[0]

    rng = np.random.default_rng(seed)
    theta = ybar.copy()
    mu = float(ybar.mean())
    residual_ss = float(np.maximum(sumsq.sum() - (n_j * ybar**2).sum(), 1e-6))
    sigma2 = residual_ss / max(n_total - m, 1.0)
    tau2 = float(max(np.var(ybar, ddof=1), 1e-4)) if m > 1 else TAU0_SQ

    n_keep = n_iter - burn_in
    theta_draws = np.empty((n_keep, m), dtype=np.float64)
    mu_draws = np.empty(n_keep, dtype=np.float64)
    tau2_draws = np.empty(n_keep, dtype=np.float64)
    sigma2_draws = np.empty(n_keep, dtype=np.float64)

    started = perf_counter()
    keep = 0
    for iteration in range(n_iter):
        prec = n_j / sigma2 + 1.0 / tau2
        loc = (n_j * ybar / sigma2 + mu / tau2) / prec
        theta = loc + rng.standard_normal(m) / np.sqrt(prec)

        prec_mu = m / tau2 + 1.0 / GAMMA0_SQ
        loc_mu = (m * float(theta.mean()) / tau2 + MU0 / GAMMA0_SQ) / prec_mu
        mu = float(loc_mu + rng.standard_normal() / np.sqrt(prec_mu))

        sse_between = float(np.sum((theta - mu) ** 2))
        tau2 = _invgamma(rng, (ETA0 + m) / 2.0, (ETA0 * TAU0_SQ + sse_between) / 2.0)

        sse_within = float(np.sum(sumsq - 2.0 * theta * sums + n_j * theta**2))
        sse_within = max(sse_within, 1e-12)
        sigma2 = _invgamma(
            rng, (NU0 + n_total) / 2.0, (NU0 * SIGMA0_SQ + sse_within) / 2.0
        )

        if iteration >= burn_in:
            theta_draws[keep] = theta
            mu_draws[keep] = mu
            tau2_draws[keep] = tau2
            sigma2_draws[keep] = sigma2
            keep += 1

    elapsed_s = perf_counter() - started
    return GibbsFit(
        day_index=day_index,
        day=pd.Timestamp(day),
        contributors=contributors,
        n_j=n_j,
        ybar=ybar,
        theta=theta_draws,
        mu=mu_draws,
        tau2=tau2_draws,
        sigma2=sigma2_draws,
        n_iter=n_iter,
        burn_in=burn_in,
        elapsed_s=elapsed_s,
        seed=seed,
    )


def summarize_fit(fit: GibbsFit) -> pd.DataFrame:
    """One row per contributor: posterior mean and 5%/95% credible intervals."""
    theta_ci_5 = np.quantile(fit.theta, 0.05, axis=0)
    theta_ci_95 = np.quantile(fit.theta, 0.95, axis=0)
    mu_mean = float(fit.mu.mean())
    tau2_mean = float(fit.tau2.mean())
    sigma2_mean = float(fit.sigma2.mean())
    day = pd.Timestamp(fit.day)
    if day.tzinfo is None:
        day = day.tz_localize("UTC")
    else:
        day = day.tz_convert("UTC")
    return pd.DataFrame(
        {
            "day": day,
            "day_index": np.int64(fit.day_index),
            "contributor": fit.contributors.astype(str),
            "n_commits": fit.n_j.astype(np.int64),
            "mean_impact": fit.ybar,
            "theta_mean": fit.theta_mean,
            "theta_ci_5": theta_ci_5,
            "theta_ci_95": theta_ci_95,
            "mu_mean": mu_mean,
            "tau2_mean": tau2_mean,
            "sigma2_mean": sigma2_mean,
        }
    )


# Populated in pool workers so the panel is aggregated once per process, not per day.
_WORKER_PANEL: pd.DataFrame | None = None


def _init_worker(panel_path: str) -> None:
    global _WORKER_PANEL
    from impact_model.storage import read_daily_panel

    _WORKER_PANEL = read_daily_panel(panel_path)


def _worker_summarize(spec: tuple[int, int, int, int]) -> pd.DataFrame:
    day_index, n_iter, burn_in, seed = spec
    if _WORKER_PANEL is None:
        raise RuntimeError("worker panel was not initialized")
    fit = fit_day(
        _WORKER_PANEL, day_index, n_iter=n_iter, burn_in=burn_in, seed=seed
    )
    return summarize_fit(fit)


def posterior_by_day(
    panel: pd.DataFrame,
    day_indexes: list[int] | None = None,
    *,
    n_iter: int = 5000,
    burn_in: int = 1000,
    seed: int = 20260818,
    max_workers: int = 8,
    panel_path: str | Path | None = None,
    validate: bool = True,
) -> pd.DataFrame:
    """Fit one expanding-window model per day and stack posterior summaries.

    The commit-level aggregation must already live in `panel` (and, for the
    process pool, on disk at `panel_path`). Days are independent given that
    table, so they fan out across `max_workers` processes.
    """
    if day_indexes is None:
        day_indexes = sorted(int(v) for v in panel["day_index"].unique())

    if max_workers <= 1 or len(day_indexes) == 1:
        parts = [
            summarize_fit(
                fit_day(
                    panel,
                    day_index,
                    n_iter=n_iter,
                    burn_in=burn_in,
                    seed=seed + day_index,
                )
            )
            for day_index in day_indexes
        ]
    else:
        if panel_path is None:
            raise ValueError(
                "panel_path is required when max_workers > 1 "
                "(workers reload the pre-aggregated panel from disk)"
            )
        specs = [
            (day_index, n_iter, burn_in, seed + day_index) for day_index in day_indexes
        ]
        with ProcessPoolExecutor(
            max_workers=max_workers,
            initializer=_init_worker,
            initargs=(str(panel_path),),
        ) as pool:
            parts = list(pool.map(_worker_summarize, specs))

    frame = (
        pd.concat(parts, ignore_index=True)
        .sort_values(["day_index", "contributor"], kind="mergesort")
        .reset_index(drop=True)
    )
    frame = frame[THETA_COLUMNS]
    return DailyThetaSchema.validate(frame) if validate else frame


def posterior_mean_by_day(
    panel: pd.DataFrame,
    day_indexes: list[int] | None = None,
    **fit_kwargs,
) -> pd.DataFrame:
    """Backward-compatible alias; prefers a sequential fit unless workers are set."""
    fit_kwargs.setdefault("max_workers", 1)
    return posterior_by_day(panel, day_indexes, **fit_kwargs)
