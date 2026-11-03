"""SwingDeck - Momentum Continuation overview (docs/SwingDeck_V2_Refactoring_Spec.md).

Reads precomputed results.json (written nightly by scripts/refresh_job.py via
GitHub Actions - see docs/SPEC.md Section 8). Never calls Tiingo directly, and -
unlike the archived V1 app - never re-runs the rules engine either: there are no
more interactive thresholds, so every row's status is exactly what refresh_job.py
already decided. app.py is a pure view layer over results.json + status_mapping.
"""

import json
import os
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from src.status_mapping import (
    GROUP_DEVELOPING,
    GROUP_OTHER,
    GROUP_QUALIFIED,
    GROUP_UNKNOWN,
    QUALIFIED_REASON,
    badge_text,
    other_monitored_reason,
    ui_group_for_status,
)

REPO_ROOT = os.path.dirname(__file__)
RESULTS_PATH = os.path.join(REPO_ROOT, "results.json")
WATCHLIST_PATH = os.path.join(REPO_ROOT, "watchlist.json")

DOT_GREEN, DOT_AMBER, DOT_NEUTRAL = "\U0001f7e2", "\U0001f7e1", "⚪"
GROUP_DOT = {GROUP_QUALIFIED: DOT_GREEN, GROUP_DEVELOPING: DOT_AMBER, GROUP_OTHER: DOT_NEUTRAL, GROUP_UNKNOWN: DOT_NEUTRAL}

FILTER_SHORTLIST = "shortlist"
FILTER_QUALIFIED = "qualified"
FILTER_DEVELOPING = "developing"
FILTER_OTHER = "other"

st.set_page_config(page_title="SwingDeck", layout="wide")

CUSTOM_CSS = """
<style>
.setup-label {
    display: inline-block;
    font-weight: 600;
    font-size: 1.05rem;
    color: #22c55e;
    border-bottom: 2px solid #22c55e;
    padding-bottom: 0.35rem;
    margin-bottom: 0.25rem;
}
div[data-testid="stVerticalBlockBorderWrapper"] div[data-testid="stMarkdownContainer"] p {
    margin-bottom: 0;
}
.sd-row-ticker {
    font-weight: 700;
    font-size: 1.05rem;
}
.sd-row-name {
    color: rgba(250, 250, 250, 0.6);
    font-size: 0.95rem;
}
.sd-row-reason {
    color: rgba(250, 250, 250, 0.75);
    font-size: 0.9rem;
    margin-top: 0.15rem;
}
.sd-badge {
    display: inline-block;
    background: rgba(245, 158, 11, 0.18);
    color: #f59e0b;
    border-radius: 4px;
    padding: 0.05rem 0.4rem;
    font-weight: 600;
    font-size: 0.78rem;
    letter-spacing: 0.02em;
    margin-right: 0.4rem;
}
.sd-badge-invalid {
    background: rgba(148, 163, 184, 0.18);
    color: rgba(250, 250, 250, 0.75);
}
.sd-dot {
    font-size: 0.7rem;
}
</style>
"""


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


def staleness_banner(generated_at: str | None) -> None:
    """Demoted operational safety net (not part of either new spec doc, not
    forbidden by them either) - the only signal that the nightly GitHub Actions
    refresh might be failing, for a single-user app with no other monitoring.
    Counts only weekdays so a Monday check doesn't false-flag the Fri->Mon gap."""
    if not generated_at:
        return
    try:
        generated = date.fromisoformat(generated_at)
    except ValueError:
        return

    today = date.today()
    missed_trading_days = len(pd.bdate_range(generated + timedelta(days=1), today)) if generated < today else 0
    if missed_trading_days >= 2:
        st.warning(
            f"Refresh is {missed_trading_days} trading day(s) behind (last: {generated_at}) - "
            "check the GitHub Actions tab."
        )
    elif missed_trading_days == 1:
        st.caption(f"Today's refresh hasn't landed yet - last data is from {generated_at}.")


def render_header() -> None:
    left, right = st.columns([5, 1])
    with left:
        st.markdown("### SwingDeck")
    with right:
        if st.button("⚙️ Settings", use_container_width=True):
            open_settings_dialog()


@st.dialog("Settings")
def open_settings_dialog() -> None:
    st.caption("Nothing configurable here yet - thresholds are fixed per the Momentum Continuation spec.")
    if st.button("Log out"):
        st.session_state["authenticated"] = False
        st.rerun()


def render_session_line(evaluated_session: str | None, generated_at: str | None) -> None:
    st.markdown('<span class="setup-label">Momentum Continuation</span>', unsafe_allow_html=True)
    session_text = evaluated_session or "unavailable"
    st.caption(f"EOD data: {session_text}", help=f"Last refreshed: {generated_at or 'unknown'}")


def render_count_cards(counts: dict) -> str:
    """Three count cards acting as filter buttons. Returns the (possibly updated)
    filter_mode. Counts are always computed from the full result set, independent
    of the current filter - see the caller."""
    mode = st.session_state.get("filter_mode", FILTER_SHORTLIST)

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button(
            f"{DOT_GREEN} Qualified\n\n### {counts[GROUP_QUALIFIED]}",
            use_container_width=True,
            type="primary" if mode in (FILTER_SHORTLIST, FILTER_QUALIFIED) else "secondary",
        ):
            mode = FILTER_QUALIFIED
    with col2:
        if st.button(
            f"{DOT_AMBER} Developing\n\n### {counts[GROUP_DEVELOPING]}",
            use_container_width=True,
            type="primary" if mode in (FILTER_SHORTLIST, FILTER_DEVELOPING) else "secondary",
        ):
            mode = FILTER_DEVELOPING
    with col3:
        if st.button(
            f"{DOT_NEUTRAL} Other monitored\n\n### {counts[GROUP_OTHER] + counts[GROUP_UNKNOWN]}",
            use_container_width=True,
            type="primary" if mode == FILTER_OTHER else "secondary",
        ):
            mode = FILTER_OTHER

    if mode != FILTER_SHORTLIST:
        if st.button("← Show shortlist"):
            mode = FILTER_SHORTLIST

    st.session_state["filter_mode"] = mode
    return mode


def render_row(result: dict) -> None:
    group = result["ui_group"]
    dot = GROUP_DOT.get(group, DOT_NEUTRAL)
    name = result.get("name")
    name_html = f'<span class="sd-row-name"> &nbsp;{name}</span>' if name else ""

    if group == GROUP_QUALIFIED:
        reason_html = f'<div class="sd-row-reason">{QUALIFIED_REASON}</div>'
    elif group == GROUP_DEVELOPING:
        badges = result.get("developing_badges") or []
        pills = "".join(f'<span class="sd-badge">{badge_text(b)}</span>' for b in badges)
        reason_html = f'<div class="sd-row-reason">{pills}</div>'
    elif group == GROUP_OTHER:
        reason_text, is_invalid = other_monitored_reason(result)
        css_class = "sd-badge sd-badge-invalid" if is_invalid else ""
        reason_html = (
            f'<div class="sd-row-reason"><span class="{css_class}">{reason_text}</span></div>'
            if is_invalid
            else f'<div class="sd-row-reason">{reason_text}</div>'
        )
    else:
        reason_html = f'<div class="sd-row-reason">Unrecognized status: {result.get("status")}</div>'

    with st.container(border=True):
        top_left, top_right = st.columns([8, 1])
        with top_left:
            st.markdown(
                f'<span class="sd-row-ticker">{result["ticker"]}</span>{name_html}', unsafe_allow_html=True
            )
            st.markdown(reason_html, unsafe_allow_html=True)
        with top_right:
            st.markdown(f'<div class="sd-dot" style="text-align:right">{dot}</div>', unsafe_allow_html=True)


def render_section(title: str, rows: list[dict], empty_message: str) -> None:
    st.markdown(f"**{title} ({len(rows)})**")
    if not rows:
        st.caption(empty_message)
        return
    for result in rows:
        render_row(result)


def main() -> None:
    if not check_password():
        st.stop()

    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

    try:
        data = load_results(RESULTS_PATH)
    except (FileNotFoundError, json.JSONDecodeError):
        render_header()
        st.error("No scan data available yet - the refresh job hasn't produced results.json.")
        return

    if not data.get("generated_at") and not data.get("results"):
        render_header()
        st.info("No data yet - waiting for the first scheduled refresh to run.")
        return

    render_header()
    staleness_banner(data.get("generated_at"))
    render_session_line(data.get("evaluated_session"), data.get("generated_at"))

    results = data.get("results", [])
    errors = data.get("errors", [])

    for r in results:
        r.setdefault("ui_group", ui_group_for_status(r.get("status")))

    by_group: dict[str, list[dict]] = {GROUP_QUALIFIED: [], GROUP_DEVELOPING: [], GROUP_OTHER: [], GROUP_UNKNOWN: []}
    for r in results:
        by_group.setdefault(r["ui_group"], []).append(r)

    counts = {g: len(rows) for g, rows in by_group.items()}
    for g in (GROUP_QUALIFIED, GROUP_DEVELOPING, GROUP_OTHER, GROUP_UNKNOWN):
        counts.setdefault(g, 0)

    if errors:
        st.warning(f"Couldn't fetch data for {len(errors)} ticker(s) this run - see details below.")

    evaluated_session = data.get("evaluated_session")
    off_session = [
        r for r in results if evaluated_session and r.get("evaluated_session") and r["evaluated_session"] != evaluated_session
    ]
    if off_session:
        tickers = ", ".join(r["ticker"] for r in off_session)
        st.info(f"{len(off_session)} ticker(s) evaluated on a different session than {evaluated_session}: {tickers}")

    mode = render_count_cards(counts)

    qualified_rows = by_group.get(GROUP_QUALIFIED, [])
    developing_rows = by_group.get(GROUP_DEVELOPING, [])
    other_rows = by_group.get(GROUP_OTHER, []) + by_group.get(GROUP_UNKNOWN, [])

    st.divider()

    if mode == FILTER_QUALIFIED:
        render_section("Qualified", qualified_rows, "No qualified stocks in this scan.")
    elif mode == FILTER_DEVELOPING:
        render_section("Developing", developing_rows, "No developing stocks in this scan.")
    elif mode == FILTER_OTHER:
        render_section("Other monitored", other_rows, "Nothing in Other monitored.")
    else:
        if not qualified_rows and not developing_rows:
            st.info("No qualified or developing stocks in this scan.")
            if other_rows:
                st.caption(f"{len(other_rows)} ticker(s) in Other monitored.")
        else:
            if not qualified_rows:
                st.caption("No qualified stocks in this scan.")
            else:
                render_section("Qualified", qualified_rows, "No qualified stocks in this scan.")
            if not developing_rows:
                st.caption("No developing stocks in this scan.")
            else:
                render_section("Developing", developing_rows, "No developing stocks in this scan.")

    if errors:
        with st.expander(f"Couldn't fetch {len(errors)} ticker(s)"):
            error_df = pd.DataFrame([{"Ticker": e["ticker"], "Reason": e["error"]} for e in errors])
            st.dataframe(error_df, width="stretch", hide_index=True)


main()
