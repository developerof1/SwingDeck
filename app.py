"""SwingDeck - watchlist-first overview (docs/trading_setup_accordion_spec.md).

Reads precomputed results/<watchlist_id>.json (written nightly by scripts/refresh_job.py
via GitHub Actions for every watchlist under watchlists/ - see docs/SPEC.md Section 8).
Never calls Tiingo directly, and never re-runs any rules engine either: there are no more
interactive thresholds, so every row's status is exactly what refresh_job.py already decided.
app.py is a pure view layer: a watchlist selector (src.watchlist_store) over watchlists/*.json,
then one accordion per setup registered in src.setups, each rendering that setup's own slice
of the selected watchlist's results via its own reason_for().
"""

import json
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from src.setups import list_setups
from src.setups.base import Setup
from src.status_mapping import GROUP_DEVELOPING, GROUP_OTHER, GROUP_QUALIFIED, GROUP_UNKNOWN
from src.watchlist_store import discover_watchlists

REPO_ROOT = os.path.dirname(__file__)
RESULTS_DIR = os.path.join(REPO_ROOT, "results")
STATE_DIR = os.path.join(REPO_ROOT, "state")


def results_path(watchlist_id: str) -> str:
    return os.path.join(RESULTS_DIR, f"{watchlist_id}.json")


def status_path() -> str:
    return os.path.join(STATE_DIR, "refresh_status.json")

# America/Chicago correctly resolves to CST or CDT depending on the date
# (zoneinfo applies US DST rules automatically) - refresh_job.py stores
# generated_at in UTC; this is purely a display-time conversion.
DISPLAY_TZ = ZoneInfo("America/Chicago")

DOT_GREEN, DOT_AMBER, DOT_NEUTRAL = "\U0001f7e2", "\U0001f7e1", "⚪"
GROUP_DOT = {GROUP_QUALIFIED: DOT_GREEN, GROUP_DEVELOPING: DOT_AMBER, GROUP_OTHER: DOT_NEUTRAL, GROUP_UNKNOWN: DOT_NEUTRAL}

st.set_page_config(page_title="SwingDeck", layout="wide")

CUSTOM_CSS = """
<style>
div[data-testid="stVerticalBlockBorderWrapper"] div[data-testid="stMarkdownContainer"] p {
    margin-bottom: 0;
}
/* Pin the bordered row box's own padding explicitly (only rows use
   st.container(border=True) in this app), instead of leaving the top/bottom
   whitespace around the row content to Streamlit's default container
   padding - same reasoning as the row-internal reset below: explicit beats
   implicit for anything the mockup needs to look deliberate. */
div[data-testid="stVerticalBlockBorderWrapper"] {
    padding: 0.9rem 1.1rem !important;
}
/* Row spacing: Streamlit's default element spacing isn't only applied to
   *direct* children of stMarkdownContainer - it was still reaching .sd-row-top
   and .sd-row-reason as nested descendants even after wrapping them in one
   .sd-row block, overriding the explicit gap below. Rather than guess at
   Streamlit's exact selector again, every element under .sd-row is reset to
   zero margin/padding here (no !important - deliberately losing to Streamlit's
   own rules is fine, that's not what sets the real gap), and then the one gap
   we actually want is added back with !important, which outranks any
   non-!important rule regardless of specificity or nesting depth. */
div[data-testid="stMarkdownContainer"] .sd-row,
div[data-testid="stMarkdownContainer"] .sd-row * {
    margin: 0;
    padding: 0;
}
.sd-row-top {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 0.6rem;
}
.sd-row-left {
    display: flex;
    align-items: baseline;
    gap: 0.5rem;
    flex-wrap: wrap;
}
.sd-row-ticker {
    font-weight: 700;
    font-size: 1.25rem;
    line-height: 1.3;
    color: #fafafa !important;
}
.sd-row-name {
    color: rgba(250, 250, 250, 0.6) !important;
    font-size: 1.05rem;
    line-height: 1.3;
}
.sd-row-reason, .sd-row-reason * {
    color: rgba(250, 250, 250, 0.75) !important;
    font-size: 1rem;
    line-height: 1.3;
}
.sd-row-reason {
    margin-top: 0.45rem !important;
}
.sd-badge {
    display: inline-block;
    background: rgba(245, 158, 11, 0.18);
    color: #f59e0b !important;
    border-radius: 4px;
    padding: 0.05rem 0.4rem;
    font-weight: 600;
    font-size: 0.85rem;
    letter-spacing: 0.02em;
    margin-right: 0.4rem;
}
.sd-badge-invalid {
    background: rgba(148, 163, 184, 0.18);
    color: rgba(250, 250, 250, 0.75) !important;
}
.sd-dot {
    font-size: 0.75rem;
    flex-shrink: 0;
}
.sd-section-header {
    font-size: 1rem;
    margin-bottom: 0.5rem;
}
.sd-section-spaced {
    margin-top: 1.5rem;
}
/* Guarantees the same gap above the Other-monitored/errors toggles as the gap between
   the Qualified and Developing sections above (.sd-section-spaced) - an empty Developing
   section's "No developing stocks..." caption has no element of its own to carry a
   margin-top, so this is a dedicated spacer rather than reusing that class directly. */
.sd-toggle-spacer {
    margin-top: 1.5rem;
}

/* Setup accordion header: st.expander's label is plain/markdown text only (confirmed against
   Streamlit's own source - no raw HTML, so no way to make part of it big and part of it
   right-aligned within one label string). Built by hand instead: an HTML header row (flex,
   title left/pills right, title sized to match the "### SwingDeck" page header - 28px/600,
   confirmed via computed style) with a fully transparent st.button absolutely covering it as
   the click target, toggling st.session_state rather than st.expander's own expanded state -
   the same "real HTML box + invisible button overlay" technique proven out for the count
   cards earlier (see git history), except position:absolute/inset:0 here instead of a fixed
   negative-margin offset, so it still covers the header correctly if the title ever wraps to
   a second line at a narrow width. */
div[class*="st-key-sd_setup_header_"] {
    position: relative;
}
.sd-setup-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    border: 1px solid rgba(148, 163, 184, 0.25);
    border-radius: 10px;
    background: rgba(255, 255, 255, 0.02);
    padding: 0.9rem 1.1rem;
    margin-bottom: 1rem;
}
.sd-setup-header-left {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    min-width: 0;
}
.sd-setup-chevron {
    font-size: 0.9rem;
    color: rgba(250, 250, 250, 0.6) !important;
    transition: transform 0.15s;
    flex-shrink: 0;
}
.sd-setup-chevron-open {
    transform: rotate(90deg);
}
.sd-setup-title {
    font-size: 28px;
    font-weight: 600;
    line-height: 33.6px;
    color: #fafafa !important;
}
.sd-setup-pills {
    font-size: 1rem;
    color: rgba(250, 250, 250, 0.85) !important;
    white-space: nowrap;
    flex-shrink: 0;
}
/* The button's own element-container div (Streamlit applies the "sd_setup_toggle_*" key's
   class here) is what actually needs position:absolute/inset:0 - Streamlit's own baseline
   CSS already makes every element-container `position: relative`, which is the *nearest*
   positioned ancestor for anything placed inside it. Putting the absolute positioning one
   level deeper, on div[data-testid="stButton"] itself, anchors inset:0 to that already-
   relative, fit-content-width parent instead of the full header (confirmed via computed
   styles: the button ended up sized to its own ~16x0px wrapper). Width/height auto
   overrides Streamlit's default fit-content sizing on this element, now that inset:0 is
   doing the actual sizing instead. */
div[class*="st-key-sd_setup_toggle_"] {
    position: absolute !important;
    inset: 0;
    margin: 0 !important;
    width: auto !important;
    height: auto !important;
}
div[class*="st-key-sd_setup_toggle_"] div[data-testid="stButton"],
div[class*="st-key-sd_setup_toggle_"] div[data-testid="stButton"] > button {
    width: 100%;
    height: 100%;
}
div[class*="st-key-sd_setup_toggle_"] div[data-testid="stButton"] > button {
    min-height: unset !important;
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    opacity: 0 !important;
    cursor: pointer;
    padding: 0 !important;
    margin: 0 !important;
}

/* Watchlist selector bar: shaded/bordered treatment wrapping the label + dropdown +
   source badge as one visual unit instead of three loose elements - matches the
   mockup's bordered selector row. */
div[class*="st-key-sd_watchlist_bar"] {
    border: 1px solid rgba(148, 163, 184, 0.25);
    border-radius: 10px;
    background: rgba(255, 255, 255, 0.02);
    padding: 0.7rem 1.1rem;
    margin-bottom: 1rem;
}
div[class*="st-key-sd_watchlist_bar"] div[data-testid="stHorizontalBlock"] {
    align-items: center;
}
/* Streamlit nests every st.markdown() output inside several of its own wrapper divs with
   flex/sizing behavior that doesn't reliably hand our content the row's true vertical
   center (confirmed by directly measuring the rendered page - the wrapper chain's reported
   heights were inconsistent between two otherwise-identical renders, so this isn't worth
   chasing through Streamlit's internal, version-specific DOM). Pragmatic fix instead of a
   theoretical one: nudge both up by the empirically-measured offset so their text lines up
   with the selectbox's real center - re-verify with Playwright (see scratchpad
   inspect_layout.py) if this ever drifts after a Streamlit upgrade. */
.sd-watchlist-label,
.sd-watchlist-source {
    margin-top: -16px;
}
.sd-watchlist-label {
    font-weight: 600;
    font-size: 0.95rem;
    color: rgba(250, 250, 250, 0.9) !important;
}
.sd-watchlist-source {
    font-size: 0.85rem;
    color: rgba(250, 250, 250, 0.55) !important;
    text-align: right;
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
    # Deliberately uncached: each results/<watchlist_id>.json is precomputed server-side and
    # only ever changes via a git push + redeploy, but caching here risks showing stale data
    # across a redeploy that doesn't fully restart the process. The file is tiny,
    # so re-reading it on every rerun costs nothing.
    with open(path) as f:
        return json.load(f)


def _parse_generated_at(raw: str | None) -> datetime | None:
    """generated_at may be a bare date (old pre-timestamp schema)
    or a full ISO timestamp (current schema) - accept either."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        pass
    try:
        return datetime.combine(date.fromisoformat(raw), datetime.min.time())
    except ValueError:
        return None


def staleness_banner(generated_at: str | None) -> None:
    """Demoted operational safety net (not part of either new spec doc, not
    forbidden by them either) - the only signal that the nightly GitHub Actions
    refresh might be failing, for a single-user app with no other monitoring.
    Counts only weekdays so a Monday check doesn't false-flag the Fri->Mon gap.
    Only genuinely concerning staleness (refresh missed outright) interrupts with
    a banner; "today's refresh just hasn't landed yet" is normal every morning
    and belongs in the EOD line's own context, not a second competing line."""
    dt = _parse_generated_at(generated_at)
    if dt is None:
        return

    generated = dt.date()
    today = date.today()
    missed_trading_days = len(pd.bdate_range(generated + timedelta(days=1), today)) if generated < today else 0
    if missed_trading_days >= 2:
        st.warning(
            f"Refresh is {missed_trading_days} trading day(s) behind (last: {generated.isoformat()}) - "
            "check the GitHub Actions tab."
        )


def load_refresh_status() -> dict | None:
    # Deliberately uncached for the same reason as load_results - small file, only
    # changes via git push + redeploy, re-reading costs nothing.
    try:
        with open(status_path()) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def refresh_health_banner(status: dict | None) -> None:
    """Complements staleness_banner rather than replacing it: staleness_banner catches
    "nothing has run in days" (the workflow stopped firing), while this catches "it ran
    last night but didn't finish" - refresh_job.py now drains a queue across several hourly
    firings, so a per-watchlist timestamp alone no longer says whether *everything* got
    refreshed. This is a verdict on the most recently *finished* night (see
    scripts/refresh_job.py's finalize_night), not a live in-progress counter, so it doesn't
    read as alarming while tonight's queue is still legitimately mid-drain."""
    if status is None:
        return
    total, completed = status.get("total", 0), status.get("completed", 0)
    if status.get("status") == "complete":
        st.caption(f"Last night's refresh completed ({completed}/{total} tickers).")
    else:
        st.warning(
            f"Last night's refresh was incomplete - {completed}/{total} ticker(s) updated before it was abandoned. "
            "Check the Actions tab."
        )


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


def _format_session_date(session: str | None) -> str:
    """Formats an evaluated_session ISO date ("2026-11-03") the same human style
    as the refresh timestamp ("Nov 03, 2026"), so the two halves of the EOD line
    don't mix a raw-ISO date with a human-formatted one."""
    if not session:
        return "unavailable"
    try:
        return date.fromisoformat(session).strftime("%b %d, %Y")
    except ValueError:
        return session


def _bucket_by_group(results: list[dict]) -> dict[str, list[dict]]:
    by_group: dict[str, list[dict]] = {GROUP_QUALIFIED: [], GROUP_DEVELOPING: [], GROUP_OTHER: [], GROUP_UNKNOWN: []}
    for r in results:
        by_group.setdefault(r.get("ui_group", GROUP_UNKNOWN), []).append(r)
    return by_group


def _toggle_setup_open(open_key: str) -> None:
    st.session_state[open_key] = not st.session_state.get(open_key, False)


def render_setup_accordion(setup: Setup, setup_data: dict, watchlist_id: str) -> None:
    """One setup's whole section for the active watchlist - header shows live counts even
    while collapsed (so scanning the page tells you what's cooking across every setup without
    opening anything), and auto-expands only if it actually has something to look at. Adding a
    second registered setup gets this same section automatically - nothing here names
    "Momentum Continuation" or assumes there's only one.

    Hand-rolled rather than st.expander (see the .sd-setup-header CSS comment for why) - the
    open/closed state lives in st.session_state instead of st.expander's own, seeded once per
    (watchlist, setup) pair from whether that setup actually has anything to look at *in this
    watchlist*, and left alone after that so a user's manual toggle isn't overwritten by a
    later rerun. Keying by watchlist_id too (not just setup.id) is what makes switching
    watchlists re-evaluate the auto-expand default instead of reusing whatever open/closed
    state a previously-viewed watchlist happened to leave behind."""
    results = setup_data.get("results", [])
    errors = setup_data.get("errors", [])

    by_group = _bucket_by_group(results)
    qualified_rows = by_group[GROUP_QUALIFIED]
    developing_rows = by_group[GROUP_DEVELOPING]
    other_rows = by_group[GROUP_OTHER] + by_group[GROUP_UNKNOWN]
    q, d, o = len(qualified_rows), len(developing_rows), len(other_rows)

    open_key = f"setup_open_{watchlist_id}_{setup.id}"
    if open_key not in st.session_state:
        st.session_state[open_key] = q > 0 or d > 0
    is_open = st.session_state[open_key]

    chevron_class = "sd-setup-chevron sd-setup-chevron-open" if is_open else "sd-setup-chevron"
    with st.container(key=f"sd_setup_header_{watchlist_id}_{setup.id}"):
        st.html(
            '<div class="sd-setup-header">'
            '<div class="sd-setup-header-left">'
            f'<span class="{chevron_class}">▶</span>'
            f'<span class="sd-setup-title">{setup.label}</span>'
            "</div>"
            '<div class="sd-setup-pills">'
            f"{DOT_GREEN} {q} Qualified · {DOT_AMBER} {d} Developing · {DOT_NEUTRAL} {o} Monitored"
            "</div>"
            "</div>"
        )
        st.button(
            setup.label,
            # Deliberately NOT prefixed "sd_setup_header_" like the container key above -
            # Streamlit applies this key's own class to the button's inner wrapper div, and a
            # shared prefix made the CSS wildcard below match that wrapper too, handing it
            # `position: relative` and anchoring the button's `inset: 0` to its own tiny
            # fit-content box instead of the full header (confirmed via a DOM dump: the
            # button ended up ~16x0px). A non-overlapping key name avoids the collision.
            key=f"sd_setup_toggle_{watchlist_id}_{setup.id}",
            on_click=_toggle_setup_open,
            args=(open_key,),
        )

    if not is_open:
        return

    staleness_banner(setup_data.get("generated_at"))
    render_session_line(setup_data.get("evaluated_session"), setup_data.get("generated_at"))

    if errors:
        st.warning(f"Couldn't fetch data for {len(errors)} ticker(s) this run - see details below.")

    evaluated_session = setup_data.get("evaluated_session")
    off_session = [
        r for r in results if evaluated_session and r.get("evaluated_session") and r["evaluated_session"] != evaluated_session
    ]
    if off_session:
        tickers = ", ".join(r["ticker"] for r in off_session)
        st.info(f"{len(off_session)} ticker(s) evaluated on a different session than {evaluated_session}: {tickers}")

    render_section("Qualified", qualified_rows, "No qualified stocks in this scan.", setup)
    render_section("Developing", developing_rows, "No developing stocks in this scan.", setup, spaced=True)

    st.markdown('<div class="sd-toggle-spacer"></div>', unsafe_allow_html=True)

    # A plain disclosure, not a nested st.expander - Streamlit doesn't allow expanders
    # inside expanders, and (now) doesn't allow one inside this hand-rolled accordion either.
    if st.toggle(f"Other monitored ({len(other_rows)})", key=f"sd_other_{watchlist_id}_{setup.id}"):
        if not other_rows:
            st.caption("Nothing in Other monitored.")
        else:
            for result in other_rows:
                render_row(result, setup)

    if errors:
        if st.toggle(f"Couldn't fetch {len(errors)} ticker(s)", key=f"sd_errors_{watchlist_id}_{setup.id}"):
            error_df = pd.DataFrame([{"Ticker": e["ticker"], "Reason": e["error"]} for e in errors])
            st.dataframe(error_df, width="stretch", hide_index=True)


def render_watchlist_selector(watchlists: list[dict]) -> str:
    """Label + dropdown + "source: <file>" badge, all inline in one shaded/bordered row
    (docs/multi_watchlist_selector_specs.md 3.1 #3 - the mockup shows this as a single
    bordered unit, not three loose elements). Defaults to the first (alphabetically-by-name)
    watchlist from discover_watchlists() and persists the pick in st.session_state across
    reruns.

    filter_mode=None on the selectbox itself (a Streamlit selectbox option, unrelated to any
    app-level filtering) turns off BaseWeb's default type-to-filter behavior - click-to-open/
    click-to-pick only, no typing - since with few watchlists a searchable combobox reads as
    an editable text box.
    """
    ids = [w["id"] for w in watchlists]
    by_id = {w["id"]: w for w in watchlists}

    if st.session_state.get("selected_watchlist_id") not in ids:
        st.session_state["selected_watchlist_id"] = ids[0]

    with st.container(key="sd_watchlist_bar"):
        label_col, select_col, source_col = st.columns([1.3, 2.4, 3.3])
        with label_col:
            st.markdown('<div class="sd-watchlist-label">Active Watchlist</div>', unsafe_allow_html=True)
        with select_col:
            selected = st.selectbox(
                "Active Watchlist",
                options=ids,
                format_func=lambda wid: f"{by_id[wid]['name']} ({by_id[wid]['ticker_count']} tickers)",
                key="selected_watchlist_id",
                label_visibility="collapsed",
                filter_mode=None,
            )
        with source_col:
            st.markdown(f'<div class="sd-watchlist-source">source: {selected}.json</div>', unsafe_allow_html=True)

    return selected


def render_session_line(evaluated_session: str | None, generated_at: str | None) -> None:
    session_text = _format_session_date(evaluated_session)
    dt = _parse_generated_at(generated_at)
    if dt:
        # Only tz-aware datetimes (current schema: a full UTC timestamp) can
        # be converted - the legacy bare-date fallback parses to a naive
        # datetime with no real time-of-day, so it's shown as-is rather than
        # mislabeled with a timezone it was never actually recorded in.
        local_dt = dt.astimezone(DISPLAY_TZ) if dt.tzinfo else dt
        tz_label = local_dt.tzname() if dt.tzinfo else ""
        refreshed_text = f"{local_dt.strftime('%b %d, %Y, %I:%M %p')} {tz_label}".strip()
    else:
        refreshed_text = "unknown"
    # Shown inline, matching the approved mockup - EOD date is the prominent half,
    # refresh time is secondary (smaller/muted via st.caption) but still visible
    # on the same line rather than hidden behind an interaction.
    st.caption(f"EOD data: {session_text}  ·  Refreshed: {refreshed_text}")


#  Plain `class="sd-row-reason"` text was rendering link-blue despite the
#  !important class rule - rather than keep chasing Streamlit's internal CSS
#  cascade blind, every text node here also carries an inline `style` color,
#  which no external stylesheet (Streamlit's or ours) can outrank.
_REASON_STYLE = 'style="color: rgba(250, 250, 250, 0.75) !important;"'


def render_row(result: dict, setup: Setup) -> None:
    """Dispatches to the active setup's own reason_for() rather than hardcoding any one
    setup's statuses/wording here - this is what lets a second registered setup show up with
    zero changes to this function."""
    group = result.get("ui_group", GROUP_UNKNOWN)
    dot = GROUP_DOT.get(group, DOT_NEUTRAL)
    name = result.get("name")
    name_html = f'<span class="sd-row-name" {_REASON_STYLE}>{name}</span>' if name else ""

    reason_text, is_invalid = setup.reason_for(result)
    if is_invalid:
        reason_html = f'<div class="sd-row-reason"><span class="sd-badge sd-badge-invalid">{reason_text}</span></div>'
    else:
        reason_html = f'<div class="sd-row-reason" {_REASON_STYLE}>{reason_text}</div>'

    row_html = (
        '<div class="sd-row">'
        '<div class="sd-row-top">'
        '<div class="sd-row-left">'
        f'<span class="sd-row-ticker" style="color: #fafafa !important;">{result["ticker"]}</span>'
        f"{name_html}"
        "</div>"
        f'<div class="sd-dot">{dot}</div>'
        "</div>"
        f"{reason_html}"
        "</div>"
    )
    with st.container(border=True):
        # st.html(), not st.markdown() - renders raw HTML with none of
        # Streamlit's own markdown-container wrapping/spacing. Two rounds of
        # !important overrides against that wrapping still wasn't enough, so
        # this sidesteps the fight entirely instead of guessing at Streamlit's
        # internal stylesheet a third time.
        st.html(row_html)


def render_section(title: str, rows: list[dict], empty_message: str, setup: Setup, spaced: bool = False) -> None:
    """`spaced=True` adds extra top margin before the header - used for a
    second section (e.g. Developing) directly following another section's
    rows, so it doesn't read as cramped against the last row above it."""
    css_class = "sd-section-header sd-section-spaced" if spaced else "sd-section-header"
    st.markdown(f'<div class="{css_class}"><strong>{title} ({len(rows)})</strong></div>', unsafe_allow_html=True)
    if not rows:
        st.caption(empty_message)
        return
    for result in rows:
        render_row(result, setup)


def main() -> None:
    if not check_password():
        st.stop()

    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

    render_header()
    refresh_health_banner(load_refresh_status())

    watchlists = discover_watchlists()
    if not watchlists:
        st.error("No watchlists configured - add a file under watchlists/.")
        return

    selected_watchlist_id = render_watchlist_selector(watchlists)

    try:
        data = load_results(results_path(selected_watchlist_id))
    except (FileNotFoundError, json.JSONDecodeError):
        st.error("No scan data available yet for this watchlist - the refresh job hasn't produced results.")
        return

    setups_doc = data.get("setups", {})
    if not setups_doc:
        st.info("No data yet - waiting for the first scheduled refresh to run.")
        return

    st.divider()
    for setup in list_setups():
        render_setup_accordion(setup, setups_doc.get(setup.id, {}), selected_watchlist_id)


main()
