---
name: add-watchlist
description: Adds a new named watchlist to SwingDeck, the Streamlit swing-trading dashboard in this repo. Use this whenever the user wants to add, create, set up, or import a new watchlist - they'll typically paste a watchlist name plus a list of tickers, each with a sector and a thesis, in whatever format is handy (comma-separated, one per line, a loose table). Also use it if they ask to "feed a new watchlist," "load a new ticker list," or hand you a batch of tickers to track as a group. Walks through parsing the input, writing watchlists/<id>.json, committing and pushing it, and triggering an immediate GitHub Actions refresh so results populate right away instead of waiting for the nightly cron.
---

# Add a new SwingDeck watchlist

SwingDeck (this repo) evaluates one or more named watchlists nightly. Each watchlist is a
self-describing file under `watchlists/<id>.json`:

```json
{
  "name": "Display Name",
  "tickers": [
    { "ticker": "NVDA", "sector": "Chips & Hardware", "thesis": "free-text rationale" }
  ]
}
```

The filename stem becomes the watchlist's id (`watchlists/tech_growth.json` → `tech_growth`).
That id is the only key anything downstream cares about: it names the results file
(`results/<id>.json`, written nightly by `scripts/refresh_job.py` via GitHub Actions) and
it's what the Streamlit app's watchlist dropdown reads via
`src/watchlist_store.discover_watchlists()`. There's no separate registry to update -
dropping a correctly-shaped file in `watchlists/` is the only step that needs a human or
Claude; the app and the nightly refresh pick it up from there automatically.

## 1. Parse the user's input

The user gives you a watchlist display name and a list of tickers, each with a sector and a
thesis - the input format varies (comma-separated, one per line, a loose table). Parse it
into `{ticker, sector, thesis}` entries.

Sector and thesis are the user's own curation - their read on why a ticker belongs on this
list - not something to look up or draft yourself, even if you could write something
plausible-sounding for a well-known company. If any entry is missing sector or thesis, ask
the user rather than defaulting to blank on your own. If they explicitly say to leave a
field blank (e.g. the source data has no thesis column at all), that's their call to make -
write `null`, don't keep pressing for text they've already told you doesn't exist.

## 2. Derive the watchlist id

Slugify the display name: lowercase, collapse any run of non-alphanumeric characters to a
single underscore, trim leading/trailing underscores. "Tech & Growth Leaders" →
`tech_growth_leaders`.

Check `watchlists/` for a file with that id already. If one exists, stop and ask the user
how to disambiguate (a different name, or they do mean to replace the existing file) -
never silently overwrite an existing watchlist.

## 3. Normalize and sanity-check the tickers

Uppercase and trim each ticker. If the same ticker appears twice *within this one new
watchlist*, that's almost certainly a copy-paste slip - flag it and confirm with the user
before writing the file.

A ticker appearing across *multiple different* watchlists is completely normal and expected
- `refresh_job.py` already dedupes fetches across watchlists, so don't treat that as an
error or try to "resolve" it.

## 4. Write the file

Write `watchlists/<id>.json` with the schema above, 2-space indent, matching the style of
the existing watchlist files in this repo.

## 5. Sanity-check it's picked up correctly

From the repo root:

```bash
python -c "from src.watchlist_store import discover_watchlists; print(discover_watchlists())"
```

Confirm the new id/name/ticker_count show up as expected before moving on - this catches a
malformed file before it's committed, rather than after.

## 6. Commit and push

```bash
git add watchlists/<id>.json
git commit -m "[data] add watchlist: <display name>"
git push
```

This matches the repo's existing convention for data-only commits (see README.md's
"Editing watchlists" section).

## 7. Trigger an immediate refresh

Waiting for the nightly cron leaves the new watchlist with no results until the next
scheduled run. Trigger it immediately instead:

```bash
gh workflow run "Daily Data Refresh"
```

(repo `developerof1/SwingDeck`, workflow file `.github/workflows/refresh_data.yml`). Leave
the `tickers` input blank - a non-blank value is a debug-only override that never writes
results, so blank is what actually produces `results/<id>.json`.

Tell the user it's running, that they can follow it with `gh run watch` or the Actions tab,
and that the new watchlist will show real evaluations in the dropdown once the run finishes
(typically a few minutes).

## Scope of this skill's automation

Steps 6-7 push to the repo and kick off a CI run without asking first. That's intentionally
pre-authorized, but *only* for this exact action - adding one well-formed watchlist file.
It is not standing permission to push other kinds of changes (code edits, deleting a
watchlist, hand-editing `results/*.json`) without confirming first. If what the user is
asking for drifts outside "add a new watchlist," fall back to the normal
confirm-before-pushing default.
