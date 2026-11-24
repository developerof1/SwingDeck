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
import time
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st

from src import pins, user_store
from src.setups import list_setups
from src.setups.base import Setup
from src.status_groups import GROUP_DEVELOPING, GROUP_OTHER, GROUP_QUALIFIED, GROUP_UNKNOWN
from src.watchlist_store import all_watchlist_tickers, discover_watchlists, load_watchlist

REPO_ROOT = os.path.dirname(__file__)
RESULTS_DIR = os.path.join(REPO_ROOT, "results")
STATE_DIR = os.path.join(REPO_ROOT, "state")

GITHUB_REPO = "developerof1/SwingDeck"
REFRESH_WORKFLOW_FILE = "refresh_data.yml"
# A bit over an hour: long enough that a normally-firing hourly cron always beats us to it,
# short enough to backfill a missed firing within about one cycle. Must stay comfortably
# above 1h - firing a catch-up inside the same rolling hour as the last batch risks adding a
# second batch on top of a Tiingo quota the first batch may have already spent (see
# scripts/refresh_job.py's rate-limit handling).
CATCHUP_COOLDOWN_SECONDS = 70 * 60


def results_path(watchlist_id: str) -> str:
    return os.path.join(RESULTS_DIR, f"{watchlist_id}.json")


def load_queue() -> dict | None:
    # queue_path() is defined further down, alongside load_queue_remaining() - fine to
    # reference here since both run well before main() ever calls this at the bottom of the
    # module.
    try:
        with open(queue_path()) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


@st.cache_resource(ttl=CATCHUP_COOLDOWN_SECONDS)
def maybe_trigger_catchup_refresh() -> None:
    """Best-effort backstop for GitHub's unreliable `schedule:` trigger (hourly firings have
    been observed landing 4-12h apart). Purely complementary to the real cron: only fires a
    `workflow_dispatch` when the queue still has work left AND the last batch landed longer
    ago than CATCHUP_COOLDOWN_SECONDS, so it can only ever drain backlog, never pile a second
    batch on top of a healthy queue or a quota the last batch may have already spent.

    @st.cache_resource(ttl=...) is the dedup mechanism: every viewer's page load calls this,
    but Streamlit only actually executes the body once per cooldown window across the whole
    app instance - everyone else in that window gets the cached (no-op) return. This needs no
    extra state file of its own; the committed queue's last_batch_at is the real clock, this
    cache just prevents a burst of redundant API calls before that commit lands.

    The workflow's own `concurrency: group: refresh-queue, cancel-in-progress: false` means a
    dispatch landing while a cron-triggered run is still in flight just queues behind it rather
    than racing it, so there's no need to guard against that case here too.
    """
    token = st.secrets.get("GITHUB_DISPATCH_TOKEN")
    if not token:
        return  # not configured yet - silently a no-op, never a user-facing error

    queue = load_queue()
    if not queue or not queue.get("remaining"):
        return  # nothing pending - today's queue is already caught up

    last_batch_at = queue.get("last_batch_at")
    if not last_batch_at:
        return
    elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(last_batch_at)).total_seconds()
    if elapsed < CATCHUP_COOLDOWN_SECONDS:
        return  # still within a normal cron cadence - let the real cron do its job

    try:
        requests.post(
            f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{REFRESH_WORKFLOW_FILE}/dispatches",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            json={"ref": "main", "inputs": {"tickers": ""}},
            timeout=5,
        )
    except requests.RequestException:
        pass  # best-effort backstop - a failed dispatch just means we try again next cooldown window


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
/* Dims a row whose status hasn't been refreshed in today's queue drain yet - opacity on the
   whole row (not per-element color tweaks) so it still dims every child even though each
   text node above already hardcodes its own color via !important. Exact last-evaluated date
   is on the row's native title attribute (hover tooltip) instead of inline text, so a crowded
   dashboard doesn't gain another always-visible data point. */
.sd-row-stale {
    opacity: 0.3;
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
/* Momentum Continuation's Trend/Daily/Weekly check glyphs (docs/Momentum Continuation -
   V2 Logic Improvement Specs.md Part C) - muted teal-green/muted orange, deliberately
   not red/green, so a failing leg doesn't read as an alarm. Declared after
   .sd-row-reason's own "* -> same muted gray, !important" rule above so these win the
   specificity tie on source order. */
.sd-check-pass {
    color: #5eb8a4 !important;
}
.sd-check-fail {
    color: #c99a5c !important;
}
/* STOCH FLATTENING row badge (Part C) - light/muted, never an alert color. Sits in
   .sd-row-status beside the dot, on the right end of .sd-row-top (see .sd-row-top's
   justify-content: space-between below). */
.sd-row-flattening-badge {
    display: inline-block;
    background: rgba(226, 232, 240, 0.12);
    color: rgba(226, 232, 240, 0.85) !important;
    border-radius: 4px;
    padding: 0.15rem 0.55rem;
    font-weight: 600;
    font-size: 0.85rem;
    letter-spacing: 0.03em;
    white-space: nowrap;
    flex-shrink: 0;
}
/* MACD TURNING row badge - same treatment as STOCH FLATTENING above (muted, never an
   alert color), just a different hue so the two read as distinct badges when both show
   on the same Developing row. */
.sd-row-macd-badge {
    display: inline-block;
    background: rgba(147, 197, 253, 0.12);
    color: rgba(147, 197, 253, 0.85) !important;
    border-radius: 4px;
    padding: 0.15rem 0.55rem;
    font-weight: 600;
    font-size: 0.85rem;
    letter-spacing: 0.03em;
    white-space: nowrap;
    flex-shrink: 0;
}
.sd-row-status {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    flex-shrink: 0;
}
div[data-testid="stMarkdownContainer"] .sd-row {
    display: flex;
    align-items: center;
    gap: 0.75rem;
}
.sd-row-main {
    flex: 1 1 auto;
    min-width: 0;
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
    font-size: 1.3rem;
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

/* Pinned wall (user-pinned names for manual/visual entry timing - never moved, dimmed or
   removed by the algo). Pin buttons are real st.buttons (icon-only, tertiary); the key prefix
   encodes pinned/unpinned so CSS can tell them apart: "sd_pinon_*" is filled and bright,
   "sd_pinoff_*" is hollow and muted. */
div[class*="st-key-sd_pin"] button [data-testid="stMarkdownContainer"] {
    display: none;  /* icon-only: the label exists for accessibility, not display */
}
div[class*="st-key-sd_pinon_"] button,
div[class*="st-key-sd_pinon_"] [data-testid="stIconMaterial"] {
    color: #ef4444 !important;  /* pinned = red (same red as the wall header's pin) */
}
div[class*="st-key-sd_pinon_tile_"] {
    margin-top: -0.35rem;  /* line the icon up with the tile's top row (tag chip) */
}
div[class*="st-key-sd_pinon_"] [data-testid="stIconMaterial"] {
    font-variation-settings: 'FILL' 1;
}
div[class*="st-key-sd_pinoff_"] button,
div[class*="st-key-sd_pinoff_"] [data-testid="stIconMaterial"] {
    color: rgba(250, 250, 250, 0.4) !important;
}
/* Wall tiles in one row stretch to the tallest tile (a name wrapping beside a badge would
   otherwise leave ragged bottoms). */
div[class*="st-key-sd_wall"] div[data-testid="stHorizontalBlock"] {
    align-items: stretch;
}
/* Streamlit 1.67 DOM: stColumn > stVerticalBlock > stLayoutWrapper > stVerticalBlock (the
   bordered tile). Each link is stretched so the bordered tile fills its (stretched) column. */
div[class*="st-key-sd_wall"] div[data-testid="stColumn"] {
    height: auto !important;
    align-self: stretch !important;
}
div[class*="st-key-sd_wall"] div[data-testid="stColumn"] > div[data-testid="stVerticalBlock"] {
    height: 100%;
}
div[class*="st-key-sd_wall"] div[data-testid="stColumn"] > div[data-testid="stVerticalBlock"] > div[data-testid="stLayoutWrapper"] {
    flex: 1 1 auto;
    align-items: stretch;
}
div[class*="st-key-sd_wall"] div[data-testid="stColumn"] > div[data-testid="stVerticalBlock"] > div[data-testid="stLayoutWrapper"] > div[data-testid="stVerticalBlock"] {
    height: 100%;
    flex: 1 1 auto;
}
/* Tile top row: setup tag on the left, current status on the right (next to the pin button). */
div[class*="st-key-sd_tile_"] {
    gap: 0.35rem;
}
.sd-tile-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.4rem 0.6rem;
}
.sd-tile-status {
    font-size: 0.85rem;
    white-space: nowrap;
}
/* Tile bottom row (height always reserved): "Pinned as ..." on the left, the setup's badges on
   the right. Badges use the same small size as the setup tag chip so the two read as one family
   (the row's own badge size, .85rem, is larger). */
.sd-tile-bottom {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 0.3rem 0.6rem;
    margin-top: 0.45rem;
    min-height: 1.3em;
}
.sd-tile-badges {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    margin-left: auto;
}
.sd-tile-badges .sd-row-flattening-badge,
.sd-tile-badges .sd-row-macd-badge {
    font-size: 0.7rem;
    padding: 0.1rem 0.45rem;
}
.sd-tile-tag {
    display: inline-block;
    background: rgba(148, 163, 184, 0.14);
    border-radius: 4px;
    padding: 0.1rem 0.45rem;
    font-size: 0.7rem;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: rgba(250, 250, 250, 0.6) !important;
}
/* "Pinned as ..." text, shown only when the status changed since pinning; the bottom row it
   lives in (.sd-tile-bottom) reserves its height either way so tiles stay aligned. */
.sd-tile-change {
    font-size: 0.9rem;
    line-height: 1.3;
    color: rgba(250, 250, 250, 0.6) !important;
}
</style>
"""


PINS_KEY = "pins_doc"  # the pins.json document for this session; None = pins unavailable
PINS_LOAD_ERROR_KEY = "pins_load_error"
PIN_ERROR_KEY = "pin_error"
PINS_RETRY_AT_KEY = "pins_retry_at"  # time.monotonic() before which a failed load is not retried
PINS_RETRY_SECONDS = 30
PINNABLE_GROUPS = (GROUP_QUALIFIED, GROUP_DEVELOPING)
GROUP_NAME = {GROUP_QUALIFIED: "Qualified", GROUP_DEVELOPING: "Developing", GROUP_OTHER: "Monitored"}
WALL_TILES_PER_ROW = 3


def get_user_store() -> user_store.UserStore | None:
    """None when no write token is configured - every pin control is then hidden and the app
    behaves exactly as it did read-only."""
    if user_store.TEST_BACKEND is not None:
        return user_store.UserStore(user_store.TEST_BACKEND)
    try:
        token = st.secrets.get(user_store.TOKEN_SECRET_NAME)
    except Exception:
        token = None
    if not token:
        return None
    return user_store.UserStore(user_store.GitHubBackend(token))


def _load_pins(store: user_store.UserStore) -> dict:
    doc = store.read(pins.PINS_PATH, pins.empty_doc)
    try:
        known_tickers = all_watchlist_tickers()
    except (OSError, ValueError):
        known_tickers = set()
    if pins.prune_orphans(doc, known_tickers) != doc:
        doc = store.update(
            pins.PINS_PATH,
            lambda d: pins.prune_orphans(d, known_tickers),
            pins.empty_doc,
            "prune pins for tickers in no watchlist",
        )
    return doc


def ensure_pins_loaded() -> None:
    """Loads pins.json once per browser session (not on every rerun - each button click
    reruns the script, and a GitHub round-trip per rerun would be slow), and cleans up pins
    whose ticker is in no watchlist.

    A failed load is NOT final: it records why (shown by render_pin_status) and the next rerun
    at least PINS_RETRY_SECONDS later tries again, so one slow GitHub response or a transient
    error no longer switches pins off until the browser is reloaded. Any exception counts, not
    just StoreError.

    Nothing is marked "attempted" until an attempt has finished. Streamlit interrupts a running
    script when a new rerun request arrives (a click, a reconnect, the frontend's follow-up
    request on page load) by raising StopException - a BaseException, so no `except Exception`
    sees it - and if that lands while waiting on GitHub the attempt simply stops mid-way. Marking
    the session "loaded" before the work left it with no pins, no error and no retry; with no
    marker left behind, the very next rerun (which is the one that interrupted us) just tries again."""
    if st.session_state.get(PINS_KEY) is not None:
        return
    retry_at = st.session_state.get(PINS_RETRY_AT_KEY)  # set only by a recorded failure
    if retry_at is not None and time.monotonic() < retry_at:
        return
    st.session_state[PINS_KEY] = None
    st.session_state.pop(PINS_RETRY_AT_KEY, None)

    store = get_user_store()
    if store is None:
        # Said out loud (not silently hidden) so a missing/misnamed secret is diagnosable at a
        # glance - an empty wall is hidden by design, so without this "no token" looks exactly
        # like "feature not deployed".
        _record_pins_failure(f"{user_store.TOKEN_SECRET_NAME} is not set in the app's secrets.")
        return
    try:
        st.session_state[PINS_KEY] = _load_pins(store)
        st.session_state.pop(PINS_LOAD_ERROR_KEY, None)
    except user_store.StoreError as exc:
        _record_pins_failure(str(exc))
    except Exception as exc:  # noqa: BLE001 - the whole point is that nothing here fails silently
        _record_pins_failure(f"{type(exc).__name__}: {exc}")


def _record_pins_failure(reason: str) -> None:
    st.session_state[PINS_LOAD_ERROR_KEY] = reason
    st.session_state[PINS_RETRY_AT_KEY] = time.monotonic() + PINS_RETRY_SECONDS


def render_pin_status() -> None:
    """Always-visible one-line pin status under the refresh banner. Temporary diagnostic: it
    exists so 'pins missing' always says why (loaded / not loaded and the reason). Remove this
    function and its call in main() once the pins problem is understood."""
    pins_doc = st.session_state.get(PINS_KEY)
    if pins_doc is not None:
        count = len(pins_doc.get("pins", []))
        st.caption(f"Pins: loaded ({count} pinned across all watchlists)")
    else:
        reason = st.session_state.get(PINS_LOAD_ERROR_KEY) or "not loaded (no error recorded)"
        st.caption(f"Pins unavailable: {reason}")


def toggle_pin(setup_id: str, ticker: str, group: str) -> None:
    """Button callback: pin if not pinned, else unpin. Writes through to the user-data branch
    and adopts the stored result as the session's truth; on failure nothing changes locally
    and the reason is surfaced as a toast on the next run."""
    store = get_user_store()
    doc = st.session_state.get(PINS_KEY)
    if store is None or doc is None:
        return
    if pins.is_pinned(doc, setup_id, ticker):
        message = f"unpin {ticker} ({setup_id})"

        def change(d: dict) -> dict:
            return pins.remove_pin(d, setup_id, ticker)

    else:
        message = f"pin {ticker} ({setup_id})"
        pinned_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")

        def change(d: dict) -> dict:
            return pins.add_pin(d, setup_id, ticker, group, pinned_at)

    try:
        st.session_state[PINS_KEY] = store.update(pins.PINS_PATH, change, pins.empty_doc, message)
    except user_store.StoreError as exc:
        st.session_state[PIN_ERROR_KEY] = f"Couldn't save the pin change: {exc}"


def pin_button(setup: Setup, ticker: str, group: str, pinned: bool, scope: str) -> None:
    st.button(
        "Pin",
        key=f"sd_pin{'on' if pinned else 'off'}_{scope}_{setup.id}_{ticker}",
        icon=":material/push_pin:",
        type="tertiary",
        help="Unpin" if pinned else "Pin to the wall",
        on_click=toggle_pin,
        args=(setup.id, ticker, group),
    )


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


def queue_path() -> str:
    return os.path.join(STATE_DIR, "refresh_queue.json")


def load_queue_remaining() -> set[str]:
    # Deliberately uncached for the same reason as load_results - small file, only
    # changes via git push + redeploy, re-reading costs nothing.
    try:
        with open(queue_path()) as f:
            return set(json.load(f).get("remaining", []))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def compute_pending_tickers(watchlist_tickers: set[str], setups_doc: dict) -> set[str]:
    """Tickers whose row on screen isn't this cycle's data yet - still sitting in tonight's
    queue (state/refresh_queue.json, not yet attempted) or whose last attempt errored (any
    registered setup's own "errors" list, e.g. a Tiingo rate limit). Shared by the pending
    banner's count and by render_row's stale-dimming, so the two always agree on what counts
    as "not yet refreshed today" instead of drifting apart as separate calculations."""
    not_yet_reached = watchlist_tickers & load_queue_remaining()
    still_erroring = {e["ticker"] for doc in setups_doc.values() for e in doc.get("errors", [])}
    return (not_yet_reached | still_erroring) & watchlist_tickers


def render_pending_banner(pending: set[str], total: int) -> None:
    """Single status line replacing the old EOD-session label plus the three separate
    staleness/incomplete-refresh/off-session banners - refresh_job.py drains one shared
    queue across many hourly firings now, so there's no single "as of" date for a whole
    watchlist anymore, just a count of what hasn't caught up yet."""
    if pending:
        st.info(f"{len(pending)} of {total} ticker(s) pending refresh today.")
    else:
        st.caption(f"All {total} ticker(s) refreshed today.")


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


def _bucket_by_group(results: list[dict], stale_tickers: set[str]) -> dict[str, list[dict]]:
    """Buckets by ui_group, then stable-sorts each bucket so today's refreshed rows sit above
    yesterday's stale (dimmed) ones - list.sort() is stable, so within "fresh" and within
    "stale" the original ordering is left untouched."""
    by_group: dict[str, list[dict]] = {GROUP_QUALIFIED: [], GROUP_DEVELOPING: [], GROUP_OTHER: [], GROUP_UNKNOWN: []}
    for r in results:
        by_group.setdefault(r.get("ui_group", GROUP_UNKNOWN), []).append(r)
    for rows in by_group.values():
        rows.sort(key=lambda r: r["ticker"] in stale_tickers)
    return by_group


def _toggle_setup_open(open_key: str) -> None:
    st.session_state[open_key] = not st.session_state.get(open_key, False)


def render_setup_accordion(setup: Setup, setup_data: dict, watchlist_id: str, stale_tickers: set[str]) -> None:
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

    by_group = _bucket_by_group(results, stale_tickers)
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

    render_section("Qualified", qualified_rows, "No qualified stocks in this scan.", setup, stale_tickers)
    render_section(
        "Developing", developing_rows, "No developing stocks in this scan.", setup, stale_tickers, spaced=True
    )

    st.markdown('<div class="sd-toggle-spacer"></div>', unsafe_allow_html=True)

    # A plain disclosure, not a nested st.expander - Streamlit doesn't allow expanders
    # inside expanders, and (now) doesn't allow one inside this hand-rolled accordion either.
    if st.toggle(f"Other monitored ({len(other_rows)})", key=f"sd_other_{watchlist_id}_{setup.id}"):
        if not other_rows:
            st.caption("Nothing in Other monitored.")
        else:
            for result in other_rows:
                render_row(result, setup, stale_tickers)

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


#  Plain `class="sd-row-reason"` text was rendering link-blue despite the
#  !important class rule - rather than keep chasing Streamlit's internal CSS
#  cascade blind, every text node here also carries an inline `style` color,
#  which no external stylesheet (Streamlit's or ours) can outrank.
_REASON_STYLE = 'style="color: rgba(250, 250, 250, 0.75) !important;"'


def _row_parts(result: dict, setup: Setup, reason_override: tuple[str, bool] | None = None) -> dict[str, str]:
    """The HTML pieces of one row, shared by the list rows and the pinned-wall tiles so a tile
    always reads exactly like its row (only the arrangement differs). Dispatches to the active
    setup's own reason_for() rather than hardcoding any one setup's statuses/wording here -
    this is what lets a second registered setup show up with zero changes to this function.
    `reason_override` is only for a pinned ticker that has no evaluation row at all."""
    group = result.get("ui_group", GROUP_UNKNOWN)
    name = result.get("name")

    reason_text, is_invalid = reason_override or setup.reason_for(result)
    if is_invalid:
        reason_html = f'<div class="sd-row-reason"><span class="sd-badge sd-badge-invalid">{reason_text}</span></div>'
    else:
        reason_html = f'<div class="sd-row-reason" {_REASON_STYLE}>{reason_text}</div>'

    return {
        "dot": GROUP_DOT.get(group, DOT_NEUTRAL),
        "ticker": f'<span class="sd-row-ticker" style="color: #fafafa !important;">{result["ticker"]}</span>',
        "name": f'<span class="sd-row-name" {_REASON_STYLE}>{name}</span>' if name else "",
        "reason": reason_html,
        "badges": "".join(
            f'<div class="{css_class}">{text}</div>'
            for text, css_class in (setup.badge_for(result) if result.get("status") else [])
        ),
    }


def row_html_for(result: dict, setup: Setup, is_stale: bool) -> str:
    parts = _row_parts(result, setup)

    # Dimmed (not relabeled/hidden) when this ticker hasn't been refreshed in today's queue
    # drain yet - the status shown is still whatever its last successful evaluation found,
    # just visibly not guaranteed to be today's. Exact date goes on the native title
    # attribute (hover tooltip) rather than inline text - see .sd-row-stale CSS comment.
    row_class = "sd-row sd-row-stale" if is_stale else "sd-row"
    evaluated_session = result.get("evaluated_session")
    title_attr = f' title="Last evaluated {evaluated_session}"' if is_stale and evaluated_session else ""

    return (
        f'<div class="{row_class}"{title_attr}>'
        '<div class="sd-row-main">'
        '<div class="sd-row-top">'
        '<div class="sd-row-left">'
        f"{parts['ticker']}"
        f"{parts['name']}"
        "</div>"
        '<div class="sd-row-status">'
        f"{parts['badges']}"
        f'<div class="sd-dot">{parts["dot"]}</div>'
        "</div>"
        "</div>"
        f"{parts['reason']}"
        "</div>"
        "</div>"
    )


def render_row(result: dict, setup: Setup, stale_tickers: set[str]) -> None:
    group = result.get("ui_group", GROUP_UNKNOWN)
    row_html = row_html_for(result, setup, result["ticker"] in stale_tickers)

    # Pin control only on Qualified/Developing rows, and only when a write token is
    # configured and pins loaded. A pinned name that later falls to Monitored is unpinned
    # from its wall tile, not from its row.
    pins_doc = st.session_state.get(PINS_KEY)
    show_pin = pins_doc is not None and group in PINNABLE_GROUPS

    with st.container(border=True):
        # st.html(), not st.markdown() - renders raw HTML with none of
        # Streamlit's own markdown-container wrapping/spacing. Two rounds of
        # !important overrides against that wrapping still wasn't enough, so
        # this sidesteps the fight entirely instead of guessing at Streamlit's
        # internal stylesheet a third time.
        if show_pin:
            html_col, button_col = st.columns([24, 1], vertical_alignment="center")
            with html_col:
                st.html(row_html)
            with button_col:
                pin_button(setup, result["ticker"], group, pins.is_pinned(pins_doc, setup.id, result["ticker"]), "row")
        else:
            st.html(row_html)


def render_tile(pin: dict, setup: Setup, results_by_ticker: dict[str, dict]) -> None:
    """One pinned-wall tile: the row's own content (live, from tonight's evaluation) under a
    setup tag, plus a reserved second line that says "Pinned as <group>" only when the current
    group differs from the group at pin time. Deliberately never dimmed for staleness and never
    expired - the wall is for manual/visual evaluation and the algo doesn't get to move it."""
    ticker = pin["ticker"]
    result = results_by_ticker.get(ticker)
    if result is None:
        result = {"ticker": ticker, "ui_group": GROUP_UNKNOWN}
        parts = _row_parts(result, setup, reason_override=("No evaluation available yet.", False))
        current_group = None
    else:
        parts = _row_parts(result, setup)
        current_group = result.get("ui_group", GROUP_UNKNOWN)

    change_html = "&nbsp;"
    pinned_group = pin.get("pinned_group")
    if current_group is not None and pinned_group and pinned_group != current_group:
        change_html = f"Pinned as {GROUP_DOT.get(pinned_group, DOT_NEUTRAL)} {GROUP_NAME.get(pinned_group, pinned_group)}"

    # Status state 1 (always): the CURRENT group, spelled out next to its dot, top-right beside
    # the pin button. Status state 2 (only when it differs from pin time): the "Pinned as ..."
    # text bottom-left. The setup's badges (MACD TURNING etc.) share that bottom row on the
    # right - it is a reserved-height row anyway - so the top row is only tag + status and the
    # ticker/name line below gets the tile's full width.
    status_label = GROUP_NAME.get(result.get("ui_group", GROUP_UNKNOWN), "Unknown")
    top_html = (
        '<div class="sd-tile-top">'
        f'<span class="sd-tile-tag" style="color: rgba(250, 250, 250, 0.6) !important;">{setup.label}</span>'
        f'<span class="sd-tile-status" style="color: rgba(250, 250, 250, 0.9) !important;">{parts["dot"]} {status_label}</span>'
        "</div>"
    )
    body_html = (
        '<div class="sd-row"><div class="sd-row-main">'
        f'<div class="sd-row-left">{parts["ticker"]}{parts["name"]}</div>'
        f"{parts['reason']}"
        "</div></div>"
        '<div class="sd-tile-bottom">'
        f'<div class="sd-tile-change" style="color: rgba(226, 232, 240, 0.85) !important;">{change_html}</div>'
        f'<div class="sd-tile-badges">{parts["badges"]}</div>'
        "</div>"
    )
    # Only the top row (tag + status) shares a row with the pin button; the body and the bottom
    # row (badges) span the tile's full width, so their right edge is the tile's padding edge.
    with st.container(border=True, key=f"sd_tile_{setup.id}_{ticker}"):
        top_col, button_col = st.columns([8, 1], vertical_alignment="top")
        with top_col:
            st.html(top_html)
        with button_col:
            pin_button(setup, ticker, pinned_group or GROUP_UNKNOWN, True, "tile")
        st.html(body_html)


def render_pinned_wall(pins_doc: dict, setups_doc: dict, watchlist_tickers: set[str]) -> None:
    """The wall for the selected watchlist: global pins filtered to tickers in this watchlist,
    in pin order, WALL_TILES_PER_ROW per row. Hidden entirely when there's nothing to show."""
    setups_by_id = {s.id: s for s in list_setups()}
    wall = [p for p in pins.pins_in_watchlist(pins_doc, watchlist_tickers) if p["setup"] in setups_by_id]
    if not wall:
        return

    with st.container(key="sd_wall"):
        st.markdown('<div class="sd-section-header"><strong>📌 Pinned Wall</strong></div>', unsafe_allow_html=True)
        for start in range(0, len(wall), WALL_TILES_PER_ROW):
            columns = st.columns(WALL_TILES_PER_ROW)  # always full width so a short last row stays tile-sized
            for column, pin in zip(columns, wall[start : start + WALL_TILES_PER_ROW]):
                setup = setups_by_id[pin["setup"]]
                results_by_ticker = {r["ticker"]: r for r in setups_doc.get(setup.id, {}).get("results", [])}
                with column:
                    render_tile(pin, setup, results_by_ticker)


def render_section(
    title: str, rows: list[dict], empty_message: str, setup: Setup, stale_tickers: set[str], spaced: bool = False
) -> None:
    """`spaced=True` adds extra top margin before the header - used for a
    second section (e.g. Developing) directly following another section's
    rows, so it doesn't read as cramped against the last row above it."""
    css_class = "sd-section-header sd-section-spaced" if spaced else "sd-section-header"
    st.markdown(f'<div class="{css_class}"><strong>{title} ({len(rows)})</strong></div>', unsafe_allow_html=True)
    if not rows:
        st.caption(empty_message)
        return
    for result in rows:
        render_row(result, setup, stale_tickers)


def main() -> None:
    if not check_password():
        st.stop()

    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

    ensure_pins_loaded()
    pin_error = st.session_state.pop(PIN_ERROR_KEY, None)
    if pin_error:
        st.toast(pin_error, icon="⚠️")

    render_header()
    maybe_trigger_catchup_refresh()

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

    watchlist_tickers = {e["ticker"] for e in load_watchlist(selected_watchlist_id).get("tickers", [])}
    pending_tickers = compute_pending_tickers(watchlist_tickers, setups_doc)
    render_pending_banner(pending_tickers, len(watchlist_tickers))

    render_pin_status()
    pins_doc = st.session_state.get(PINS_KEY)
    if pins_doc is not None:
        render_pinned_wall(pins_doc, setups_doc, watchlist_tickers)

    st.divider()
    for setup in list_setups():
        render_setup_accordion(setup, setups_doc.get(setup.id, {}), selected_watchlist_id, pending_tickers)


main()
