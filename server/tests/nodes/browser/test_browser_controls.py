"""Admission and retry semantics without browser launches or wall-clock sleeps."""

from __future__ import annotations

import pytest

from nodes.browser._controls import ActionGuard, GuardDecision, is_retry_safe, is_site_action


def _admit(guard, at, *, origin="https://example.com", profile="employee-a", operation="click", arguments=None, **limits):
    return guard.admit(origin=origin, profile_id=profile, operation=operation, arguments=arguments or {"ref": str(at)}, now=at, **limits)


@pytest.mark.parametrize("op", ["snapshot", "page_text", "page_info", "screenshot", "diagnose", "webmcp_list", "wait"])
def test_observations_do_not_consume_budget_or_require_a_retry_decision(op):
    guard = ActionGuard()
    assert is_retry_safe(op)
    assert not is_site_action(op)
    assert _admit(guard, 0, operation=op) is None
    assert guard._buckets == {}


@pytest.mark.parametrize("op", ["navigate", "click", "hover", "type", "press", "select", "scroll", "back", "forward", "reload", "webmcp_call", "evaluate", "run_python"])
def test_site_actions_cannot_be_replayed_after_an_uncertain_failure(op):
    assert is_site_action(op)
    assert not is_retry_safe(op)


@pytest.mark.parametrize("action", ["new", "switch", "close"])
def test_tab_mutations_are_counted_but_listing_is_an_observation(action):
    guard = ActionGuard()
    assert is_retry_safe("tabs") and not is_site_action("tabs")
    assert not is_retry_safe("tabs", action) and is_site_action("tabs", action)
    assert _admit(guard, 0, operation="tabs", arguments={"tab_action": "list"}) is None
    assert not guard._buckets
    assert _admit(guard, 0, operation="tabs", arguments={"tab_action": action}) is None
    assert len(guard._buckets) == 1


def test_unknown_operations_are_not_declared_safe_to_retry():
    assert not is_retry_safe("future_operation")
    assert not is_retry_safe("close")


def test_interval_is_shared_across_profiles_and_rejection_does_not_extend_it():
    guard = ActionGuard()
    assert _admit(guard, 0) is None
    rejected = _admit(guard, 0.75, profile="employee-b")
    assert rejected.error_type == "rate_limited"
    assert rejected.retry_after == pytest.approx(0.25)
    assert _admit(guard, 1, profile="employee-b") is None
    assert len(next(iter(guard._buckets.values())).actions) == 2


def test_quota_is_shared_across_profiles_with_an_exact_rolling_boundary():
    guard = ActionGuard()
    limits = {"min_interval_ms": 0, "max_actions_per_minute": 2}
    assert _admit(guard, 0, **limits) is None
    assert _admit(guard, 1, profile="employee-b", **limits) is None
    rejected = _admit(guard, 59.99, profile="employee-c", **limits)
    assert rejected.error_type == "rate_limited"
    assert rejected.retry_after == pytest.approx(0.01)
    assert _admit(guard, 60, profile="employee-c", **limits) is None
    assert _admit(guard, 60.5, **limits).retry_after == pytest.approx(0.5)
    assert _admit(guard, 61, **limits) is None


def test_unrelated_origins_have_independent_budgets():
    guard = ActionGuard()
    assert _admit(guard, 0, max_actions_per_minute=1) is None
    assert _admit(guard, 0, origin="https://other.example", max_actions_per_minute=1) is None


def test_repeat_fingerprint_is_canonical_and_scoped_to_the_profile_and_origin():
    guard = ActionGuard()
    limits = {"min_interval_ms": 0, "max_repeat_actions": 2}
    first = {"text": "private input", "clear": True}
    reordered = {"clear": True, "text": "private input"}
    assert _admit(guard, 0, operation="type", arguments=first, **limits) is None
    assert _admit(guard, 1, operation="type", arguments=reordered, **limits) is None
    rejected = _admit(guard, 2, operation="type", arguments=first, **limits)
    assert rejected.error_type == "repeat_limit" and rejected.next_action == "inspect_page"
    assert rejected.retry_after == 58
    assert _admit(guard, 2, profile="employee-b", operation="type", arguments=first, **limits) is None
    assert _admit(guard, 2, origin="https://other.example", operation="type", arguments=first, **limits) is None
    assert _admit(guard, 59, operation="type", arguments=first, **limits).retry_after == 1
    assert _admit(guard, 60, operation="type", arguments=first, **limits) is None


def test_ledger_and_errors_never_retain_arguments_profile_ids_or_urls():
    guard = ActionGuard()
    arguments = {"text": "highly-sensitive-text", "url": "https://private.example/path?token=secret-value"}
    assert _admit(guard, 0, profile="private-profile", operation="type", arguments=arguments, max_repeat_actions=1) is None
    rejected = _admit(guard, 1, profile="private-profile", operation="type", arguments=arguments, max_repeat_actions=1)
    stored = repr(guard.__dict__) + repr(rejected.to_dict())
    for secret in ("highly-sensitive-text", "private.example", "secret-value", "private-profile", "https://example.com"):
        assert secret not in stored
    # Caller edits cannot mutate a retained argument object: there is none.
    arguments["text"] = "changed"
    assert "changed" not in repr(guard.__dict__)


def test_active_origins_are_not_evicted_to_reset_limits_and_idle_entries_are_pruned():
    guard = ActionGuard(max_origins=2)
    assert _admit(guard, 0, origin="https://a.example") is None
    assert _admit(guard, 1, origin="https://b.example") is None
    rejected = _admit(guard, 30, origin="https://c.example")
    assert rejected.error_type == "rate_limited" and rejected.retry_after == 30
    assert len(guard._buckets) == 2
    assert _admit(guard, 60, origin="https://c.example") is None
    assert len(guard._buckets) == 2
    assert _admit(guard, 121, origin="https://d.example") is None
    assert len(guard._buckets) == 1


def test_per_origin_history_stays_bounded_even_with_a_larger_configured_quota():
    guard = ActionGuard(max_records_per_origin=2)
    limits = {"min_interval_ms": 0, "max_actions_per_minute": 10000}
    assert _admit(guard, 0, **limits) is None
    assert _admit(guard, 1, **limits) is None
    assert _admit(guard, 2, **limits).retry_after == 58
    assert len(next(iter(guard._buckets.values())).actions) == 2


def test_long_minimum_interval_survives_expiration_of_the_rolling_window():
    guard = ActionGuard()
    assert _admit(guard, 0, min_interval_ms=120000) is None
    assert _admit(guard, 60, min_interval_ms=120000).retry_after == 60
    assert _admit(guard, 120, min_interval_ms=120000) is None


def test_tightened_quota_reports_when_enough_admissions_expire():
    guard = ActionGuard()
    for at in (0, 10, 20):
        assert _admit(guard, at) is None
    assert _admit(guard, 30, max_actions_per_minute=1).retry_after == 50
    assert _admit(guard, 80, max_actions_per_minute=1) is None


def test_decision_wire_shape_and_invalid_time():
    assert GuardDecision("rate_limited", "Wait.", 1.5, "wait").to_dict() == {
        "error_type": "rate_limited", "error": "Wait.", "retry_after": 1.5, "next_action": "wait",
    }
    with pytest.raises(ValueError, match="finite"):
        _admit(ActionGuard(), float("nan"))
