"""FS101 frequency sensor model: measurement, fault injection, and validation.

This module directly models the incident described in the task background:
"a faulty frequency reading caused an unnecessary breaker trip at 2am."

- `measure` represents FS101's raw output. In the "faulty_sensor" scenario it
  injects a single false low reading at a configured instant, independent of
  the true bus frequency -- exactly the failure mode described (a bad
  reading, not a real under-frequency event).
- `validate` is the fix: a physics-based plausibility check. No real bus
  frequency can jump faster than MAX_PHYSICAL_RATE_HZ_PER_S, so any reading
  implying a steeper change must be a sensor fault and is rejected in favor
  of the last trusted value.
- Rejections are tracked via `SensorState.consecutive_rejections` and
  actually consulted by `is_sensor_degraded`: a single rejected reading is
  silently absorbed (the controller just keeps using the last trusted
  value, as in the faulty_sensor scenario), but a *run* of
  DEBOUNCE_SAMPLES-or-more consecutive rejections means FS101 itself is
  persistently misbehaving (not a one-off spike) and is surfaced to the
  engine/UI as a degraded-sensor condition, distinct from an ordinary single
  rejected sample.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.simulator import constants as c

# How far below the lower limit the injected faulty reading lands. Large
# enough to look, at face value, like a clear-cut under-frequency trip --
# matching "faulty reading caused an unnecessary breaker trip" -- while
# being an obviously impossible single-step jump from a nominal true value.
INJECTED_FAULT_READING_HZ: float = 49.00


def measure(
    true_frequency_hz: float,
    t: int,
    scenario: str,
    fault_time_s: int = 60,
) -> tuple[float, bool]:
    """Return (reading_hz, fault_injected) for FS101 at time t.

    Under "faulty_sensor", exactly at fault_time_s the sensor reports a
    fixed bogus low value regardless of the true frequency. All other
    scenarios/timesteps report the true frequency unmodified (an idealized,
    noise-free sensor otherwise).
    """
    if scenario == "faulty_sensor" and t == fault_time_s:
        return INJECTED_FAULT_READING_HZ, True
    return true_frequency_hz, False


@dataclass(frozen=True)
class SensorState:
    """Validator's running memory: last trusted reading + rejection streak."""

    last_valid_hz: float
    consecutive_rejections: int = 0


def validate(
    measured_hz: float,
    state: SensorState,
    dt: float = c.TIME_STEP_S,
) -> tuple[bool, SensorState]:
    """Physics-based plausibility check for a new FS101 reading.

    Returns (accepted, new_state). new_state is always a fresh object
    (immutable) -- `state` itself is never mutated. When a reading is
    rejected, `last_valid_hz` is carried forward unchanged: the controller
    should keep acting on the last trusted value rather than the implausible
    one.
    """
    delta = abs(measured_hz - state.last_valid_hz)
    plausible_delta = c.MAX_PLAUSIBLE_DELTA_HZ * (dt / c.TIME_STEP_S)
    float_epsilon = 1e-9  # guards against float-arithmetic boundary jitter

    if delta <= plausible_delta + float_epsilon:
        return True, SensorState(last_valid_hz=measured_hz, consecutive_rejections=0)

    return False, SensorState(
        last_valid_hz=state.last_valid_hz,
        consecutive_rejections=state.consecutive_rejections + 1,
    )


def is_sensor_degraded(state: SensorState) -> bool:
    """True once rejections have persisted for DEBOUNCE_SAMPLES in a row.

    A single rejected reading is expected/handled transparently (the
    validator already falls back to the last trusted value). This flag is
    for the stronger claim that FS101 itself is faulty on an ongoing basis,
    not just a one-off noisy sample -- debounced so a single implausible
    reading can't trigger it alone.
    """
    return state.consecutive_rejections >= c.DEBOUNCE_SAMPLES
