"""Tests for FS101's measurement/fault-injection/validation model.

Written first (RED). This module directly models the task's background
incident: "a faulty frequency reading caused an unnecessary breaker trip."
The validator rejects readings that imply a physically impossible rate of
change, and debounces so a single still-plausible noisy sample can't trip
the breaker alone.
"""

from __future__ import annotations

import pytest

from backend.simulator import constants as c
from backend.simulator.sensor import SensorState, is_sensor_degraded, measure, validate


class TestMeasureNormalScenario:
    def test_normal_scenario_returns_true_frequency_unmodified(self) -> None:
        reading, fault_injected = measure(
            true_frequency_hz=49.90, t=42, scenario="normal", fault_time_s=60
        )
        assert reading == pytest.approx(49.90)
        assert fault_injected is False


class TestMeasureFaultyScenario:
    def test_injects_false_low_reading_at_fault_time(self) -> None:
        reading, fault_injected = measure(
            true_frequency_hz=50.00, t=60, scenario="faulty_sensor", fault_time_s=60
        )
        assert fault_injected is True
        # the injected fault must be large enough to look like an
        # under-frequency trip if taken at face value, but implies an
        # impossible instantaneous jump from the true 50.00 Hz.
        assert reading < c.LOWER_LIMIT_HZ

    def test_other_timesteps_are_unaffected_by_fault(self) -> None:
        reading, fault_injected = measure(
            true_frequency_hz=50.00, t=59, scenario="faulty_sensor", fault_time_s=60
        )
        assert fault_injected is False
        assert reading == pytest.approx(50.00)


class TestValidate:
    def test_accepts_physically_plausible_reading(self) -> None:
        state = SensorState(last_valid_hz=50.00, consecutive_rejections=0)
        accepted, new_state = validate(measured_hz=49.95, state=state, dt=1.0)
        assert accepted is True
        assert new_state.last_valid_hz == pytest.approx(49.95)
        assert new_state.consecutive_rejections == 0

    def test_rejects_physically_impossible_jump(self) -> None:
        # True max rate is 0.15 Hz/s; a jump of 0.9 Hz in one second cannot
        # be real bus physics -- it's a sensor fault.
        state = SensorState(last_valid_hz=50.00, consecutive_rejections=0)
        accepted, new_state = validate(measured_hz=49.10, state=state, dt=1.0)
        assert accepted is False
        # last_valid_hz is carried forward unchanged -- we don't trust the bad reading
        assert new_state.last_valid_hz == pytest.approx(50.00)
        assert new_state.consecutive_rejections == 1

    def test_accepts_reading_at_exactly_the_plausibility_boundary(self) -> None:
        state = SensorState(last_valid_hz=50.00, consecutive_rejections=0)
        boundary = 50.00 - c.MAX_PLAUSIBLE_DELTA_HZ
        accepted, _ = validate(measured_hz=boundary, state=state, dt=1.0)
        assert accepted is True

    def test_rejection_count_resets_after_an_accepted_reading(self) -> None:
        state = SensorState(last_valid_hz=50.00, consecutive_rejections=1)
        accepted, new_state = validate(measured_hz=49.95, state=state, dt=1.0)
        assert accepted is True
        assert new_state.consecutive_rejections == 0

    def test_does_not_mutate_input_state(self) -> None:
        state = SensorState(last_valid_hz=50.00, consecutive_rejections=0)
        validate(measured_hz=49.10, state=state, dt=1.0)
        # original object must be untouched (immutability)
        assert state.last_valid_hz == pytest.approx(50.00)
        assert state.consecutive_rejections == 0


class TestDebounceGatesAction:
    def test_single_rejection_does_not_yet_warrant_acting_on_an_excursion(self) -> None:
        state = SensorState(last_valid_hz=50.00, consecutive_rejections=0)
        _, new_state = validate(measured_hz=49.10, state=state, dt=1.0)
        assert new_state.consecutive_rejections < c.DEBOUNCE_SAMPLES

    def test_is_sensor_degraded_false_after_a_single_rejection(self) -> None:
        state = SensorState(last_valid_hz=50.00, consecutive_rejections=0)
        _, new_state = validate(measured_hz=49.10, state=state, dt=1.0)
        assert is_sensor_degraded(new_state) is False

    def test_is_sensor_degraded_true_once_rejections_reach_the_threshold(self) -> None:
        state = SensorState(last_valid_hz=50.00, consecutive_rejections=0)
        for _ in range(c.DEBOUNCE_SAMPLES):
            _, state = validate(measured_hz=49.10, state=state, dt=1.0)
        assert is_sensor_degraded(state) is True

    def test_is_sensor_degraded_resets_after_an_accepted_reading(self) -> None:
        state = SensorState(last_valid_hz=50.00, consecutive_rejections=c.DEBOUNCE_SAMPLES)
        assert is_sensor_degraded(state) is True
        _, state = validate(measured_hz=49.95, state=state, dt=1.0)
        assert is_sensor_degraded(state) is False
