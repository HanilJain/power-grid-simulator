"""Breaker (CB101) control logic.

Two layers, mirroring real protective-relay practice:

1. Protection (hard, predictive): before committing to a candidate breaker
   state, ask physics.py what frequency *would* result one step from now in
   that state. If it would breach the lower safety limit, force OPEN
   regardless of policy. This is what guarantees Design Assumption 3 ("CB101
   should never be CLOSED when frequency is below 49.50 Hz") and, together
   with the hysteresis margins, Assumption 2 (never actually entering an
   over/under-frequency condition).

2. Policy (soft, hysteresis): prefers CLOSED (Assumption 4 — "the station
   always needs power") but duty-cycles between REOPEN_LOW_HZ and
   RECLOSE_HIGH_HZ to avoid chattering the breaker open/closed every second
   at the nominal frequency, since the physics admits no steady closed
   state (see constants.py for the full rationale).

Protection always overrides policy.
"""

from __future__ import annotations

from backend.simulator import constants as c
from backend.simulator.physics import next_frequency


def protection_would_open(measured_frequency_hz: float, generator_on: bool) -> tuple[bool, str]:
    """Layer-1 predictive protection check, extracted for reuse.

    Always evaluates the CLOSED candidate one step ahead: if staying/going
    CLOSED would breach the lower safety limit, protection must force OPEN
    regardless of policy or operator intent. Returns (must_open, reason).
    reason is "" when protection does not need to intervene.

    Shared by `decide_breaker` (automatic hysteresis policy) and the manual
    single-step API (operator-driven breaker choice) so both paths enforce
    the exact same 49.50 Hz rule from a single source of truth. (The upper
    limit needs no equivalent predictive check here: HYSTERESIS_MARGIN_HZ
    safely exceeds MAX_PHYSICAL_RATE_HZ_PER_S, so a single step from
    RECLOSE_HIGH_HZ can't overshoot UPPER_LIMIT_HZ.)
    """
    would_be_freq_if_closed = next_frequency(measured_frequency_hz, generator_on, True)
    if would_be_freq_if_closed < c.LOWER_LIMIT_HZ:
        return True, (
            f"protection: closing/staying closed would drop frequency to "
            f"{would_be_freq_if_closed:.3f} Hz, below the {c.LOWER_LIMIT_HZ} Hz limit"
        )
    return False, ""


def decide_breaker(
    measured_frequency_hz: float,
    generator_on: bool,
    prev_breaker_closed: bool,
) -> tuple[bool, str]:
    """Decide CB101's next state given the latest (validated) frequency reading.

    Returns (breaker_should_be_closed, reason) where reason is a short
    human-readable explanation, primarily for logging/UI display.
    """
    # --- Layer 1: predictive protection -----------------------------------
    # Always evaluate the CLOSED candidate one step ahead, unconditionally
    # (regardless of prev_breaker_closed): both "stay closed" and "newly
    # close" require the same one-step lookahead, since either results in
    # CB101 being closed for the next step.
    must_open, reason = protection_would_open(measured_frequency_hz, generator_on)
    if must_open:
        return False, reason

    # --- Layer 2: hysteresis policy, prefers CLOSED -----------------------
    if prev_breaker_closed:
        if measured_frequency_hz <= c.REOPEN_LOW_HZ:
            return False, (
                f"policy: frequency decayed to {measured_frequency_hz:.3f} Hz "
                f"(<= reopen threshold {c.REOPEN_LOW_HZ} Hz); opening to recover"
            )
        return True, "policy: within hysteresis band, remaining closed (station powered)"

    # previously open
    if measured_frequency_hz >= c.RECLOSE_HIGH_HZ:
        return True, (
            f"policy: frequency recovered to {measured_frequency_hz:.3f} Hz "
            f"(>= reclose threshold {c.RECLOSE_HIGH_HZ} Hz); reclosing to restore power"
        )
    return False, "policy: not yet recovered enough to reclose; remaining open"
