# Technical Specification: AI Watchlist Swing Trading Streamlit Dashboard

## 1. Project Overview & Objective

Build a Python-based **Streamlit web application** that automatically evaluates a custom AI Watchlist against a systematic **5-Step Swing Trading Ruleset**.

The application eliminates manual spreadsheet tracking by processing daily market data through an automated decision engine. It outputs a dynamic "traffic light" dashboard (🟢 Green = `ENTER`, 🟡 Yellow = `WAIT/NEAR SETUP`, 🔴 Red = `DISQUALIFIED/TREND FAILURE`) and categorizes actionable trades into **Momentum Continuation** vs. **Pullback Value** regimes.

It is hosted remotely as a single-user, password-protected dashboard (see Section 8).

---

## 2. Pipeline Architecture Diagram

```text
               +----------------------------------+
               |   Daily Market Data (Tiingo)      |
               +----------------------------------+
                                |
                                v
               +----------------------------------+
               |  STEP 1: Macro Trend Eligibility |
               |     Price > 50-SMA > 200-SMA     |
               +----------------------------------+
                                |
                   +------------+------------+
                   |                         |
               [PASSED]                  [FAILED]
                   |                         |
                   v                         v
   +-------------------------------+   +---------------------------------+
   | STEP 2: Regime Classification |   | 🔴 DISQUALIFIED: Trend Failure |
   |  (Momentum vs. Value Pullback)|   |  (Short-circuit Execution)      |
   +-------------------------------+   +---------------------------------+
                   |
                   v
   +-------------------------------+
   | STEP 3: Location Zone Test    |
   | (Price <= 1.5% from 8/21 EMA) |
   +-------------------------------+
                   |
       +-----------+-----------+
       |                       |
   [PASSED]                [FAILED]
       |                       |
       v                       v
+-----------------------+  +--------------------------------+
| STEP 4: Oscillator    |  | 🟡 WAIT: Extended from Support |
|         Trigger       |  +--------------------------------+
| (Stoch %K crosses %D) |
+-----------------------+
       |
       +-----------+-----------+
       |                       |
   [PASSED]                [FAILED]
       |                       |
       v                       v
+-----------------------+  +--------------------------------+
| STEP 5: Confirmation  |  | 🟡 WAIT: Trigger Pending       |
|  & Position Sizing    |  +--------------------------------+
| Price > Prior High    |
|   AND RVOL >= 1.2     |
+-----------------------+
       |
       +-----------+-----------+
       |                       |
   [PASSED]                [FAILED]
       |                       |
       v                       v
+-----------------------+  +--------------------------------+
| 🟢 ENTER SIGNAL       |  | 🟡 WAIT: Awaiting Confirmation |
| Output Entry, Stop,   |  +--------------------------------+
| Target & R:R Ratio    |
+-----------------------+
```

---

## 3. Full Watchlist & Data Strategy

### Ticker Configuration & Storage Strategy
* **File-Based Source of Truth:** The watchlist lives in `watchlist.json` committed inside the repository.
* **Schema:** a list of objects, not flat ticker strings — `{ "ticker": "NVDA", "sector": "Chips & Hardware", "thesis": "..." }`. `sector` and `thesis` come from the user's existing manual tracking sheet (sector labels stripped of any numeric sort prefix; `thesis` is free text, not used by the rules engine, surfaced only in the UI).
* **No Legacy Spreadsheet Required:** The application operates independently of external Excel files going forward. All pricing and technicals are calculated from a live market data provider — the sector/thesis fields are a one-time import from the prior manual tracker, not an ongoing dependency on it.
* **Manual Editing Only — No In-App Add/Remove UI:** Tickers are added, removed, or reordered by editing `watchlist.json` directly and pushing the change (by hand, or via GitHub's web UI). There is no sidebar control for this. See Section 8 for the rationale (keeps the running app fully read-only against GitHub, no write token required in `app.py`).
* **Reordering Not Required:** The display order of tickers is not meaningful — see Section 6 (sortable columns instead).

### Default Watchlist (38 Names)

Imported from the user's existing manual tracking sheet (Symbol / Industry / Primary Swing Catalyst columns). One duplicate row (`ASML`, listed under two different industry labels in the source sheet) was collapsed to a single entry per the user's explicit choice.

| Ticker | Sector | Thesis |
|---|---|---|
| MU | Chips & Hardware | Cyclical HBM memory play; high ATR for quick percentage swings |
| TSM | Chips & Hardware | Global foundry monopoly; high volume, steady channel swings |
| AVGO | Chips & Hardware | Custom silicon growth; clean trendline support bounces |
| ETN | Power & Thermal Management | Grid management & transformers; steady institutional trend-follower |
| ANET | Networking & Interconnects | Cloud networking leader; clean 20-day and 50-day EMA support bounces |
| NVDA | Chips & Hardware | Ultimate AI beta leader; ideal for oversold RSI bounces |
| GOOGL | Big Tech & Hyperscalers | Gemini expansion; liquid range-bound channel trades |
| ARM | Chips & Hardware | High-beta IP licensor; rapid momentum expansion setups off oversold RSI |
| ASML | Chips & Hardware | Lithography monopoly; sharp mean-reversion moves off key Fib levels |
| AMZN | Big Tech & Hyperscalers | AWS re-acceleration; tight technical consolidation patterns |
| BABA | Big Tech & Hyperscalers | High-volatility international cloud/AI swing trade |
| KLAC | Advanced Packaging & Equipment | Process control & inspection; institutional favorite for MA retests |
| CRDO | Networking & Interconnects | Active Electrical Cables; rapid momentum expansion setups |
| AMD | Chips & Hardware | High beta runner; large weekly price swings |
| PLTR | SaaS with AI Story | Retail/institutional favorite; extreme momentum range swings |
| SNPS | EDA & Custom Silicon | Chip design automation; tight consolidation and breakout setups |
| META | Big Tech & Hyperscalers | Ad-tech monetization; strong momentum breakouts on volume |
| MSFT | Big Tech & Hyperscalers | Core AI cloud enabler; highly predictable moving average bounces |
| AAPL | Big Tech & Hyperscalers | On-device AI rollout; deep liquidity and clean support zones |
| SAP | Big Tech & Hyperscalers | Enterprise AI integration; high-volume European leader |
| SNOW | SaaS with AI Story | Data cloud play; sharp technical bounce off oversold stochastic |
| NOW | SaaS with AI Story | Workflow automation; clean institutional trend-following setups |
| ADBE | SaaS with AI Story | Creative Cloud AI; frequent mean-reversion bounces after dips |
| CRM | SaaS with AI Story | Enterprise agentic AI growth; predictable post-earnings swings |
| AMAT | Advanced Packaging & Equipment | Deposition/etching leader; strong trendline bounces on high volume |
| LRCX | Advanced Packaging & Equipment | Wafer fabrication tools; high-beta mover during chip rallies |
| BESIY | Advanced Packaging & Equipment | Hybrid bonding for 3D packaging; high-amplitude momentum setup |
| CAMT | Advanced Packaging & Equipment | Optical inspection for HBM; rapid percentage swings on volume |
| SKHY | High-Bandwidth Memory (HBM) | Primary HBM supplier to Nvidia; high-volume liquid OTC ADR |
| SSNLF | High-Bandwidth Memory (HBM) | High-volume memory & foundry scale; range-bound swing channel |
| WDC | High-Bandwidth Memory (HBM) | Enterprise SSD storage; clear breakout patterns on daily charts |
| MRVL | Networking & Interconnects | Electro-optics & custom ASICs; high beta for multi-week swings |
| COHR | Networking & Interconnects | Optical transceivers; high-volatility momentum breakout candidate |
| VRT | Power & Thermal Management | Liquid cooling & rack power; prime high-volume momentum runner |
| CEG | Power & Thermal Management | Largest US nuclear operator; reacts sharply to data center power deals |
| VST | Power & Thermal Management | Independent nuclear/gas producer; high-beta clean energy trade |
| SBGSY | Power & Thermal Management | Data center power distribution; high-volume German ADR |
| CDNS | EDA & Custom Silicon | EDA software monopoly; predictable mean-inversion |

*(Note: `SKHY` and `SSNLF` are OTC-traded ADRs — worth double-checking Tiingo has coverage for them once the data engine is built; not all providers carry thin OTC names.)*

---

## 4. Technical Indicators & Calculations

* **Data Provider:** **Tiingo** only for v1. Daily historical candles, minimum 300 days requested to ensure accurate 200-day SMA.
  * *yfinance fallback deferred:* the provider call is isolated behind a single fetch interface (`src/providers/`) specifically so a fallback adapter can be added later without a redesign, but it is not built in v1.
* **Macro Baselines:**
  * `SMA 50` = 50-period Simple Moving Average of Daily Close
  * `SMA 200` = 200-period Simple Moving Average of Daily Close
* **Swing Structure:**
  * `EMA 8` = 8-period Exponential Moving Average (Fast Momentum)
  * `EMA 21` = 21-period Exponential Moving Average (Core Swing Support)
* **Trigger Oscillator:**
  * `Stochastic (14, 3, 3)` = `%K` (14-period Fast Stoch, smoothed by 3) and `%D` (3-period SMA of `%K`).
* **Volume Baseline:**
  * `20-day Volume SMA` = 20-period SMA of Volume.
  * `RVOL` = $\frac{\text{Current Volume}}{\text{20-day Volume SMA}}$

---

## 5. The 5-Step Pipeline Rules Engine

Each stock must be evaluated sequentially through the following rules:

### Step 1: Macro Trend Filter (Hard Gate)
* **Rule:** `Price > SMA_50 AND SMA_50 > SMA_200`
* **Fail Action:** Short-circuit pipeline immediately. Status = 🔴 **`DISQUALIFIED: Trend Failure`**

### Step 2: Regime Classification
* **Rule A (Momentum Continuation):** `Price >= EMA_21`
* **Rule B (Pullback Value):** `Price < EMA_21 AND Price >= SMA_200`

### Step 3: Location Test (Support Zone Proximity)
* **Momentum Mode:** `Distance_To_EMA8 <= 1.5%` OR `Distance_To_EMA21 <= 1.5%`
  * *Formula:* $\text{Distance \%} = \frac{|\text{Price} - \text{EMA}|}{\text{EMA}} \times 100$
* **Pullback Mode:** `Price within 1.5% of SMA_200`.
  * *Resolved ambiguity:* the original spec also allowed "designated structural support," which has no defined detection logic. Dropped — proximity to `SMA_200` alone is the rule.
* **Fail Action:** Status = 🟡 **`WAIT: Extended (Location)`**

### Step 4: Oscillator Trigger (Stochastic Signal)
* **Momentum Mode:** `Stochastic %K(14,3,3) crosses above %D(14,3,3)` where `%K` is between **40 and 65**.
* **Pullback Mode:** `Stochastic %K(14,3,3) crosses above %D(14,3,3)` from oversold territory (**%K < 20**).
* *Resolved ambiguity:* "crosses above" is evaluated over the **most recent 2 trading days** (i.e., the cross happened today or yesterday), not strictly today-only — otherwise a signal goes stale the moment a day passes between data refreshes.
* **Fail Action:** Status = 🟡 **`WAIT: Trigger Pending (Stoch)`**

### Step 5: Confirmation & Position Sizing Engine
* **Condition:** `Current Price > Prior Day High AND RVOL >= 1.2`
* **Trade Calculation Rules:**
  * `Entry Price`: `Prior Day High + $0.05` (Breakout Confirmation)
  * `Stop Loss`: `Swing Low of last 5 days - 0.5%`.
    * *Resolved ambiguity:* this is the sole stop-loss rule. The original "or below key EMA support" alternative is dropped as a primary rule; the relevant EMA is only used as a sanity check (stop should sit below it).
  * `Target Price`:
    * *Momentum Mode:* `Entry + (Entry - Stop) * 2.5`.
      * *Resolved ambiguity:* this R-multiple formula is the sole rule. The original "+12% to +15% default" alternative is dropped to keep the calculation deterministic.
    * *Pullback Mode:* Capped at underside of 21-EMA or 50-SMA resistance
  * `Risk/Reward Ratio (R:R)`: $\frac{\text{Target} - \text{Entry}}{\text{Entry} - \text{Stop}}$ (Minimum threshold = **2.0**)
* **Pass Action:** Status = 🟢 **`ENTER`**

---

## 6. Front-End Dashboard UI (Streamlit Requirements)

1. **Header & Summary Metric Cards:**
   * Total Monitored Tickers (e.g., `38`)
   * 🟢 Actionable Signals (`ENTER`)
   * 🟡 Near Setup (`WAIT: Location / Trigger`)
   * 🔴 Disqualified (`WAIT: Trend Failure`)

2. **Main Traffic Light Data Table:**
   * Row styling / badges reflecting status (🟢 Green, 🟡 Yellow, 🔴 Red).
   * **Columns:** `Ticker`, `Sector`, `Status`, `Current Price`, `Regime`, `Dist to 21-EMA %`, `Stoch %K/%D`, `RVOL`, `Suggested Entry`, `Stop Loss`, `Target Price`, `R:R Ratio`.
   * **Sortable by column header**, ascending/descending, for every column — the implementation (`st.dataframe`) sorts all columns uniformly, including `Status`; pinning `Status` to pipeline order while leaving other columns sortable isn't supported by the chosen UI primitive and was treated as a minor, acceptable deviation rather than building a custom grid for it.
   * **Expandable row detail:** selecting a row shows the ticker's `thesis` text and the pipeline's `reason` string (not shown in the main grid — too long to fit as a column).

3. **Sidebar Controls:**
   * **Parameter Sliders:**
     * `Location Threshold %` (Default: `1.5%`, range: `0.5% - 3.0%`)
     * `Min RVOL Threshold` (Default: `1.2`, range: `1.0 - 2.0`)
   * **Sector Filter:** multi-select to show only selected sectors; defaults to all.
   * No watchlist add/remove/reorder controls (see Section 3 — editing is manual, outside the app).
   * **Data Control:** app reads precomputed results written by the scheduled refresh job (see Section 8) rather than calling the data provider live; no "Refresh Data" button or client-side caching TTL needed. Moving the sliders re-runs the rules engine **locally**, against the small per-ticker indicator snapshot stored in `results.json`, not against live Tiingo data — see Section 8.

---

## 7. Technical Indicators Validation

* Validate the pipeline against known tickers (`BABA`, `WDC`, `META`) during development.
* Automated test files are optional for this single-user project — manual validation against a 2-3 ticker watchlist is sufficient to start. Add `tests/` later only if the ruleset keeps changing and regressions become a real risk.

---

## 8. Architecture & Deployment

* **Hosting:** Streamlit Community Cloud, deployed from `developerof1/SwingDeck` on GitHub.
* **Access Control:** single shared-secret password gate implemented in `app.py`, checked against `APP_PASSWORD` in Streamlit's `st.secrets` (not committed to git). No per-user accounts.
* **Data Provider:** Tiingo only (free tier) for v1 — see Section 4. yfinance fallback deferred until actually needed.
* **Scheduled Refresh (no external database):**
  * A GitHub Actions workflow (`.github/workflows/refresh_data.yml`) runs weekdays at 11:00 UTC (overnight US time, after EOD data has settled) and can also be triggered manually via `workflow_dispatch`.
  * It executes `scripts/refresh_job.py`, which fetches market data via Tiingo, runs the rules engine with default thresholds, and writes `results.json` — including, per ticker, both the default-threshold evaluation and a small 5-row indicator snapshot.
  * The workflow commits and pushes `results.json` back to the repo using the GitHub Actions-provided token (`permissions: contents: write`) — no external secret needed for this step beyond `TIINGO_API_KEY`, which must be set as a GitHub Actions repository secret.
  * Streamlit Community Cloud auto-redeploys on every push to the watched branch, so the live app picks up new results automatically without ever calling the data provider itself.
* **Interactive thresholds without live data:** `app.py` never calls Tiingo. When the sidebar sliders move, it re-runs the same `rules_engine.evaluate_stock()` locally against the 5-row snapshot stored per ticker in `results.json` — this is enough data for the 5-day swing low, prior-day-high, and stochastic-cross lookback checks, since those only ever examine the last few rows. SMA 50/200, EMA 8/21, and Stochastic %K/%D themselves are precomputed server-side (they need the full history) and carried through the snapshot as-is.
* **Persistence Model — Git as the Database:** both `watchlist.json` (edited manually) and `results.json` (written by the scheduled job) are ordinary committed files. There is no external database (Turso, SQLite, etc.) — git's commit history doubles as the audit trail.
  * `app.py` is **read-only** with respect to GitHub: it never writes or pushes anything, so it needs no write-scoped token. This is a direct consequence of making watchlist edits manual-only.
  * Trade-off: every data refresh and every manual watchlist edit triggers a full app redeploy (brief downtime / cold start), and the git history accumulates one commit per refresh/edit. Acceptable for a low-frequency, single-user tool.
  * **Commit convention:** tag both automated refresh commits and manual watchlist-edit commits with a `[data]` prefix (e.g. `[data] daily refresh 2026-10-17`, `[data] add ticker: SOFI`) to keep them distinguishable from real code changes in the git log.

---

## 9. Codebase Layout

```text
SwingDeck/
├── .github/workflows/refresh_data.yml   # weekday cron -> runs refresh_job.py -> commits results.json
├── .streamlit/
│   ├── config.toml                      # theme/page config
│   └── secrets.toml.example             # documents required secret keys; real secrets.toml is gitignored
├── app.py                               # entrypoint: password gate, layout, reads results.json
├── docs/SPEC.md                         # this document
├── src/
│   ├── providers/tiingo_provider.py      # v1 data source; interface leaves room for a fallback adapter later
│   ├── data_engine.py                   # indicator calcs: SMA/EMA/Stochastic/RVOL + snapshot serialization
│   └── rules_engine.py                  # evaluate_stock() - the 5-step pipeline, pure logic
├── scripts/refresh_job.py               # entrypoint the GitHub Actions cron calls
├── watchlist.json                       # manually edited, committed source of truth
├── results.json                         # written by refresh_job.py, read by app.py
├── requirements.txt
├── runtime.txt                          # pins Python 3.12 for Streamlit Community Cloud
├── .gitignore
└── README.md
```
