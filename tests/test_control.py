"""Tests for the breaker control logic (backend/simulator/control.py).

Written first (RED), encoding the task's hard constraints directly:
  - CB101 must NEVER be CLOSED when frequency is below 49.50 Hz (Assumption 3)
  - CB101 should be CLOSED whenever feasible (Assumption 4)
  - The system must never actually cross into over/under-frequency
    (Assumption 2) -- enforced here via the predictive-protection check.
"""

from __future__ import annotations

import pytest

from backend.simulator import constants as c
from backend.simulator.control import decide_breaker


class TestProtectionNeverViolatesLowerLimit:
    def test_refuses_to_stay_closed_if_it_would_cross_lower_limit(self) -> None:
        # At 49.54 Hz, gen ON + CB CLOSED would drop 0.05 -> 49.49 (violation).
        closed, reason = decide_breaker(
            measured_frequency_hz=49.54, generator_on=True, prev_breaker_closed=True
        )
        assert closed is False
        assert "protection" in reason.lower()

    def test_refuses_to_close_if_it_would_cross_lower_limit(self) -> None:
        closed, _reason = decide_breaker(
            measured_frequency_hz=49.52, generator_on=True, prev_breaker_closed=False
        )
        assert closed is False

    def test_generator_off_forces_open_near_lower_limit(self) -> None:
        # gen OFF + CB CLOSED drops at 0.15 Hz/s -- a much larger margin is
        # needed; protection must still catch it.
        closed, _reason = decide_breaker(
            measured_frequency_hz=49.60, generator_on=False, prev_breaker_closed=True
        )
        assert closed is False

    def test_never_returns_closed_at_or_below_hard_lower_limit(self) -> None:
        for freq in (49.50, 49.49, 49.00, 48.00):
            closed, _ = decide_breaker(
                measured_frequency_hz=freq, generator_on=True, prev_breaker_closed=True
            )
            assert closed is False, f"must not be closed at {freq} Hz"


class TestPolicyPrefersClosed:
    def test_stays_closed_at_nominal_frequency(self) -> None:
        closed, _reason = decide_breaker(
            measured_frequency_hz=50.00, generator_on=True, prev_breaker_closed=True
        )
        assert closed is True

    def test_recloses_once_recovered_to_reclose_high(self) -> None:
        closed, reason = decide_breaker(
            measured_frequency_hz=c.RECLOSE_HIGH_HZ,
            generator_on=True,
            prev_breaker_closed=False,
        )
        assert closed is True
        assert "polic" in reason.lower() or "hyster" in reason.lower()

    def test_stays_open_below_reclose_high_after_opening(self) -> None:
        closed, _ = decide_breaker(
            measured_frequency_hz=50.10,
            generator_on=True,
            prev_breaker_closed=False,
        )
        assert closed is False  # not yet recovered enough; avoid chattering

    def test_reopens_once_decayed_to_reopen_low(self) -> None:
        closed, reason = decide_breaker(
            measured_frequency_hz=c.REOPEN_LOW_HZ,
            generator_on=True,
            prev_breaker_closed=True,
        )
        assert closed is False

    def test_stays_closed_above_reopen_low_even_if_falling(self) -> None:
        closed, _ = decide_breaker(
            measured_frequency_hz=49.80,
            generator_on=True,
            prev_breaker_closed=True,
        )
        assert closed is True  # hysteresis: don't give up load prematurely


class TestHysteresisAvoidsChatter:
    def test_does_not_oscillate_at_a_single_midband_frequency(self) -> None:
        # At the same measured frequency, the decision should depend on the
        # previous state (hysteresis), not flip-flop.
        mid = 50.00
        closed_from_closed, _ = decide_breaker(mid, True, prev_breaker_closed=True)
        closed_from_open, _ = decide_breaker(mid, True, prev_breaker_closed=False)
        assert closed_from_closed is True
        assert closed_from_open is False


class TestGeneratorOffScenario:
    def test_opens_before_crossing_limit_when_generator_off(self) -> None:
        # gen OFF + CB CLOSED => -0.15 Hz/s; starting well above 49.50 should
        # still stay closed, but once close enough protection must trip.
        closed_far, _ = decide_breaker(49.80, generator_on=False, prev_breaker_closed=True)
        closed_near, _ = decide_breaker(49.63, generator_on=False, prev_breaker_closed=True)
        assert closed_far is True
        assert closed_near is False
