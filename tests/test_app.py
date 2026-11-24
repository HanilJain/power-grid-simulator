"""Tests for the Flask API (backend/app.py).

Uses Flask's test client -- no real server/socket needed.
"""

from __future__ import annotations

import pytest

from backend.app import app


@pytest.fixture()
def client():
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


class TestSimulateEndpoint:
    def test_default_normal_run_succeeds(self, client) -> None:
        resp = client.get("/api/simulate")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["error"] is None
        assert len(body["data"]["steps"]) == 300  # default duration

    def test_rejects_non_numeric_duration(self, client) -> None:
        resp = client.get("/api/simulate?duration=banana")
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["success"] is False
        assert "duration" in body["error"]

    def test_rejects_duration_out_of_bounds(self, client) -> None:
        resp = client.get("/api/simulate?duration=999999")
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["success"] is False
        assert "duration" in body["error"]

    def test_rejects_unknown_scenario(self, client) -> None:
        resp = client.get("/api/simulate?scenario=not_real")
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["success"] is False

    def test_rejects_non_numeric_initial_frequency(self, client) -> None:
        resp = client.get("/api/simulate?initial_frequency=banana")
        assert resp.status_code == 400

    def test_rejects_initial_frequency_out_of_bounds(self, client) -> None:
        resp = client.get("/api/simulate?initial_frequency=999")
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["success"] is False
        assert "initial_frequency" in body["error"]

    def test_rejects_non_numeric_fault_time(self, client) -> None:
        resp = client.get("/api/simulate?scenario=faulty_sensor&fault_time=abc")
        assert resp.status_code == 400

    def test_rejects_fault_time_outside_duration(self, client) -> None:
        resp = client.get("/api/simulate?scenario=faulty_sensor&duration=50&fault_time=50")
        assert resp.status_code == 400
        body = resp.get_json()
        assert "event time" in body["error"]

    def test_rejects_negative_generator_failure_time(self, client) -> None:
        resp = client.get(
            "/api/simulate?scenario=generator_failure&generator_failure_time=-5"
        )
        assert resp.status_code == 400

    def test_internal_error_returns_safe_500_envelope(self, client, monkeypatch) -> None:
        def boom(_config):
            raise RuntimeError("something broke internally with a secret detail")

        monkeypatch.setattr("backend.app.run_simulation", boom)
        resp = client.get("/api/simulate")
        assert resp.status_code == 500
        body = resp.get_json()
        assert body["success"] is False
        assert body["data"] is None
        # the real exception message must never reach the client
        assert "secret" not in body["error"]
        assert body["error"] == "simulation failed internally"

    def test_faulty_sensor_scenario_returns_fault_metadata(self, client) -> None:
        resp = client.get(
            "/api/simulate?scenario=faulty_sensor&duration=120&fault_time=50"
        )
        assert resp.status_code == 200
        body = resp.get_json()
        steps = body["data"]["steps"]
        assert steps[50]["sensor_fault_injected"] is True

    def test_response_includes_limits(self, client) -> None:
        resp = client.get("/api/simulate?duration=30")
        body = resp.get_json()
        assert body["data"]["limits"]["lower_hz"] == 49.50
        assert body["data"]["limits"]["upper_hz"] == 50.50


class TestManualScenarioEndpoint:
    def test_accepts_a_valid_events_payload(self, client) -> None:
        resp = client.get(
            '/api/simulate?scenario=manual&duration=10&events=[{"t":5,"action":"breaker_open"}]'
        )
        assert resp.status_code == 200
        body = resp.get_json()
        steps = body["data"]["steps"]
        assert steps[4]["breaker_closed"] is True
        assert steps[5]["breaker_closed"] is False
        assert body["data"]["config"]["events"] == [{"t": 5, "action": "breaker_open"}]

    def test_defaults_to_no_events_and_protection_enabled(self, client) -> None:
        resp = client.get("/api/simulate?scenario=manual&duration=10")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["config"]["events"] == []
        assert body["data"]["config"]["protection_enabled"] is True

    def test_protection_enabled_false_is_honored(self, client) -> None:
        resp = client.get(
            "/api/simulate?scenario=manual&duration=10&initial_frequency=49.54"
            "&protection_enabled=false"
        )
        body = resp.get_json()
        assert body["data"]["config"]["protection_enabled"] is False

    def test_rejects_malformed_events_json(self, client) -> None:
        resp = client.get("/api/simulate?scenario=manual&events=not-json")
        assert resp.status_code == 400
        body = resp.get_json()
        assert "events" in body["error"]

    def test_rejects_events_not_a_list(self, client) -> None:
        resp = client.get('/api/simulate?scenario=manual&events={"t":1}')
        assert resp.status_code == 400

    def test_rejects_event_missing_required_keys(self, client) -> None:
        resp = client.get('/api/simulate?scenario=manual&duration=10&events=[{"t":1}]')
        assert resp.status_code == 400
        body = resp.get_json()
        assert "events[0]" in body["error"]

    def test_rejects_event_with_non_integer_t(self, client) -> None:
        resp = client.get(
            '/api/simulate?scenario=manual&duration=10&events=[{"t":"soon","action":"breaker_open"}]'
        )
        assert resp.status_code == 400

    def test_rejects_event_with_unknown_action(self, client) -> None:
        resp = client.get(
            '/api/simulate?scenario=manual&duration=10&events=[{"t":1,"action":"explode"}]'
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert "action" in body["error"]

    def test_rejects_event_time_outside_duration(self, client) -> None:
        resp = client.get(
            '/api/simulate?scenario=manual&duration=5&events=[{"t":10,"action":"breaker_open"}]'
        )
        assert resp.status_code == 400

    def test_rejects_too_many_events(self, client) -> None:
        import json as json_module

        events = [{"t": i % 100, "action": "breaker_open"} for i in range(201)]
        resp = client.get(
            f"/api/simulate?scenario=manual&duration=200&events={json_module.dumps(events)}"
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert "200" in body["error"]


class TestStepEndpoint:
    def test_honors_operator_intent_when_safe(self, client) -> None:
        resp = client.post(
            "/api/step",
            json={
                "current_frequency_hz": 50.00,
                "generator_on": True,
                "breaker_closed": True,
                "protection_enabled": True,
            },
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["breaker_closed"] is True
        assert body["data"]["protection_overridden"] is False
        assert body["data"]["next_frequency_hz"] == pytest.approx(49.95)

    def test_protection_overrides_unsafe_close(self, client) -> None:
        resp = client.post(
            "/api/step",
            json={
                "current_frequency_hz": 49.54,
                "generator_on": True,
                "breaker_closed": True,
                "protection_enabled": True,
            },
        )
        body = resp.get_json()
        assert body["data"]["breaker_closed"] is False
        assert body["data"]["protection_overridden"] is True
        assert body["data"]["tripped"] is True

    def test_protection_disabled_allows_unsafe_close(self, client) -> None:
        resp = client.post(
            "/api/step",
            json={
                "current_frequency_hz": 49.54,
                "generator_on": True,
                "breaker_closed": True,
                "protection_enabled": False,
            },
        )
        body = resp.get_json()
        assert body["data"]["breaker_closed"] is True
        assert body["data"]["protection_overridden"] is False

    def test_rejects_non_json_body(self, client) -> None:
        resp = client.post("/api/step", data="not json")
        assert resp.status_code == 400

    def test_rejects_missing_field(self, client) -> None:
        resp = client.post(
            "/api/step",
            json={"current_frequency_hz": 50.0, "generator_on": True},
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["success"] is False

    def test_rejects_out_of_bounds_frequency(self, client) -> None:
        resp = client.post(
            "/api/step",
            json={
                "current_frequency_hz": 999.0,
                "generator_on": True,
                "breaker_closed": True,
                "protection_enabled": True,
            },
        )
        assert resp.status_code == 400

    def test_rejects_non_boolean_generator_on(self, client) -> None:
        resp = client.post(
            "/api/step",
            json={
                "current_frequency_hz": 50.0,
                "generator_on": "yes",
                "breaker_closed": True,
                "protection_enabled": True,
            },
        )
        assert resp.status_code == 400


class TestScenariosEndpoint:
    def test_lists_all_scenarios(self, client) -> None:
        resp = client.get("/api/scenarios")
        assert resp.status_code == 200
        body = resp.get_json()
        names = {s["name"] for s in body["data"]}
        assert names == {"normal", "generator_failure", "faulty_sensor", "manual"}


class TestIndexRoute:
    def test_serves_index_html(self, client) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"<html" in resp.data.lower() or b"<!doctype" in resp.data.lower()
