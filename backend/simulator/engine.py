"""Discrete-time simulation engine.

Runs the station model in 1-second steps for a configurable duration
(Functional Requirement 1). At every step:

  1. Apply any scenario-scripted event for this instant (generator trip,
     sensor fault injection).
  2. FS101 produces a reading of the current true frequency (`sensor.measure`).
  3. Optionally validate that reading against physics before trusting it
     (`sensor.validate`) -- this is what prevents a faulty reading alone
     from causing an unnecessary trip.
  4. The controller decides CB101's next state from the (validated) reading
     (`control.decide_breaker`).
  5. True frequency is advanced one step under the *actual* generator/breaker
     state (`physics.next_frequency`) -- never under the possibly-wrong
     measured state, since the bus doesn't care what the sensor reported.

Every step produces a new, immutable StepResult; nothing is mutated in
place.
"""

from __future__ import annotations

from backend.simulator import constants as c
from backend.simulator.control import decide_breaker
from backend.simulator.models import SimConfig, SimulationResult, StepResult
from backend.simulator.physics import next_frequency
from backend.simulator.sensor import SensorState, is_sensor_degraded, measure, validate


def run_simulation(config: SimConfig) -> SimulationResult:
    """Run one full simulation and return the complete step trace."""
    true_frequency = config.initial_frequency_hz
    generator_on = config.generator_initially_on
    breaker_closed = config.breaker_initially_closed
    sensor_state = SensorState(last_valid_hz=config.initial_frequency_hz)

    steps: list[StepResult] = []

    for t in range(config.duration_s):
        # 1. Scenario-scripted events for this instant.
        if config.scenario == "generator_failure" and t == config.generator_failure_time_s:
            generator_on = False

        # 2. Sensor reading (possibly faulted).
        raw_reading, fault_injected = measure(
            true_frequency_hz=true_frequency,
            t=t,
            scenario=config.scenario,
            fault_time_s=config.fault_time_s,
        )

        # 3. Optional validation against physics.
        reading_rejected = False
        sensor_degraded = False
        if config.disable_sensor_validation:
            effective_reading = raw_reading
        else:
            accepted, sensor_state = validate(raw_reading, sensor_state)
            reading_rejected = not accepted
            sensor_degraded = is_sensor_degraded(sensor_state)
            effective_reading = sensor_state.last_valid_hz

        # 4. Controller decision from the (validated) reading.
        prev_closed = breaker_closed
        new_breaker_closed, reason = decide_breaker(
            measured_frequency_hz=effective_reading,
            generator_on=generator_on,
            prev_breaker_closed=breaker_closed,
        )
        # A "trip" is a protection-driven opening (abnormal: the limit was
        # genuinely at risk) -- not routine hysteresis reopening, which is
        # expected duty-cycling behaviour under Assumption 4/5 and happens
        # continuously even in the normal scenario.
        tripped = prev_closed and not new_breaker_closed and reason.startswith("protection")
        breaker_closed = new_breaker_closed

        steps.append(
            StepResult(
                t=t,
                true_frequency_hz=true_frequency,
                measured_frequency_hz=raw_reading,
                generator_on=generator_on,
                breaker_closed=breaker_closed,
                sensor_fault_injected=fault_injected,
                reading_rejected=reading_rejected,
                sensor_degraded=sensor_degraded,
                tripped=tripped,
                note=reason,
            )
        )

        # 5. Advance true frequency under the *actual* physical state.
        true_frequency = next_frequency(
            true_frequency, generator_on, breaker_closed, dt=c.TIME_STEP_S
        )

    return SimulationResult(config=config, steps=tuple(steps))
