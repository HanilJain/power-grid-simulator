"""Immutable data structures shared across the simulator.

All records are frozen dataclasses: every simulation step produces a new
StepResult rather than mutating a previous one, per the project's
immutability convention.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from backend.simulator import constants as c

Scenario = Literal["normal", "generator_failure", "faulty_sensor", "manual"]
ManualAction = Literal["generator_on", "generator_off", "breaker_open", "breaker_close"]


@dataclass(frozen=True)
class ManualEvent:
    """One scripted operator action fired at a specific second.

    Used only by the "manual" scenario (see scenarios.py/engine.py): a
    sequence of these lets a user script a custom run (e.g. "open CB101 at
    t=30") instead of picking one of the fixed preset scenarios.
    """

    t: int
    action: ManualAction


@dataclass(frozen=True)
class SimConfig:
    """User-configurable parameters for one simulation run."""

    duration_s: int = 300
    initial_frequency_hz: float = 50.00
    scenario: Scenario = "normal"
    generator_initially_on: bool = True
    breaker_initially_closed: bool = True
    # faulty_sensor scenario only: second at which the bad reading fires
    fault_time_s: int = 60
    # generator_failure scenario only: second at which G101 trips to OFF
    generator_failure_time_s: int = 60
    # faulty_sensor scenario only: run with validation disabled (reproduces
    # the "unnecessary breaker trip" incident) rather than the protected path
    disable_sensor_validation: bool = False
    # manual scenario only: scripted operator actions, applied in engine.py
    # at the step where event.t matches the current second
    events: tuple[ManualEvent, ...] = field(default_factory=tuple)
    # manual scenario only: whether predictive protection (control.py's
    # protection_would_open) may override an operator's forced CLOSE that
    # would breach the lower frequency limit. True (default) mirrors the
    # automatic scenarios' always-on protection; False lets an operator
    # intentionally drive the system into a limit violation, e.g. to see
    # what happens without the safety net.
    protection_enabled: bool = True


@dataclass(frozen=True)
class StepResult:
    """Snapshot of system state at one discrete time step."""

    t: int
    true_frequency_hz: float
    measured_frequency_hz: float
    generator_on: bool
    breaker_closed: bool
    sensor_fault_injected: bool = False
    reading_rejected: bool = False
    sensor_degraded: bool = False
    tripped: bool = False
    note: str = ""
    # manual scenario / /api/step only: True when protection overrode an
    # operator-requested CLOSE (forced OPEN instead). Always False for the
    # automatic scenarios, where there is no separate "operator intent" to
    # override -- decide_breaker's own choice already reflects protection.
    protection_overridden: bool = False


@dataclass(frozen=True)
class SimulationResult:
    """Full output of a simulation run: config plus the ordered step trace."""

    config: SimConfig
    steps: tuple[StepResult, ...] = field(default_factory=tuple)

    @property
    def powered_fraction(self) -> float:
        """Fraction of steps during which CB101 was CLOSED (station powered)."""
        if not self.steps:
            return 0.0
        closed_count = sum(1 for s in self.steps if s.breaker_closed)
        return closed_count / len(self.steps)

    @property
    def trip_count(self) -> int:
        return sum(1 for s in self.steps if s.tripped)

    @property
    def limit_violations(self) -> int:
        """Count of steps where true frequency breached the safe band.

        Should always be zero for a correctly behaving controller; exposed
        so tests and the UI can assert/display this invariant directly.
        """
        return sum(
            1
            for s in self.steps
            if s.true_frequency_hz < c.LOWER_LIMIT_HZ or s.true_frequency_hz > c.UPPER_LIMIT_HZ
        )
