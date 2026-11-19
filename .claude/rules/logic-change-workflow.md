# Logic-change workflow (Momentum Continuation engine, and any future setup)

Standing instruction: **every time the screening logic is refined or revised, follow this checklist
end to end.** It applies to anything under `src/setups/`, `src/data_engine.py` indicator math, and the
status/row-copy mapping. It exists because each past change (EMA50 switch, zero-signal Monitored, MACD
removal, Round 1) repeated the same steps by hand.

## 1. Before writing code
- Read the change note first. It is the delta; the master spec is
  `docs/Momentum Continuation - V2 Logic Improvement Specs.md` (read only the sections you need).
  **Where the two disagree, the change note wins.** If the change note is itself unclear or contradicts
  something else, stop and ask Satwik.
- **Ask instead of guessing** on anything ambiguous: holiday-shortened weeks, data gaps, monthly bars for
  short histories, newly listed tickers, off-by-one on "completed" vs "in-progress" bars. State the
  ambiguity and the default you would pick; do not silently choose.
- Rules marked "do not build" / parked (see spec §13) stay unbuilt. Do not "fix" listed known limits
  without asking.

## 2. Implementation
- **All tunable values live in the config section at the top of `src/setups/momentum_continuation/gates.py`**
  (the spec's `MC` block): thresholds, periods, lookbacks, memory windows. No magic numbers in
  `rules_engine.py` or `status_mapping.py`. A new literal the change note fixes (e.g. "score_now >= 2")
  still gets a named constant.
- No dashboard copy or layout changes beyond what the change note explicitly says. Reuse existing
  templates when the note says to.
- Log every new rule's pieces **separately** (into the row's `diagnostics`; flags into `flags`) so each
  rule can be reviewed on its own. Values must be JSON-safe (no numpy scalars).
- Keep gates (indicator inputs, `gates.py`) and decisions (`rules_engine.py`) separate, as today.

## 3. Verification
- Run the full suite with the **system** Python: `python -m pytest -q` (the repo `.venv` has no pytest).
- Write unit fixtures from the change note's own values (named after the cases: ASML, ADM, ASO, AFRM...).
  Report **pass/fail per fixture and per acceptance-table row**, plus any difference, however small.
  Tolerance is 0.1 on prices/indicators; a one-day difference at "armed" edges is acceptable, anything
  larger gets flagged.
- The acceptance table needs real price history. The Tiingo key exists only as the GitHub Actions secret
  `TIINGO_API_KEY` (local `.streamlit/secrets.toml` holds only the app password). If no key/CSVs are
  available, **say the replay was not run** and ask for the data; never report it as passed.
- Report before/after counts of Qualified / Developing / Monitored on the current universe. "Before" comes
  from the stored `results/*.json`; "after" only exists once the reset queue has drained.

## 4. Keep the docs and memory in sync (same commit as the code)
- Update `docs/Momentum Continuation - V2 Logic Improvement Specs.md`: config block, the affected
  pseudocode sections, the acceptance table, §9 log columns, §13 parked items.
- Keep the change note itself in `docs/` (named `Momentum_Continuation_V2_ChangeNote_R<n>_<date>.md`).
- Update the project memory `swingdeck_momentum_continuation_v2_rebuild` (and any memory the change
  supersedes) so it points at the new behavior.

## 5. Ship
- Commit the logic and docs, then **reset the refresh queue and stored status in a separate commit**
  (precedent: `066a785`, `9beaef2`). Without it the dashboard mixes rows from old and new rules until
  the nightly queue rolls over.
- `git pull --rebase` first: the hourly Actions job commits `[data] daily refresh` to `main` constantly,
  so local `state/` is usually stale.
- Reset procedure (from the repo root; use the **UTC** date, because the Actions runner's `date.today()`
  is UTC - a local-date `night_key` makes the next run finalize your fresh queue as an "incomplete" night):

  ```python
  import sys; sys.path.insert(0, "scripts"); sys.path.insert(0, ".")
  from datetime import datetime, timezone
  import refresh_job as rj
  from src.watchlist_store import discover_watchlists, load_watchlist

  all_tickers = {e["ticker"] for w in discover_watchlists() for e in load_watchlist(w["id"]).get("tickers", [])}
  queue = rj.load_queue()
  rj.finalize_night(queue)                              # snapshot partial progress -> state/refresh_status.json
  fresh = rj.build_fresh_queue(all_tickers, queue.get("rotation_cursor", 0))
  fresh["night_key"] = datetime.now(timezone.utc).date().isoformat()
  rj.save_queue(fresh)
  ```
- Push to `main`, then confirm the push landed and say what is still outstanding (e.g. acceptance replay).
