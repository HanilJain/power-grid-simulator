"""Immutable data structures shared across the simulator.

All records are frozen dataclasses: every simulation step produces a new
StepResult rather than mutating a previous one, per the project's
immutability convention.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from backend.simulator import constants as c

Scenario = Literal["normal", "generator_failure", "faulty_sensor"]


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
