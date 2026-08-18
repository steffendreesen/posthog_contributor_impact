# PostHog contributor impact dashboard

Static D3 app (nginx in Docker) for Cloud Run. Right now only the timeseries panel is shown — the engineer profile panel on the right was removed. Each faint line is one contributor’s expanding-window **posterior mean of θ<sub>j</sub>** (typical commit impact from the hierarchical model in `model/`). Lines start on that person’s first commit day; hover shows the GitHub login. The red line is that day’s posterior mean of μ. Credible intervals are not drawn.

The browser loads `public/data/engineers.json`, rebuilt from `model/data/theta_by_day.parquet` (copied to `data/theta_by_day.parquet`) by `scripts/export_engineers.py`. `make run` builds and serves locally; GCP deploy commands are in `setup.md`.
