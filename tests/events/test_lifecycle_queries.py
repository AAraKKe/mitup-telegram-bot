from mitup_bot.events.lifecycle_queries import loggable_windows, resolved_windows
from mitup_bot.events.service import lifecycle_windows
from mitup_bot.lifecycle import LifecyclePolicy
from mitup_bot.supporter import LEVEL_ORDER


def test_resolved_windows_labels_every_level_with_its_own_policy_window():
    """The label a lifecycle decision carries is generated from the same `levels_by_duration`
    grouping the SQL branches are, so every level resolves to the window its own policy runs on and
    a tier moved to a different duration moves its label with it."""
    windows = resolved_windows(lambda policy: policy.inactive_retention)

    assert set(windows) == set(LEVEL_ORDER)
    for level in LEVEL_ORDER:
        assert windows[level] == LifecyclePolicy.interval_days(LifecyclePolicy.get(level).inactive_retention)


def test_loggable_windows_keys_on_the_stored_level_value():
    windows = loggable_windows(lambda policy: policy.dateless_lifetime)

    assert set(windows) == {level.value for level in LEVEL_ORDER}


def test_bootstrap_names_every_window_the_process_enforces():
    """A policy edit otherwise ships with the image tag as its only evidence: the startup line is
    what makes the running windows queryable."""
    assert set(lifecycle_windows()) == {"dateless_lifetime", "inactive_retention", "deletion_warning_delay"}
