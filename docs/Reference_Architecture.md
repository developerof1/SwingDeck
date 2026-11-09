# SwingDeck Reference Architecture (North Star)

Tracks two growth axes against the current single-watchlist/single-setup baseline:
**Vertical** = more watchlists/universes, same setup. **Horizontal** = more setups,
same watchlist. Checkboxes are checked off as each delta actually ships. If reality
diverges from this doc (a different approach is taken, a requirement turns out to be
unnecessary), **update this doc in the same change** — it's a living reference, not a
one-time plan.

## 1. Baseline (today)

```
watchlists/*.json (N named watchlists, e.g. "AI Trade Names" - 38 tickers)
        |
        v
data_engine.add_continuation_indicators()
        |
        v
rules_engine (Momentum Continuation only)
        |
        v
status_mapping (status -> UI text)
        |
        v
results/<watchlist_id>.json (1 file per watchlist)
        |
        v
app.py (1 setup view, watchlist selector)
```

N watchlists → one engine → N results files → one view with a watchlist selector.
`refresh_job.py` runs this whole chain nightly via GitHub Actions: it fetches/evaluates
each unique ticker once regardless of how many watchlists reference it, fans the result
out into every watchlist's own `results/<id>.json`, and commits `results/` to git
(git-as-DB). `watchlists/*.json` stays manually edited, same as the old single
`watchlist.json` was.

## 2. Growth model: Universe × Setup

Every future ticker evaluation is a cell in this grid. Today we have exactly one populated
cell, but the watchlist mechanism itself now supports any number of rows — adding a second
named watchlist is just dropping a new `watchlists/<id>.json` file, no code change.

| | Momentum Continuation (live) | Mean Reversion / Pullback (future) | Setup N |
|---|---|---|---|
| **AI Trade Names (current watchlist, 38 tickers)** | ✅ shipped | ☐ | ☐ |
| **Additional named watchlist** | ✅ mechanism shipped — none added yet | ☐ | ☐ |
| **Broader universe (e.g. S&P 500 slice)** | ☐ *(vertical)* | ☐ | ☐ |

Vertical growth adds **rows**. Horizontal growth adds **columns**. Neither should require
touching the other axis's code.

## 3. Vertical growth — interchangeable watchlists / broader universes

```
Watchlist registry (N named lists/universes)
        |
        v
data_engine
        |
        v
rules_engine (same setup)
        |
        v
results, keyed by watchlist_id
        |
        v
app.py + watchlist selector
```

**Deltas required:**
- [x] Replace single `watchlist.json` with a small registry — `watchlists/*.json`, one
  self-describing file per watchlist (`src/watchlist_store.py`), no separate registry file
  to keep in sync
- [x] `refresh_job.py` loops over registered watchlists, not one hardcoded path — and
  fetches/evaluates each unique ticker once regardless of how many watchlists reference it
- [x] `results.json` → keyed/split by `watchlist_id` — `results/<watchlist_id>.json`, one
  file per watchlist
- [x] `app.py` gets a watchlist selector; no more hardcoded `WATCHLIST_PATH`/`RESULTS_PATH`
- [ ] Tiingo fetches for a broad universe are **sequenced/staggered through the day**
  (multiple scheduled runs, each covering a slice) — acceptable because every setup here
  is EOD-based, not day-trading-grade, so "yesterday's close" staying valid for a few
  extra hours doesn't cost anything. No rate-limit workaround needed beyond pacing.

## 4. Horizontal growth — parallel setups

```
                   watchlists/*.json
                          |
                          v
     Shared indicator toolkit (SMA/EMA/Stochastic,
           arbitrary window/timeframe)
                          |
            +-------------+-------------+
            |                           |
            v                           v
 Setup: Momentum Continuation  Setup: Mean Reversion /
                                Pullback (future)
            |                           |
            +-------------+-------------+
                          |
                          v
             results, keyed by setup_id
                          |
                          v
            app.py + setup tab/selector
```

**Deltas required:**
- [ ] Define a minimal setup interface (id, `add_indicators(df)`, `evaluate(ticker, df)`,
  status→UI-group map, reason-text renderer) — retrofit Momentum Continuation behind it
  with no behavior change
- [ ] Promote `data_engine`'s private math helpers (`_stochastic`, `_resample_ohlcv`,
  SMA/EMA) into a shared toolkit any setup composes from, instead of each setup writing
  its own monolithic `add_X_indicators`
- [ ] `status_mapping`-equivalent logic becomes per-setup, owned by each setup module —
  only the generic `qualified/developing/other/unknown` taxonomy stays shared
- [ ] `refresh_job.py` loops over registered setups (× watchlists); results tagged with
  `setup_id`
- [ ] `app.py` gets a setup tab/selector instead of the hardcoded "Momentum Continuation"
  label and single engine import

## 5. Combined target (both axes)

```
 Watchlist registry          Setup registry
          \                       /
           v                     v
       refresh_job: for watchlist x setup
                      |
                      v
    results, keyed by (watchlist_id, setup_id)
                      |
                      v
       app.py: watchlist selector x setup tabs
```

`refresh_job.py` becomes the only place that knows about the cross product; neither
registry needs to know the other exists.

## 6. Infra notes / revisit triggers

- **git-as-DB stays fine** at the scale either axis implies today (more JSON rows/files,
  not more writers or queries). No reason to introduce a real database for this alone.
- **Revisit Supabase / a real backend only if** we need: time-series history per
  ticker/setup (backtesting, "how did this signal evolve"), queries across
  setups/watchlists instead of whole-file reads, writes initiated from the UI, or
  multi-user accounts. None of the planned growth above needs these yet.
- **Tiingo rate limit is not a blocker** given EOD-only, non-day-trading setups — pace
  broad-universe fetches across multiple scheduled runs rather than one nightly batch.
