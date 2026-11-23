"""Tests for the physical frequency-rate model (backend/simulator/physics.py).

Written first (RED) against the exact rate table in the task's Design
Assumption 5, before physics.py exists.
"""

from __future__ import annotations

import pytest

from backend.simulator import constants as c
from backend.simulator.physics import next_frequency, rate_for


class TestRateFor:
    def test_generator_on_breaker_closed_drops_at_point_zero_five(self) -> None:
        assert rate_for(generator_on=True, breaker_closed=True) == pytest.approx(-0.05)

    def test_generator_on_breaker_open_rises_at_point_one_two(self) -> None:
        assert rate_for(generator_on=True, breaker_closed=False) == pytest.approx(0.12)

    def test_generator_off_breaker_closed_drops_at_point_one_five(self) -> None:
        assert rate_for(generator_on=False, breaker_closed=True) == pytest.approx(-0.15)

    def test_generator_off_breaker_open_is_static(self) -> None:
        assert rate_for(generator_on=False, breaker_closed=False) == pytest.approx(0.0)

    def test_rates_match_constants_module(self) -> None:
        assert rate_for(True, True) == c.RATE_GEN_ON_CB_CLOSED
        assert rate_for(True, False) == c.RATE_GEN_ON_CB_OPEN
        assert rate_for(False, True) == c.RATE_GEN_OFF_CB_CLOSED
        assert rate_for(False, False) == c.RATE_GEN_OFF_CB_OPEN


class TestNextFrequency:
    def test_one_second_step_gen_on_cb_closed(self) -> None:
        assert next_frequency(50.00, True, True, dt=1.0) == pytest.approx(49.95)

    def test_one_second_step_gen_on_cb_open(self) -> None:
        assert next_frequency(50.00, True, False, dt=1.0) == pytest.approx(50.12)

    def test_one_second_step_gen_off_cb_closed(self) -> None:
        assert next_frequency(50.00, False, True, dt=1.0) == pytest.approx(49.85)

    def test_one_second_step_gen_off_cb_open_unchanged(self) -> None:
        assert next_frequency(50.00, False, False, dt=1.0) == pytest.approx(50.00)

    def test_half_second_step_scales_rate_linearly(self) -> None:
        assert next_frequency(50.00, True, True, dt=0.5) == pytest.approx(49.975)

    def test_default_dt_is_one_second(self) -> None:
        assert next_frequency(50.00, True, True) == pytest.approx(49.95)

    def test_is_pure_function_does_not_mutate_inputs(self) -> None:
        # floats are immutable in Python, but assert the call has no side
        # effects by calling twice and expecting identical results.
        first = next_frequency(50.00, True, True)
        second = next_frequency(50.00, True, True)
        assert first == second
