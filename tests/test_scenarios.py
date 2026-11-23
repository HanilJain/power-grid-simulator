"""Tests for scenario-specific wiring (backend/simulator/scenarios.py).

Written first (RED). Thin layer on top of engine.py -- mostly validates
that each named scenario produces the state transitions it claims to.
"""

from __future__ import annotations

from backend.simulator.models import SimConfig
from backend.simulator.scenarios import SCENARIOS, describe_scenario


class TestScenarioRegistry:
    def test_all_three_required_scenarios_are_registered(self) -> None:
        assert set(SCENARIOS) == {"normal", "generator_failure", "faulty_sensor"}

    def test_describe_scenario_returns_nonempty_text_for_each(self) -> None:
        for name in SCENARIOS:
            text = describe_scenario(name)
            assert isinstance(text, str)
            assert len(text) > 0

    def test_describe_unknown_scenario_raises(self) -> None:
        import pytest

        with pytest.raises(KeyError):
            describe_scenario("not_a_real_scenario")


class TestDefaultConfigsAreValid:
    def test_normal_default_config_constructs(self) -> None:
        cfg = SimConfig(scenario="normal")
        assert cfg.scenario == "normal"

    def test_generator_failure_default_config_constructs(self) -> None:
        cfg = SimConfig(scenario="generator_failure")
        assert cfg.scenario == "generator_failure"

    def test_faulty_sensor_default_config_constructs(self) -> None:
        cfg = SimConfig(scenario="faulty_sensor")
        assert cfg.scenario == "faulty_sensor"
