"""SwingDeck - AI Watchlist swing trading dashboard.

Reads precomputed results.json (written nightly by scripts/refresh_job.py via GitHub
Actions - see docs/SPEC.md Section 8). Never calls Tiingo directly. The sidebar
threshold sliders re-run the same rules_engine.evaluate_stock() locally against the
small per-ticker snapshot stored in results.json, so they're interactive without any
live data fetch.
"""

import json
import os
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from src.rules_engine import (
    DEFAULT_LOCATION_THRESHOLD_PCT,
    DEFAULT_RVOL_THRESHOLD,
    MIN_RISK_REWARD,
    STATUS_DISQUALIFIED,
    STATUS_ENTER,
    STATUS_WAIT_CONFIRMATION,
    STATUS_WAIT_LOCATION,
    STATUS_WAIT_TRIGGER,
    evaluate_stock,
)

REPO_ROOT = os.path.dirname(__file__)
RESULTS_PATH = os.path.join(REPO_ROOT, "results.json")
WATCHLIST_PATH = os.path.join(REPO_ROOT, "watchlist.json")

GREEN, RED, YELLOW = "\U0001f7e2", "\U0001f534", "\U0001f7e1"

# True/False/None pass state -> the same traffic-light ball everywhere in the UI
# (status badges, the per-step pipeline checklist, and the threshold-context cells).
STEP_CIRCLE = {True: GREEN, False: RED, None: YELLOW}

STATUS_BADGE = {
    STATUS_ENTER: f"{GREEN} ENTER",
    STATUS_DISQUALIFIED: f"{RED} DISQUALIFIED",
    STATUS_WAIT_LOCATION: f"{YELLOW} WAIT · Extended",
    STATUS_WAIT_TRIGGER: f"{YELLOW} WAIT · Oscillator",
    STATUS_WAIT_CONFIRMATION: f"{YELLOW} WAIT · Confirmation",
}

FILTER_PILLS = ["All", f"{GREEN} ACTIONABLE", f"{YELLOW} WAIT", f"{RED} DISQUALIFIED"]

st.set_page_config(page_title="SwingDeck", layout="wide")


def check_password() -> bool:
    if st.session_state.get("authenticated"):
        return True

    st.title("SwingDeck")

    try:
        expected_password = st.secrets.get("APP_PASSWORD")
    except Exception:
        expected_password = None

    if not expected_password:
        st.error("APP_PASSWORD is not configured in secrets yet - set it before this app can be used.")
        return False

    password = st.text_input("Password", type="password")
    if st.button("Log in") or password:
        if password == expected_password:
            st.session_state["authenticated"] = True
            st.rerun()
        elif password:
            st.error("Incorrect password")
    return False


def load_results(path: str) -> dict:
    # Deliberately uncached: results.json is precomputed server-side and only ever
    # changes via a git push + redeploy, but caching here risks showing stale data
    # across a redeploy that doesn't fully restart the process. The file is tiny,
    # so re-reading it on every rerun costs nothing.
    with open(path) as f:
        return json.load(f)


def load_watchlist(path: str) -> list[dict]:
    with open(path) as f:
        return json.load(f)


def badge(status: str) -> str:
    return STATUS_BADGE.get(status, f"{YELLOW} {status}")


# Regime is left out: it can never fail once Trend has passed (Trend already requires
# price > 200-SMA, which is the only way Regime's own check could fail), so it's
# always green except on an already-DISQUALIFIED row - covered by the Status badge
# and the inspector's full 5-step breakdown, not worth a ball here.
#
# Trend IS shown, but separated from the rest by "|": it's a pass/fail gate with no
# metric column of its own. After the separator, every remaining ball is exactly one
# column's pass/fail, in that column's left-to-right order: Location (Dist to
# Support), Oscillator (Stoch %K), Breakout (vs Prior-Day High), RVOL, R:R. The old
# single "Confirmation" ball covered 3 columns (Breakout/RVOL/R:R) at once, which is
# exactly what broke the 1-ball-per-column correspondence - it's split into its 3
# sub-checks here so every ball has exactly one matching column.
def checklist_cell(
    trend_passed: bool | None,
    location_passed: bool | None,
    oscillator_passed: bool | None,
    breakout_ok: bool | None,
    rvol_ok: bool | None,
    rr_ok: bool | None,
) -> str:
    """Trend, then - after a separator - one ball per column to its right, in the same
    left-to-right order: Location, Oscillator, Breakout, RVOL, R:R."""
    rest = [STEP_CIRCLE[p] for p in (location_passed, oscillator_passed, breakout_ok, rvol_ok, rr_ok)]
    return STEP_CIRCLE[trend_passed] + " ❘ " + "".join(rest)


def threshold_cell(value: float | None, passed: bool | None, fmt: str) -> str:
    """A number paired with the traffic-light ball for whether it clears its own
    threshold, so the value reads with context instead of as a bare float."""
    if value is None or pd.isna(value):
        return f"{STEP_CIRCLE[None]} –"
    return f"{STEP_CIRCLE[passed]} {fmt.format(value)}"


def live_eval(entry: dict, location_thresh: float, rvol_thresh: float):
    snapshot_df = pd.DataFrame(entry["snapshot"])
    snapshot_df["date"] = pd.to_datetime(snapshot_df["date"])
    state = entry.get("state") or {}
    return evaluate_stock(
        entry["ticker"],
        snapshot_df,
        location_thresh,
        rvol_thresh,
        location_last_ok_date=state.get("location_last_ok_date"),
        oscillator_last_triggered_date=state.get("oscillator_last_triggered_date"),
    )


def days_in_regime(entry: dict, as_of: str) -> int | None:
    """Calendar days since this ticker's regime (Momentum/Pullback/none) last
    changed, per refresh_job.py's day-over-day state diffing. None if no prior-state
    history exists yet for this ticker (e.g. right after this feature's first deploy)."""
    since = (entry.get("state") or {}).get("regime_since")
    if not since:
        return None
    try:
        return (date.fromisoformat(as_of) - date.fromisoformat(since)).days
    except ValueError:
        return None


def staleness_banner(generated_at: str) -> None:
    """Surfaces how far results.json is behind schedule, counting only trading
    (weekday) days so a Monday check doesn't falsely flag the Friday-to-Monday gap -
    see the 2026-10-28 grace-window TypeError incident that silently broke the
    nightly refresh for 2 days before anyone noticed from the UI alone."""
    try:
        generated = date.fromisoformat(generated_at)
    except ValueError:
        st.caption(f"Last refreshed: {generated_at}")
        return

    today = date.today()
    missed_trading_days = (
        len(pd.bdate_range(generated + timedelta(days=1), today)) if generated < today else 0
    )
    if missed_trading_days >= 2:
        st.error(
            f"{RED} Last refreshed {generated_at} — {missed_trading_days} trading day(s) behind. "
            "The nightly GitHub Actions refresh may be failing — check the Actions tab."
        )
    elif missed_trading_days == 1:
        st.warning(f"{YELLOW} Last refreshed {generated_at} — today's refresh hasn't landed yet.")
    else:
        st.caption(f"{GREEN} Last refreshed: {generated_at}")


def main() -> None:
    if not check_password():
        st.stop()

    data = load_results(RESULTS_PATH)
    watchlist = load_watchlist(WATCHLIST_PATH)
    sector_by_ticker = {w["ticker"]: w.get("sector") for w in watchlist}

    st.title("SwingDeck")

    if not data.get("generated_at"):
        st.info("No data yet - waiting for the first scheduled refresh to run.")
        return

    staleness_banner(data["generated_at"])

    success_count = len(data.get("results", []))
    errors = data.get("errors", [])
    error_count = len(errors)
    total_watchlist = success_count + error_count

    if error_count and success_count == 0:
        st.error(
            f"This refresh couldn't evaluate any of the {total_watchlist} tickers. "
            "Showing the error details below instead of a results table."
        )
        error_df = pd.DataFrame(
            [
                {"Ticker": e["ticker"], "Sector": sector_by_ticker.get(e["ticker"]), "Reason": e["error"]}
                for e in errors
            ]
        )
        st.dataframe(error_df, width="stretch", hide_index=True)
        return
    elif error_count:
        st.warning(f"{error_count} of {total_watchlist} tickers couldn't be refreshed this run - see details below the table.")

    st.sidebar.header("Parameters")
    location_thresh = st.sidebar.slider(
        "Location Threshold %", min_value=0.5, max_value=3.0, value=DEFAULT_LOCATION_THRESHOLD_PCT, step=0.1
    )
    rvol_thresh = st.sidebar.slider(
        "Min RVOL Threshold", min_value=1.0, max_value=2.0, value=DEFAULT_RVOL_THRESHOLD, step=0.05
    )

    all_sectors = sorted({w.get("sector") for w in watchlist if w.get("sector")})
    st.markdown(
        """
        <style>
        [data-testid="stMultiSelect"] div:has(> [data-testid="stMultiSelectTagsContainer"]) {
            height: auto !important;
            max-height: 420px !important;
            overflow-y: auto !important;
        }
        [data-testid="stMultiSelectTagsContainer"] {
            height: auto !important;
            max-height: none !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    selected_sectors = st.sidebar.multiselect("Sectors", options=all_sectors, default=all_sectors)

    rows = []
    eval_errors = []
    for entry in data["results"]:
        if entry.get("sector") not in selected_sectors:
            continue
        try:
            result = live_eval(entry, location_thresh, rvol_thresh)

            by_key = {s.key: s for s in result.steps}
            trend_passed = by_key["trend"].passed if "trend" in by_key else None
            location_passed = by_key["location"].passed if "location" in by_key else None
            oscillator_passed = by_key["oscillator"].passed if "oscillator" in by_key else None
            rvol_ok = None if result.rvol is None else result.rvol >= rvol_thresh
            rr_ok = None if result.rr is None else result.rr >= MIN_RISK_REWARD
            # getattr, not result.X: these fields were added after the others, so a
            # stale cached rules_engine module (an old EvalResult class, missing the
            # field) would otherwise crash the whole page for every user instead of
            # just degrading this one cell - see the try/except below too.
            location_dist_pct = getattr(result, "location_dist_pct", None)
            prior_day_high = getattr(result, "prior_day_high", None)
            breakout_ok = getattr(result, "breakout_ok", None)
            # Only meaningful once Trend has actually failed - blank (None) otherwise,
            # same convention as Entry/Stop/Target being blank pre-Step-5. Kept as raw
            # floats (not threshold_cell strings) so the columns sort numerically.
            price_vs_sma50_pct = (
                getattr(result, "price_vs_sma50_pct", None) if trend_passed is False else None
            )
            sma50_vs_sma200_pct = (
                getattr(result, "sma50_vs_sma200_pct", None) if trend_passed is False else None
            )

            rows.append(
                {
                    "Status": badge(result.status),
                    # Driven by refresh_job.py's day-over-day regime diffing - a quick
                    # scan of how long a setup has been building, not shown in the
                    # detail panel since it's already visible here per row.
                    "Days in Regime": days_in_regime(entry, data["generated_at"]),
                    "Ticker": entry["ticker"],
                    "Sector": entry.get("sector"),
                    "Pipeline": checklist_cell(
                        trend_passed, location_passed, oscillator_passed, breakout_ok, rvol_ok, rr_ok
                    ),
                    "Price": result.current_price,
                    # Blank unless Trend actually failed - see price_vs_sma50_pct comment
                    # above. Distinguishes a "price-only" gap (SMA50 already above SMA200,
                    # could clear on one good day) from a "structural" one (SMA50 itself
                    # still below SMA200, resolves over weeks regardless of tomorrow's price).
                    "Price vs SMA50 %": price_vs_sma50_pct,
                    "SMA50 vs SMA200 %": sma50_vs_sma200_pct,
                    # Reuses the Location step's own pass/fail and its regime-appropriate
                    # distance (nearest-EMA for Momentum, 200-SMA for Pullback) instead of
                    # recomputing a check against dist_to_ema21_pct alone - that recompute
                    # could silently disagree with the Pipeline ball on Pullback-regime
                    # rows, where the EMA21 distance isn't what Location actually gates on.
                    "Dist to Support": threshold_cell(location_dist_pct, location_passed, "{:.2f}%"),
                    "Stoch %K": threshold_cell(result.stoch_k, oscillator_passed, "{:.1f}"),
                    # Confirmation is really 3 ANDed checks - breakout, RVOL, R:R - so
                    # all 3 need their own column, or the Confirmation ball can show red
                    # while every *visible* column is green (the breakout check failing
                    # invisibly). Evaluated first in rules_engine.py, so shown first here.
                    "vs Prior-Day High": threshold_cell(prior_day_high, breakout_ok, "${:.2f}"),
                    "RVOL": threshold_cell(result.rvol, rvol_ok, "{:.2f}x"),
                    "R:R": threshold_cell(result.rr, rr_ok, "{:.2f}"),
                    "Thesis": entry.get("thesis") or "–",
                    "_result": result,
                }
            )
        except Exception as exc:
            # One bad ticker (stale cached module, unexpected data shape, etc.) should
            # degrade to "this row is missing", never take down the page for everyone.
            eval_errors.append({"ticker": entry["ticker"], "error": f"{type(exc).__name__}: {exc}"})

    if eval_errors:
        st.warning(
            f"{len(eval_errors)} ticker(s) failed to evaluate and are missing from the table below "
            "- see details under the table."
        )
        errors = errors + eval_errors

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("Status", ascending=False, kind="stable").reset_index(drop=True)

    enter_count = sum(1 for r in rows if r["_result"].status == STATUS_ENTER)
    disqualified_count = sum(1 for r in rows if r["_result"].status == STATUS_DISQUALIFIED)
    waiting_count = len(rows) - enter_count - disqualified_count

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Monitored", len(rows))
    col2.metric(f"{GREEN} Actionable", enter_count)
    col3.metric(f"{YELLOW} Near Setup", waiting_count)
    col4.metric(f"{RED} Disqualified", disqualified_count)

    if not rows:
        if eval_errors:
            # Surface *why* here too, not just in the "N failed to evaluate" warning
            # above - this used to return before ever reaching the error-detail
            # expander at the bottom of the page, hiding the actual exception text
            # behind a misleading "no tickers match the filter" message.
            st.info("No tickers to show - every evaluated ticker failed (see details below).")
        else:
            st.info("No tickers match the current sector filter.")
        if errors:
            with st.expander(f"Data unavailable for {len(errors)} ticker(s)", expanded=True):
                error_df = pd.DataFrame(
                    [
                        {"Ticker": e["ticker"], "Sector": sector_by_ticker.get(e["ticker"]), "Reason": e["error"]}
                        for e in errors
                    ]
                )
                st.dataframe(error_df, width="stretch", hide_index=True)
        return

    active_pill = st.pills("Filter", FILTER_PILLS, default="All", label_visibility="collapsed")
    if active_pill == f"{GREEN} ACTIONABLE":
        df = df[df["_result"].apply(lambda r: r.status == STATUS_ENTER)]
    elif active_pill == f"{YELLOW} WAIT":
        df = df[df["_result"].apply(lambda r: r.status not in (STATUS_ENTER, STATUS_DISQUALIFIED))]
    elif active_pill == f"{RED} DISQUALIFIED":
        df = df[df["_result"].apply(lambda r: r.status == STATUS_DISQUALIFIED)]
    df = df.reset_index(drop=True)

    if df.empty:
        st.info("No tickers match the current filter.")
        return

    display_df = df.drop(columns=["_result"])
    event = st.dataframe(
        display_df,
        width="stretch",
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "Price": st.column_config.NumberColumn(format="$%.2f"),
            "Price vs SMA50 %": st.column_config.NumberColumn(
                format="%.2f", help="Signed % distance of price from the 50-SMA. Only shown once Trend has "
                "failed - negative means price is below the 50-SMA."
            ),
            "SMA50 vs SMA200 %": st.column_config.NumberColumn(
                format="%.2f", help="Signed % distance of the 50-SMA from the 200-SMA. Negative means a "
                "structural gap (death cross) that resolves over weeks, not a single good price day."
            ),
            "Days in Regime": st.column_config.NumberColumn(
                format="%d", help="Calendar days since this ticker's regime (Momentum/Pullback/none) last changed"
            ),
            "Pipeline": st.column_config.TextColumn(
                help="Trend (gate)  |  Location → Oscillator → Breakout → RVOL → R:R - one ball per column "
                "to the right, in that order"
            ),
            "Thesis": st.column_config.TextColumn(width="medium"),
        },
    )

    selected_rows = event.selection.rows if event and event.selection else []
    if selected_rows:
        selected = df.iloc[selected_rows[0]]
        result = selected["_result"]
        with st.expander(f"Details: {selected['Ticker']}", expanded=True):
            # st.markdown treats single $ as LaTeX math delimiters, so dollar amounts
            # in the generated detail text must be escaped or they render as a code pill.
            st.markdown(f"**{badge(result.status)}**  ·  Regime: {result.regime or '–'}")
            escaped_reason = result.reason.replace("$", "\\$")
            st.markdown(f"_{escaped_reason}_")
            for step in result.steps:
                detail = step.detail.replace("$", "\\$")
                st.markdown(f"{STEP_CIRCLE[step.passed]} **{step.label}** — {detail}")
            if result.entry is not None:
                st.caption("Trade levels (live once ENTER; hypothetical otherwise)")
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Entry", f"${result.entry:.2f}")
                c2.metric("Stop", f"${result.stop:.2f}")
                c3.metric("Target", f"${result.target:.2f}")
                c4.metric("R:R", f"{result.rr:.2f}")

    if errors:
        with st.expander(f"Data unavailable for {len(errors)} ticker(s)"):
            error_df = pd.DataFrame(
                [
                    {"Ticker": e["ticker"], "Sector": sector_by_ticker.get(e["ticker"]), "Reason": e["error"]}
                    for e in errors
                ]
            )
            st.dataframe(error_df, width="stretch", hide_index=True)


main()
