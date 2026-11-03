# SwingDeck — Locked Shortlist Overview: Claude Code Refactoring Specification
Status: Approved UX baseline, 3 November 2026. Implement this overview independently of unfinished detail and feedback views.

## 1. Objective
Refactor the existing Streamlit Cloud app into a focused desktop EOD stock-screening overview. Help the user quickly see Qualified and Developing momentum-continuation stocks and understand why each appears. Do not build a trading terminal or execution interface.

Use the latest approved Stitch screenshot as the visual baseline. Ask the user to supply it if it is not available in the coding environment. Do not reconstruct the obsolete first terminal-style mockup.

## 2. Scope and exclusions
IN: app header, one setup tab, session date, three count/filter cards, grouped two-line candidate rows, prominent reason labels, colored status dots, settings access, empty/error states, basic accessibility.
OUT: ticker search, mobile-specific design, permanent sidebar, future setup tabs, embedded charts, stock-detail redesign, disagreement forms/history, entry/stop/target/R:R/position sizing, performance analytics.
Only Momentum Continuation is available. Do not add disabled future tabs or placeholder modules. No Stage Analysis, Watchlist Deck, Market Breadth, engine-status widgets, readiness scores, or operational metadata.

This is a UI refactor, not authorization to change trading formulas. Use the authoritative latest V2 logic specification, including its Developing addendum. If only V1 exists, identify that mismatch and separate engine migration from the UI patch. Do not silently reinterpret V1 statuses as V2 categories.

## 3. Layout
Desktop web app; dark theme. No permanent navigation or configuration sidebar. Compact header: SwingDeck left, Settings right. Below it: Momentum Continuation as one active setup tab, then a quiet evaluated-session line, then three count cards in one row. Under cards: Qualified section followed by Developing section. Use readable, aligned full-width rows within the overview content area. No selected-stock panel in this implementation scope.

The approved screenshot, not a dense table, is the layout reference. Use available width sensibly; avoid overly narrow text and horizontal scrolling. Do not hardcode a fixed count of stocks.

## 4. Date and freshness
Primary visible text: 'EOD data: [evaluated completed session date]'. This must be the session used by the engine, not today's date or the last app render time. A morning refresh can still evaluate yesterday's session. Refresh timestamp is secondary: place it in a tooltip/help detail with timezone. Do not make two competing prominent timestamp lines.
If symbols have different evaluated sessions, do not falsely label all rows with one date. Surface stale/unavailable symbols explicitly and show their actual session where relevant. Use existing authoritative data-validity behavior; do not invent freshness thresholds.

## 5. Filter/count cards
Labels: Qualified, Developing, Other monitored. Display counts for the loaded scan/watchlist; keep counts independent of current visibility selection.
Default mode: Shortlist, containing Qualified + Developing in that order. Both cards visibly active. Clicking Qualified selects Qualified only; Developing selects Developing only; Other monitored selects Other only. Provide a compact 'Show shortlist' reset when not in default mode. Clicking an already active single-group card need not toggle it off: never leave an ambiguous no-filter selection.
Show hover, focus, and selected states. Use semantic buttons. Other monitored contains ineligible and data-invalid symbols, but explicitly distinguish those assessments in their rows; missing data is not a strategy failure.

## 6. Status mapping
Engine CANDIDATE -> UI Qualified.
Engine DEVELOPING -> UI Developing.
Engine INELIGIBLE_TREND -> Other monitored, specific trend reason.
Engine INELIGIBLE_PULLBACK -> Other monitored, specific pullback reason.
Engine DATA_INVALID -> Other monitored, explicitly 'Data unavailable/invalid' with reason.
Map statuses centrally; do not scatter label translations through rendering code. Unknown statuses must be surfaced, not silently assigned Qualified or Developing.

## 7. Rows and visual hierarchy
Use ticker as the strongest row identifier, company name secondary. The second line communicates why. Retain colored dots on the far right: green Qualified, amber Developing, subdued neutral for exclusions, distinguish data issues with text/icon treatment. Color is reinforcement, never the only status cue.
Qualified reason: 'Trend + pullback criteria met.'
Developing: a prominent short reason badge and relevant measured value/required threshold. Examples only: 'SHALLOW PULLBACK | 0.8 ATR · minimum 1.0'; 'EXTENDED FROM EMA20 | +0.7 ATR · maximum +0.5'. Do not fabricate stock measurements.
Use readable secondary text, sufficient contrast, and comfortable row spacing. Prefer ordinary UI typography over tiny terminal-like monospace. Avoid truncating away the reason. A fully qualified reason should not imply execution approval.
If both Candidate pullback conditions fail, show both short gaps on the second line when space allows; wrap cleanly rather than hiding one or inventing an arbitrary primary blocker. Do not create a hidden combined readiness score.
Do not invent return metrics, ADR values, stochastic/volume waiting reasons, or bullish claims.

## 8. Logic contract for display verification (not an optimization mandate)
Latest completed regular-session daily bar only. Baseline data gate requires 252 completed sessions, valid consistently split-adjusted OHLCV, finite required indicators, ATR14 > 0. Watchlist mode evaluates all supplied symbols; low liquidity is warning-only, not exclusion.
Trend: Close > SMA200; SMA50 > SMA200; SMA50_today > SMA50_20_sessions_ago. Price below SMA50 is allowed. No Close >= EMA21 gate.
Qualified/CANDIDATE: data and trend pass AND Close_today < Close_3_sessions_ago AND 1.0 <= (HighestHigh20-Close)/ATR14 <= 3.0 AND -1.0 <= (Close-EMA20)/ATR14 <= 0.5.
Developing addendum: data and trend pass AND three-session return negative AND drawdown between 0.5 and 3.0 ATR AND EMA20 distance between -1.0 and +1.0 ATR AND at least one Candidate pullback condition fails. These are provisional monitoring bands, not proven profitability thresholds. Confirm endpoint conventions against the final engine spec; do not create a separate UI eligibility calculation.
Candidate takes precedence over Developing. Otherwise retain ineligible/data-invalid outcomes. Both lists may legitimately be empty. Never relax thresholds to populate lists.
Recovery observed = completed close above prior session high; optional annotation only. It never changes group membership. Not required in the locked overview; do not add a new card or filter for it.
Remove obsolete UI gates/controls for stochastic, MTF vetoes, grace windows, nearest EMA8/EMA21 selection, prior-high breakout, RVOL, and projected R:R where they are no longer part of the current engine. Preserve V1 separately if maintained for research; do not overwrite research artifacts.

## 9. Settings and interactions
Keep Settings as a secondary entry point. Reuse existing appropriate settings behavior; remove obsolete screening controls and do not add unapproved new forms. Do not expose rules as editable just because values exist in code.
Stock detail and feedback destinations are not approved yet. Do not invent a new detail page or flagging modal. Preserve an existing detail action only if useful, clearly accessible, and it does not expose obsolete rule interpretations. Otherwise keep rows informational pending the next scope. Never render a clickable-looking row that does nothing. Future row actions should be addable without redesigning the list.

## 10. States
- Initial load: restrained loading state, no fabricated zero-result conclusion.
- No Qualified: 'No qualified stocks in this scan'; Developing still visible.
- No Developing: concise section empty state.
- Both empty: 'No qualified or developing stocks in this scan'; offer Other monitored.
- Partial data issues: compact notice with count and route to affected rows; unaffected results remain usable.
- Fetch failure: distinct from an empty successful scan; retry if supported. Cached results, if shown, must be labeled with their true session.
- Other monitored: exact exclusion/data reason per row; no universal red disqualified wall.

## 11. Engineering requirements
Use existing data sources and calculation results. No duplicate screening logic in UI. Separate engine-to-view-model mapping from components. Preserve calculated values at full precision for comparisons; format for readability without changing eligibility. If rounded output makes a failure look equal to a boundary, show sufficient precision.
Keep scan/filter state stable across ordinary Streamlit reruns; reset appropriately when scan/universe changes. Do not introduce unnecessary dependencies or redesign the app framework. Persisted feedback storage is not part of this task.

## 12. Acceptance tests
1. Default scan displays Qualified and Developing, not Other monitored.
2. Engine CANDIDATE is always labeled Qualified; DEVELOPING stays distinct.
3. Count cards, visible rows, and filter reset agree with actual data.
4. Each Developing row exposes its real failing Candidate condition(s).
5. True evaluated session is visible; refresh does not masquerade as session date.
6. Colored dots retained; no search, sidebar, future setup tabs, or trade-plan outputs.
7. No stochastic/MTF/volume-trigger progression labels appear.
8. Zero results never loosen bands; invalid data never counts as a setup failure.
9. Readable desktop layout without horizontal scrolling; keyboard focus and contrast supported.
10. Engine logic changes, if needed, are separately identified and tested, not buried in UI refactoring.

Deliver a concise file-by-file change summary, test results, and any unresolved API/logic assumptions. Implement only this scope; do not build unfinished feedback or stock-detail screens.
