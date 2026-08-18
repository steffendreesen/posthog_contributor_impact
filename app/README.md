# PostHog contributor impact dashboard

Static D3 app (nginx in Docker) for Cloud Run. The left panel is an expanding-window timeseries of each contributor’s **posterior mean of θ<sub>j</sub>** (typical commit impact from the hierarchical model in `model/`). Lines start on that person’s first commit day; hover names them; click selects up to six into the right-hand inspector. The red line is that day’s posterior mean of μ and is not selectable.

A selected card shows last-day θ<sub>j</sub> with its 90% central credible interval (5th–95th posterior percentiles — not a 95% interval), commit count, and mean landing / review / attached-issues points as bars against the Section 10 weights. The browser loads `public/data/engineers.json`, rebuilt from `model/data/theta_by_day.parquet` (copied to `data/theta_by_day.parquet`) plus scored commits by `scripts/export_engineers.py`. `make run` builds and serves locally; GCP deploy commands are in `setup.md`.
