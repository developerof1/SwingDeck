"""The shared UI-status taxonomy every setup's results map into (see
docs/Reference_Architecture.md §4). This is deliberately the *only* thing shared across
setups here: the four bucket names app.py renders sections for, their dot colors, their
section labels, and one generic lookup helper. Everything about *how* any one setup's
own internal states map into these buckets - and all the row-copy text explaining why -
is owned by that setup's own status_mapping.py (e.g.
src/setups/momentum_continuation/status_mapping.py), never here.
"""

GROUP_QUALIFIED = "qualified"
GROUP_DEVELOPING = "developing"
GROUP_OTHER = "other"
GROUP_UNKNOWN = "unknown"

# Dot colors: green/amber/neutral only.
DOT_QUALIFIED = "\U0001f7e2"  # green
DOT_DEVELOPING = "\U0001f7e1"  # amber
DOT_OTHER = "⚪"  # neutral/white

GROUP_DOT = {
    GROUP_QUALIFIED: DOT_QUALIFIED,
    GROUP_DEVELOPING: DOT_DEVELOPING,
    GROUP_OTHER: DOT_OTHER,
    GROUP_UNKNOWN: DOT_OTHER,
}

GROUP_LABEL = {
    GROUP_QUALIFIED: "Qualified",
    GROUP_DEVELOPING: "Developing",
    GROUP_OTHER: "Other monitored",
    GROUP_UNKNOWN: "Unknown status",
}


def group_for_status(status: str, status_to_group: dict[str, str]) -> str:
    """Looks `status` up in a setup-owned status->group dict. Unrecognized status ->
    GROUP_UNKNOWN, never silently Qualified/Developing - same safety net every setup
    gets for free by going through this instead of indexing its own dict directly."""
    return status_to_group.get(status, GROUP_UNKNOWN)
