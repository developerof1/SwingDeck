# Momentum Continuation V2 — Change Note, Round 1 (2026-11-18)

**What this is:** the delta Claude Code builds from. Read this first. The full, merged spec is
`Momentum_Continuation_V2_MASTER_SPEC_v3.md` (read only the sections you need). If this note and the master
ever disagree, stop and ask Satwik.

**Why (one paragraph):** After the MACD removal the Developing bucket was still too big and had the wrong names.
Five live cases (ASML, ADM, ASO, BJ, AFRM) showed three problems: (1) L1 passes names that are breaking down
from a monthly overbought stoch (ASML, ADM), because the weekly EMA50 slope is slow; (2) L1 and L3 flag real
recoveries months late (ASO, BJ); (3) Developing accepts any L1 pass, even with no live trigger, and L2 can
fire on a falling knife (AFRM). Goal: shrink the visible universe to names Satwik can actually time.

## Rules in this round

| ID | Layer | Change |
|---|---|---|
| L1-R1 | L1 | **Breakdown cut.** L1 = FAIL when the monthly stoch has rolled over from overbought **and** the weekly close is below the weekly EMA21 for 2 consecutive completed weeks. Releases automatically when either condition stops being true. |
| L1-R2 | L1 | **Second pass route.** L1 also passes when the weekly close is above both the EMA50 and the EMA21 and the EMA21 is higher than 2 weeks ago. |
| L2-R1 | L2 | **Falling-knife guard.** No daily signal on a day when Close < EMA21 − 1.0 × ATR14. |
| L3-R1 | L3 | **RSI point needs a band.** `rsi_pts` counts only when RSI is rising **and** 40 ≤ RSI_now ≤ 60. |
| L3-R2 | L3 | **Turn memory.** A stoch point or RSI point stays valid for 25 sessions (about 5 weeks) after it last lit. |
| S-R1 | Status | **Developing needs a live trigger.** L1 PASS and (armed **or** this-week score ≥ 2). Otherwise Monitored. |

**Not in this round (do not build):** L1-R3 (higher-timeframe pass route, from BJ, untested) and L1-R4 (weekly
close below EMA21 and EMA50 guard, from AFRM, superseded by L2-R1 + S-R1). Both are parked in master §13.

## Exact logic

### L1 (weekly, completed bars only; replaces master §4 pseudocode)
```
if completed_weekly_bars < L1_MIN_WEEKS: L1 = NOT_EVALUABLE
else:
  ema50 = EMA(wclose, 50);  ema21 = EMA(wclose, 21)                     # ewm span n, adjust=False
  slope_pass = ema50[-1] > ema50[-1 - L1_LOOKBACK_WEEKS]                # unchanged, zero tolerance
  r2_pass    = wclose[-1] > ema50[-1] and wclose[-1] > ema21[-1] \
               and ema21[-1] > ema21[-1 - L1_R2_RISE_WEEKS]             # L1-R2
  # monthly stoch on completed calendar months only: months that ended before the month of the
  # last completed weekly bar's date. Stoch(14,3,3) on monthly H/L/C.
  mroll   = (mK[-1] < mD[-1]) and (max(mK[-4:]) >= L1_R1_OVERBOUGHT)    # False if monthly stoch not computable (<18 months)
  below2  = wclose[-1] < ema21[-1] and wclose[-2] < ema21[-2]
  r1_cut  = mroll and below2                                            # L1-R1
  L1 = PASS if (slope_pass or r2_pass) and not r1_cut else FAIL
```
Log every piece separately (`l1_slope_pass, l1_r2_pass, l1_mroll, l1_below21_2w, l1_r1_cut`) so each rule can be reviewed on its own.
Display is unchanged: a cut or failed L1 still renders `Trend ✗`.

### L2 (daily; adds one condition to master §5)
```
knife(t)  = Close[t] < EMA21[t] - L2_KNIFE_ATR_K * ATR14[t]
l2_signal(t) = any(prox[t-2..t]) and any(cross[t-2..t]) and not knife(t)
```
Everything else in L2 (proximity, cross, 3-session grace, armed age ≤ 5) is unchanged. Log `l2_knife`.

### L3 (weekly, in-progress bar; replaces master §6 scoring lines)
```
stoch_pt_now = 2 if (K_now > K_prev and K_prev <= 20) else 0          # unchanged
rsi_pt_now   = 2 if (RSI_now > RSI_prev and 40 <= RSI_now <= 60) else 0   # L3-R1
score_now    = stoch_pt_now + rsi_pt_now
stoch_pts    = 2 if stoch_pt_now == 2 on any of the last L3_TURN_MEMORY_SESSIONS sessions (today included) else 0   # L3-R2
rsi_pts      = 2 if rsi_pt_now   == 2 on any of the last L3_TURN_MEMORY_SESSIONS sessions (today included) else 0
score        = stoch_pts + rsi_pts                                    # used for Qualified and for the Weekly ✓/✗ display
```
Note the earlier "no level conditions on RSI" line is retired: what was rejected was requiring RSI to already be
above 50 (too late). The 40–60 band is an early-zone check, not a late one.

### Status (replaces master §7)
```
if L1 != PASS:
    status = Monitored            # flags as before; add L1_BREAKDOWN when L1 == FAIL and l1_r1_cut
elif armed and score >= L3_QUALIFY_MIN:
    status = Qualified            # 4/4
elif armed or score_now >= 2:
    status = Developing           # label: "Daily signal in, awaiting weekly confirmation" if armed, else "Awaiting daily signal"
else:
    status = Monitored; flags = [NO_LIVE_TRIGGER]
```
New Monitored case display (no new copy): reuse the Developing "awaiting daily signal" template, shown under the Monitored heading:
`Trend ✓ ; Daily ✗ ; Weekly (RSI {✓/✗}, Stoch {✓/✗})`.

## Config additions (add to the `MC` block)
```python
L1_R2_RISE_WEEKS=2,
L1_R1_OVERBOUGHT=80, L1_R1_MONTHS=4, L1_R1_BELOW_WEEKS=2, L1_R1_EMA_WEEKS=21, L1_R2_EMA_WEEKS=21,
L2_KNIFE_ATR_K=1.0,
L3_RSI_LO=40, L3_RSI_HI=60,
L3_TURN_MEMORY_SESSIONS=25,
```
All provisional; keep them in config, not code.

## Acceptance tests (replay values, adjusted prices, tolerance 0.1 on prices/indicators)
Replay was built from the spec text, so a one-day difference around "armed" edges is acceptable. Anything larger: flag it.

| Case | Expected |
|---|---|
| ASML weekly 2026-10-23, 10-30, 11-06, 11-13 | `l1_r1_cut` = True, **L1 = FAIL**. Values on 11-13: close 1495.16, EMA21 1633.17, EMA50 1501.23, mK 76.55, mD 79.24, max K (4 mo) 89.15. Slope test alone would PASS. |
| ASML historical R1 cuts (regression list) | 2020-03-20→03-27, 2021-10-08, 2021-12-03→12-17, 2023-09-01→09-08, 2024-07-26. The 2020-03 cut is a known false positive (price rallied 68% after); accepted. |
| ADM weekly 2026-11-06 and 11-13 | `l1_r1_cut` = True, **L1 = FAIL**. On 11-13: close 72.90, EMA21 78.14, EMA50 73.61 (4 weeks earlier 73.40, so slope test alone is PASS by +0.29%), mK 74.50, mD 77.60, max K 83.31. |
| ASO weekly 2026-10-09 | `l1_r2_pass` = True (close 53.49, EMA50 50.44, EMA21 49.62, EMA21 rising over 2 weeks), **L1 = PASS**. Today's rule gives PASS only from 2026-10-16. |
| ASO daily 2026-10-09 to about 2026-10-14 | Armed (signal 2026-10-02 to 10-07), stoch point carried from the 2026-09-08 to 09-11 turn (turn memory), RSI point lit → **Qualified** at about $53.5–55.8. Today's rules never qualify it. `l2_knife` must be False on 2026-10-02 to 10-07. |
| ASO weekly turn, week of 2026-09-07 | `rsi_pt_now` = 2 on 09-09 (RSI 52.8, prev 39.2), 09-10 (57.5) and 09-11 (59.0). Stoch point lit 09-08 to 09-11. L1 is still FAIL that week, so status stays Monitored (expected). |
| ASO weekly 2026-11-13 | RSI 73.3 and rising → `rsi_pt_now` = 0 (above 60). Row shows RSI ✗. |
| BJ weekly (latest) | RSI about 69.5 and rising → `rsi_pt_now` = 0. |
| AFRM daily 2026-11-18 (chart-read values, confirm with data) | Close 58.91, daily EMA21 about 67.8, ATR about 3–3.5 → `l2_knife` = True, Daily ✗, weekly score 0 → no live trigger → **Monitored** with `NO_LIVE_TRIGGER`. |

## Known limits (do not "fix" without asking)
- Evidence is five hand-picked names, in-sample. R1 on ASML cut 13 of 308 passing weeks (cut weeks: 56% up at 13 weeks vs 73% for kept weeks). R2 added about 19 passing weeks across three names, mostly one-week blips (12 of 17 with an outcome were up at 13 weeks). Do not add hysteresis this round; log and review.
- L2-R1 removes about 41–46% of raw daily-signal days on the three CSV names. Its effect on outcomes is not yet measured, hence `l2_knife` in the log.
- Qualified calls are rare in history (ASO 2–4 episodes in about 4.8 years, ASML about 5–6, ADM none). Do not tune on them.
- The ✓/✗ shown for Weekly uses the memory-valid points (what feeds Qualified); S-R1 uses this-week points. A row can show RSI ✓ from three weeks ago and still be Monitored. Flag if that reads badly on the dashboard.

## Log additions (master §9)
`l1_slope_pass, l1_r2_pass, l1_mroll, l1_below21_2w, l1_r1_cut, wk_ema21, mo_K, mo_D, mo_Kmax4, l2_knife, stoch_pt_now, rsi_pt_now, score_now, no_live_trigger`.

## Definition of done
1. Rules above implemented, config-driven, with the log columns added.
2. Acceptance table run against the ASML, ADM and ASO histories; results reported (pass/fail per row, plus any difference).
3. Before/after counts of Qualified / Developing / Monitored on the current ticker universe, reported so Satwik can see the Developing drop.
4. No changes to dashboard copy beyond what is written here. Ask before guessing anything ambiguous.
