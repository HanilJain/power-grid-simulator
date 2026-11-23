"""Physical model of bus frequency behaviour.

Single source of truth for Design Assumption 5's rate table. Both the
simulation engine (to advance true frequency) and the protection logic in
control.py (to predict whether a candidate breaker state would cross a
limit before committing to it) import `rate_for` / `next_frequency` from
here so the two can never disagree about the physics.
"""

from __future__ import annotations

from backend.simulator import constants as c


def rate_for(generator_on: bool, breaker_closed: bool) -> float:
    """Return the bus frequency rate of change (Hz/s) for a given state.

    Pure lookup over the four (generator, breaker) combinations defined in
    Design Assumption 5. No side effects, no hidden state.
    """
    if generator_on and breaker_closed:
        return c.RATE_GEN_ON_CB_CLOSED
    if generator_on and not breaker_closed:
        return c.RATE_GEN_ON_CB_OPEN
    if not generator_on and breaker_closed:
        return c.RATE_GEN_OFF_CB_CLOSED
    return c.RATE_GEN_OFF_CB_OPEN


def next_frequency(
    frequency_hz: float,
    generator_on: bool,
    breaker_closed: bool,
    dt: float = c.TIME_STEP_S,
) -> float:
    """Advance the bus frequency by `dt` seconds under forward-Euler integration.

    Returns a new float; never mutates the input (floats are immutable in
    Python, but this keeps the function's contract explicit and testable).
    """
    rate = rate_for(generator_on, breaker_closed)
    return frequency_hz + rate * dt
