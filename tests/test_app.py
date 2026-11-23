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


class TestScenariosEndpoint:
    def test_lists_all_three_scenarios(self, client) -> None:
        resp = client.get("/api/scenarios")
        assert resp.status_code == 200
        body = resp.get_json()
        names = {s["name"] for s in body["data"]}
        assert names == {"normal", "generator_failure", "faulty_sensor"}


class TestIndexRoute:
    def test_serves_index_html(self, client) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"<html" in resp.data.lower() or b"<!doctype" in resp.data.lower()
