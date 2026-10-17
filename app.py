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
    evaluate_stock,
)

REPO_ROOT = os.path.dirname(__file__)
RESULTS_PATH = os.path.join(REPO_ROOT, "results.json")

STATUS_BADGE = {
    STATUS_ENTER: "\U0001f7e2 ENTER",
    STATUS_DISQUALIFIED: "\U0001f534 DISQUALIFIED: Trend Failure",
}

st.set_page_config(page_title="SwingDeck", layout="wide")


def check_password() -> bool:
    if st.session_state.get("authenticated"):
        return True

    st.title("SwingDeck")
    password = st.text_input("Password", type="password")
    if st.button("Log in") or password:
        if password == st.secrets.get("APP_PASSWORD"):
            st.session_state["authenticated"] = True
            st.rerun()
        elif password:
            st.error("Incorrect password")
    return False


@st.cache_data
def load_results(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def badge(status: str) -> str:
    return STATUS_BADGE.get(status, f"\U0001f7e1 {status}")


def live_eval(entry: dict, location_thresh: float, rvol_thresh: float):
    snapshot_df = pd.DataFrame(entry["snapshot"])
    snapshot_df["date"] = pd.to_datetime(snapshot_df["date"])
    return evaluate_stock(entry["ticker"], snapshot_df, location_thresh, rvol_thresh)


def main() -> None:
    if not check_password():
        st.stop()

    data = load_results(RESULTS_PATH)

    if not data.get("generated_at"):
        st.title("SwingDeck")
        st.info("No data yet - waiting for the first scheduled refresh to run.")
        return

    st.sidebar.header("Parameters")
    location_thresh = st.sidebar.slider(
        "Location Threshold %", min_value=0.5, max_value=3.0, value=DEFAULT_LOCATION_THRESHOLD_PCT, step=0.1
    )
    rvol_thresh = st.sidebar.slider(
        "Min RVOL Threshold", min_value=1.0, max_value=2.0, value=DEFAULT_RVOL_THRESHOLD, step=0.05
    )

    all_sectors = sorted({r["sector"] for r in data["results"] if r.get("sector")})
    selected_sectors = st.sidebar.multiselect("Sectors", options=all_sectors, default=all_sectors)

    rows = []
    for entry in data["results"]:
        if entry.get("sector") not in selected_sectors:
            continue
        result = live_eval(entry, location_thresh, rvol_thresh)
        rows.append(
            {
                "Ticker": entry["ticker"],
                "Sector": entry.get("sector"),
                "Status": badge(result.status),
                "Current Price": result.current_price,
                "Regime": result.regime,
                "Dist to 21-EMA %": result.dist_to_ema21_pct,
                "Stoch %K": result.stoch_k,
                "Stoch %D": result.stoch_d,
                "RVOL": result.rvol,
                "Suggested Entry": result.entry,
                "Stop Loss": result.stop,
                "Target Price": result.target,
                "R:R Ratio": result.rr,
                "_thesis": entry.get("thesis"),
                "_reason": result.reason,
            }
        )

    df = pd.DataFrame(rows)

    st.title("SwingDeck")
    st.caption(f"Last refreshed: {data['generated_at']}")

    enter_count = sum(1 for r in rows if r["Status"].startswith("\U0001f7e2"))
    disqualified_count = sum(1 for r in rows if r["Status"].startswith("\U0001f534"))
    waiting_count = len(rows) - enter_count - disqualified_count

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Monitored", len(rows))
    col2.metric("\U0001f7e2 Actionable (ENTER)", enter_count)
    col3.metric("\U0001f7e1 Near Setup", waiting_count)
    col4.metric("\U0001f534 Disqualified", disqualified_count)

    if data.get("errors"):
        with st.expander(f"⚠️ {len(data['errors'])} ticker(s) failed to refresh"):
            for err in data["errors"]:
                st.write(f"**{err['ticker']}**: {err['error']}")

    display_df = df.drop(columns=["_thesis", "_reason"])
    event = st.dataframe(
        display_df,
        width="stretch",
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
    )

    selected_rows = event.selection.rows if event and event.selection else []
    if selected_rows:
        selected = df.iloc[selected_rows[0]]
        with st.expander(f"Details: {selected['Ticker']}", expanded=True):
            st.write(f"**Thesis:** {selected['_thesis']}")
            st.write(f"**Reason:** {selected['_reason']}")


main()
