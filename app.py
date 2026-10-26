"""SwingDeck - AI Watchlist swing trading dashboard.

Reads precomputed results.json (written nightly by scripts/refresh_job.py via GitHub
Actions - see docs/SPEC.md Section 8). Never calls Tiingo directly. The sidebar
threshold sliders re-run the same rules_engine.evaluate_stock() locally against the
small per-ticker snapshot stored in results.json, so they're interactive without any
live data fetch.
"""

import json
import os

import pandas as pd
import streamlit as st

from src.rules_engine import (
    DEFAULT_LOCATION_THRESHOLD_PCT,
    DEFAULT_RVOL_THRESHOLD,
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
    STATUS_WAIT_LOCATION: f"{YELLOW} WAIT · Location",
    STATUS_WAIT_TRIGGER: f"{YELLOW} WAIT · Oscillator",
    STATUS_WAIT_CONFIRMATION: f"{YELLOW} WAIT · Confirmation",
}

FILTER_PILLS = ["All", f"{GREEN} ENTER", f"{YELLOW} WAIT", f"{RED} DISQUALIFIED"]

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


def checklist_cell(steps) -> str:
    """The 5-step pipeline as a row of traffic-light balls, in pipeline order."""
    return "".join(STEP_CIRCLE[s.passed] for s in steps)


def threshold_cell(value: float | None, passed: bool | None, fmt: str) -> str:
    """A number paired with the traffic-light ball for whether it clears its own
    threshold, so the value reads with context instead of as a bare float."""
    if value is None or pd.isna(value):
        return f"{STEP_CIRCLE[None]} –"
    return f"{STEP_CIRCLE[passed]} {fmt.format(value)}"


def live_eval(entry: dict, location_thresh: float, rvol_thresh: float):
    snapshot_df = pd.DataFrame(entry["snapshot"])
    snapshot_df["date"] = pd.to_datetime(snapshot_df["date"])
    return evaluate_stock(entry["ticker"], snapshot_df, location_thresh, rvol_thresh)


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

    st.caption(f"Last refreshed: {data['generated_at']}")

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
    for entry in data["results"]:
        if entry.get("sector") not in selected_sectors:
            continue
        result = live_eval(entry, location_thresh, rvol_thresh)

        oscillator_step = next(s for s in result.steps if s.key == "oscillator")
        dist_ok = None if result.dist_to_ema21_pct is None else result.dist_to_ema21_pct <= location_thresh
        rvol_ok = None if result.rvol is None else result.rvol >= rvol_thresh

        rows.append(
            {
                "Status": badge(result.status),
                "Ticker": entry["ticker"],
                "Sector": entry.get("sector"),
                "Pipeline": checklist_cell(result.steps),
                "Price": result.current_price,
                "Dist to 21-EMA": threshold_cell(result.dist_to_ema21_pct, dist_ok, "{:.2f}%"),
                "Stoch %K": threshold_cell(result.stoch_k, oscillator_step.passed, "{:.1f}"),
                "RVOL": threshold_cell(result.rvol, rvol_ok, "{:.2f}x"),
                "Thesis": entry.get("thesis") or "–",
                "Reason": result.reason,
                "_result": result,
            }
        )

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("Status", ascending=False, kind="stable").reset_index(drop=True)

    enter_count = sum(1 for r in rows if r["_result"].status == STATUS_ENTER)
    disqualified_count = sum(1 for r in rows if r["_result"].status == STATUS_DISQUALIFIED)
    waiting_count = len(rows) - enter_count - disqualified_count

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Monitored", len(rows))
    col2.metric(f"{GREEN} Actionable (ENTER)", enter_count)
    col3.metric(f"{YELLOW} Near Setup", waiting_count)
    col4.metric(f"{RED} Disqualified", disqualified_count)

    if not rows:
        st.info("No tickers match the current sector filter.")
        return

    active_pill = st.pills("Filter", FILTER_PILLS, default="All", label_visibility="collapsed")
    if active_pill == f"{GREEN} ENTER":
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
            "Pipeline": st.column_config.TextColumn(help="Trend → Regime → Location → Oscillator → Confirmation"),
            "Thesis": st.column_config.TextColumn(width="medium"),
            "Reason": st.column_config.TextColumn(width="medium"),
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
