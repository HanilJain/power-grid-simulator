"""Tests for the discrete-time simulation engine (backend/simulator/engine.py).

Written first (RED). These assert the task's core invariants hold over a
full run, not just at isolated states:
  - frequency never leaves the safe band (Assumption 2)
  - CB101 is never CLOSED while frequency is below 49.50 Hz (Assumption 3)
  - CB101 is closed "whenever feasible" (Assumption 4) -> majority powered
"""

from __future__ import annotations

import pytest

from backend.simulator import constants as c
from backend.simulator.engine import run_simulation
from backend.simulator.models import ManualEvent, SimConfig


class TestNormalScenarioInvariants:
    def test_frequency_never_breaches_safe_band(self) -> None:
        result = run_simulation(SimConfig(duration_s=600, scenario="normal"))
        assert result.limit_violations == 0

    def test_breaker_never_closed_below_lower_limit(self) -> None:
        result = run_simulation(SimConfig(duration_s=600, scenario="normal"))
        for step in result.steps:
            if step.breaker_closed:
                assert step.true_frequency_hz >= c.LOWER_LIMIT_HZ

    def test_station_is_powered_most_of_the_time(self) -> None:
        # Physics fixes the theoretical steady-state duty cycle at
        # 0.12 / (0.05 + 0.12) ~= 0.706; allow a reasonable band around it.
        result = run_simulation(SimConfig(duration_s=600, scenario="normal"))
        assert 0.55 <= result.powered_fraction <= 0.85

    def test_produces_one_step_per_second_of_configured_duration(self) -> None:
        result = run_simulation(SimConfig(duration_s=120, scenario="normal"))
        assert len(result.steps) == 120
        assert result.steps[0].t == 0
        assert result.steps[-1].t == 119

    def test_no_trips_in_normal_scenario(self) -> None:
        result = run_simulation(SimConfig(duration_s=600, scenario="normal"))
        assert result.trip_count == 0


class TestGeneratorFailureScenario:
    def test_generator_turns_off_at_configured_time(self) -> None:
        result = run_simulation(
            SimConfig(duration_s=300, scenario="generator_failure", generator_failure_time_s=100)
        )
        before = result.steps[99]
        after = result.steps[100]
        assert before.generator_on is True
        assert after.generator_on is False

    def test_breaker_opens_protectively_after_generator_failure(self) -> None:
        result = run_simulation(
            SimConfig(duration_s=300, scenario="generator_failure", generator_failure_time_s=100)
        )
        # well after the failure, with no source, CB101 must be open
        tail = result.steps[150:]
        assert all(not s.breaker_closed for s in tail)

    def test_frequency_never_breaches_safe_band_during_failure(self) -> None:
        result = run_simulation(
            SimConfig(duration_s=300, scenario="generator_failure", generator_failure_time_s=100)
        )
        assert result.limit_violations == 0

    def test_frequency_freezes_once_unpowered_and_open(self) -> None:
        result = run_simulation(
            SimConfig(duration_s=300, scenario="generator_failure", generator_failure_time_s=100)
        )
        tail_freqs = [s.true_frequency_hz for s in result.steps[200:]]
        assert max(tail_freqs) - min(tail_freqs) < 1e-9


class TestFaultySensorScenario:
    def test_validated_controller_rejects_the_bad_reading_and_does_not_trip(self) -> None:
        result = run_simulation(
            SimConfig(
                duration_s=200,
                scenario="faulty_sensor",
                fault_time_s=60,
                disable_sensor_validation=False,
            )
        )
        # the fault must have been injected...
        fault_step = result.steps[60]
        assert fault_step.sensor_fault_injected is True
        # ...but rejected by validation, and true frequency stays safe
        assert fault_step.reading_rejected is True
        assert result.limit_violations == 0
        assert result.trip_count == 0

    def test_naive_controller_without_validation_is_fooled_by_the_bad_reading(self) -> None:
        # Reproduces the task's background incident: a faulty reading alone
        # causes the breaker to open even though the true frequency was fine.
        result = run_simulation(
            SimConfig(
                duration_s=200,
                scenario="faulty_sensor",
                fault_time_s=60,
                disable_sensor_validation=True,
            )
        )
        fault_step = result.steps[60]
        assert fault_step.sensor_fault_injected is True
        assert fault_step.breaker_closed is False  # fooled into opening
        assert fault_step.tripped is True

    def test_true_frequency_was_never_actually_unsafe_at_fault_time(self) -> None:
        result = run_simulation(
            SimConfig(
                duration_s=200,
                scenario="faulty_sensor",
                fault_time_s=60,
                disable_sensor_validation=True,
            )
        )
        fault_step = result.steps[60]
        assert fault_step.true_frequency_hz >= c.LOWER_LIMIT_HZ


class TestManualScenario:
    """The "manual" scenario lets an operator script G101/CB101 actions
    directly (via SimConfig.events) instead of picking an automatic
    scenario. Predictive protection (control.protection_would_open) can
    optionally override an unsafe forced CLOSE via protection_enabled.
    """

    def test_events_apply_at_the_scripted_second(self) -> None:
        result = run_simulation(
            SimConfig(
                duration_s=10,
                scenario="manual",
                events=(ManualEvent(t=5, action="breaker_open"),),
            )
        )
        assert result.steps[4].breaker_closed is True
        assert result.steps[5].breaker_closed is False
        assert result.steps[9].breaker_closed is False

    def test_generator_off_event_applies_at_the_scripted_second(self) -> None:
        result = run_simulation(
            SimConfig(
                duration_s=10,
                scenario="manual",
                events=(ManualEvent(t=3, action="generator_off"),),
            )
        )
        assert result.steps[2].generator_on is True
        assert result.steps[3].generator_on is False

    def test_reclose_event_restores_closed_state(self) -> None:
        result = run_simulation(
            SimConfig(
                duration_s=10,
                scenario="manual",
                events=(
                    ManualEvent(t=2, action="breaker_open"),
                    ManualEvent(t=4, action="breaker_close"),
                ),
            )
        )
        assert result.steps[2].breaker_closed is False
        assert result.steps[4].breaker_closed is True

    def test_protection_enabled_overrides_unsafe_forced_close(self) -> None:
        # Start just above the lower limit so closing (gen ON -> -0.05 Hz/s)
        # would breach it; protection must force OPEN instead.
        result = run_simulation(
            SimConfig(
                duration_s=5,
                scenario="manual",
                initial_frequency_hz=49.54,
                protection_enabled=True,
                events=(ManualEvent(t=0, action="breaker_close"),),
            )
        )
        assert result.steps[0].breaker_closed is False
        assert result.steps[0].protection_overridden is True
        assert result.limit_violations == 0

    def test_protection_disabled_allows_limit_violation(self) -> None:
        # Same unsafe close, but with the safety net off: the operator's
        # choice stands and the band CAN be breached (demonstrating what
        # protection normally prevents).
        result = run_simulation(
            SimConfig(
                duration_s=5,
                scenario="manual",
                initial_frequency_hz=49.54,
                protection_enabled=False,
                breaker_initially_closed=True,
                generator_initially_on=True,
            )
        )
        assert result.steps[0].breaker_closed is True
        assert result.steps[0].protection_overridden is False
        assert result.limit_violations > 0

    def test_no_events_behaves_like_a_static_initial_state(self) -> None:
        result = run_simulation(
            SimConfig(duration_s=5, scenario="manual", events=())
        )
        assert all(s.generator_on for s in result.steps)
