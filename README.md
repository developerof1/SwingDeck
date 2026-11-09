# SwingDeck

AI Watchlist swing trading dashboard. Evaluates one or more named watchlists nightly against
a systematic swing trading ruleset and displays the results as a password-protected
Streamlit dashboard, with a dropdown to switch between watchlists. Full spec:
[docs/SPEC.md](docs/SPEC.md).

## How it works

1. A GitHub Actions workflow runs on weekday mornings, fetches fresh daily data from
   Tiingo for every unique ticker across all watchlists in `watchlists/` (a ticker shared
   by multiple watchlists is only fetched once), runs the rules engine, and commits one
   results file per watchlist to `results/<watchlist_id>.json`.
2. Streamlit Community Cloud auto-redeploys on every push, so the live app always shows
   the latest committed results. The app itself never calls Tiingo or writes to the repo.

## Local development

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt

cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# edit .streamlit/secrets.toml and set APP_PASSWORD

streamlit run app.py
```

To run the refresh job locally (needs a Tiingo API key):

```bash
set TIINGO_API_KEY=your_key_here   # Windows (cmd)
python scripts/refresh_job.py
```

## Deployment

**GitHub Actions secret** (Settings → Secrets and variables → Actions):
- `TIINGO_API_KEY` — your Tiingo API key.

**Repo setting** (Settings → Actions → General → Workflow permissions):
- Set to "Read and write permissions" so the nightly job can commit `results/*.json`.

**Streamlit Community Cloud** ([share.streamlit.io](https://share.streamlit.io)):
1. Sign in with GitHub, deploy from `developerof1/SwingDeck`, main file `app.py`.
2. Under App settings → Secrets, add:
   ```toml
   APP_PASSWORD = "your-chosen-password"
   ```

## Editing watchlists

Each file under `watchlists/` (e.g. `watchlists/ai_trade_names.json`) is a named watchlist
and is edited manually — no in-app UI:

```json
{
  "name": "AI Trade Names",
  "tickers": [
    { "ticker": "NVDA", "sector": "Chips & Hardware", "thesis": "..." }
  ]
}
```

Edit an existing file directly (locally and push, or via GitHub's web UI) and commit with a
`[data]` prefix, e.g. `[data] add ticker: SOFI`. To add a new watchlist, drop a new
`watchlists/<id>.json` file with the same shape — the dropdown picks it up automatically on
the next refresh, no code change needed. The filename (minus `.json`) becomes the
watchlist's stable id and its `results/<id>.json` filename.

## Project layout

See [docs/SPEC.md](docs/SPEC.md) Section 9 for the full codebase layout and Section 8
for the architecture/deployment model in detail.
