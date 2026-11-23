"""Physical and control constants for the station's single-bus power system.

All values are taken directly from the task's Design Assumptions unless noted
as a control/engineering choice, in which case the rationale is documented
inline. Keeping every tunable value here (rather than scattered through the
code) makes the model's assumptions auditable in one place.
"""

from __future__ import annotations

# --- Nominal operating point & safety band (Design Assumption 1) ---------
NOMINAL_FREQUENCY_HZ: float = 50.00
UPPER_LIMIT_HZ: float = 50.50  # above this: over-frequency condition
LOWER_LIMIT_HZ: float = 49.50  # below this: under-frequency condition

# --- Frequency variation rates, Hz/second (Design Assumption 5) ----------
# Keyed by (generator_on, breaker_closed) -> rate of change in Hz/s.
# Positive = frequency rising, negative = frequency falling.
RATE_GEN_ON_CB_CLOSED: float = -0.05   # load drawing power
RATE_GEN_ON_CB_OPEN: float = 0.12      # generator spinning freely, no load
RATE_GEN_OFF_CB_CLOSED: float = -0.15  # load draining stored bus energy
RATE_GEN_OFF_CB_OPEN: float = 0.00     # no source, no load -> static

# Largest magnitude rate possible in any state. Used to size safety margins
# (e.g. hysteresis thresholds, sensor-jump validation) so that no single
# 1-second step can cross a margin without being caught by protection logic.
MAX_PHYSICAL_RATE_HZ_PER_S: float = max(
    abs(RATE_GEN_ON_CB_CLOSED),
    abs(RATE_GEN_ON_CB_OPEN),
    abs(RATE_GEN_OFF_CB_CLOSED),
    abs(RATE_GEN_OFF_CB_OPEN),
)  # == 0.15

# --- Discrete-time simulation step ----------------------------------------
TIME_STEP_S: float = 1.0  # task requires per-second discrete time steps

# --- Control policy thresholds (engineering choice, not given by the task) -
# The physics admits no steady CLOSED state: with G101=ON (the only state in
# which the station is actually powered) CB101=CLOSED always loses frequency
# and CB101=OPEN always gains it. A controller that simply reacts at the
# 49.50/50.50 limits would hug the boundary with no margin, and since CB101
# must NEVER be CLOSED below 49.50 Hz, the policy instead duty-cycles the
# breaker inside a tighter hysteresis band:
#   - reopen (give up load) once frequency decays to REOPEN_LOW_HZ
#   - reclose (restore load) once frequency recovers to RECLOSE_HIGH_HZ
# Both thresholds sit 0.20 Hz inside their respective hard limits -- safely
# above MAX_PHYSICAL_RATE_HZ_PER_S (0.15 Hz in one step) so a single 1 s step
# starting exactly at threshold cannot overshoot a hard limit before the next
# decision is made.
HYSTERESIS_MARGIN_HZ: float = 0.20
REOPEN_LOW_HZ: float = LOWER_LIMIT_HZ + HYSTERESIS_MARGIN_HZ   # 49.70
RECLOSE_HIGH_HZ: float = UPPER_LIMIT_HZ - HYSTERESIS_MARGIN_HZ  # 50.30

# --- Sensor fault-validation (engineering choice) -------------------------
# A reading implying a rate steeper than physically possible cannot be real;
# it's a sensor fault, not a true frequency change. This directly models and
# defends against the "faulty frequency reading caused an unnecessary trip"
# incident described in the task background.
SENSOR_VALIDATION_SLACK_HZ_PER_S: float = 0.02  # tolerance above max physical rate
MAX_PLAUSIBLE_DELTA_HZ: float = (
    MAX_PHYSICAL_RATE_HZ_PER_S + SENSOR_VALIDATION_SLACK_HZ_PER_S
) * TIME_STEP_S  # == 0.17 Hz per 1 s step

# Consecutive out-of-band *validated* readings required before the
# controller is allowed to react to an excursion. Guards against a single
# noisy sample (that still passes the plausibility check) causing a
# spurious trip, mirroring real protective-relay debounce practice.
DEBOUNCE_SAMPLES: int = 2
