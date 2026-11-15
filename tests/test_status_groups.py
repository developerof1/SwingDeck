"""Tests for the shared UI-status taxonomy (src/status_groups.py) - the bucket names,
dot colors, section labels, and the generic lookup helper every setup's own status
mapping goes through. No setup-specific status or copy belongs in this file.
"""

from src.status_groups import (
    DOT_DEVELOPING,
    DOT_OTHER,
    DOT_QUALIFIED,
    GROUP_DEVELOPING,
    GROUP_DOT,
    GROUP_LABEL,
    GROUP_OTHER,
    GROUP_QUALIFIED,
    GROUP_UNKNOWN,
    group_for_status,
)


def test_group_for_status_looks_up_the_given_mapping():
    mapping = {"FOO": GROUP_QUALIFIED, "BAR": GROUP_DEVELOPING}
    assert group_for_status("FOO", mapping) == GROUP_QUALIFIED
    assert group_for_status("BAR", mapping) == GROUP_DEVELOPING


def test_group_for_status_falls_back_to_unknown_for_an_unmapped_status():
    assert group_for_status("SOME_UNRECOGNIZED_STATUS", {}) == GROUP_UNKNOWN


def test_every_group_has_a_dot_and_a_label():
    for group in (GROUP_QUALIFIED, GROUP_DEVELOPING, GROUP_OTHER, GROUP_UNKNOWN):
        assert group in GROUP_DOT
        assert group in GROUP_LABEL


def test_dot_colors_are_distinct():
    assert len({DOT_QUALIFIED, DOT_DEVELOPING, DOT_OTHER}) == 3
