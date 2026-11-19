# SwingDeck

Streamlit swing-trading dashboard. Python, Tiingo daily data, results committed to the repo by an hourly
GitHub Actions job (`scripts/refresh_job.py`). Run tests with the system interpreter: `python -m pytest -q`.

## Standing instruction: logic changes

Whenever the screening logic (Momentum Continuation or any setup) is refined or revised, follow
`.claude/rules/logic-change-workflow.md` end to end: change note wins over the master spec, ask rather than
guess on ambiguity, every tunable value in the `gates.py` config block, no copy/layout changes beyond the
note, log each new rule separately, test with fixtures from the note and report pass/fail per row, update
`docs/Momentum Continuation - V2 Logic Improvement Specs.md` and the project memory in the same commit, then
reset the refresh queue and stored status in a separate commit (UTC `night_key`) before pushing.
