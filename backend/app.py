"""Flask application: serves the frontend and the simulation API.

Routes:
  GET  /                    -> frontend/index.html (explicit route below)
  GET  /<other assets>      -> other frontend files (style.css, app.js, ...),
                               served automatically by Flask's built-in
                               static handler (static_folder=frontend dir,
                               static_url_path=""); not a custom route here,
                               so Flask's own safe path resolution applies
  GET  /api/simulate        -> run a simulation, return JSON timeseries
  GET  /api/scenarios       -> list available scenarios + descriptions

Input validation happens here, at the system boundary, per the project's
coding-style rule: never trust external data. All query parameters are
parsed and range-checked before being handed to SimConfig/run_simulation.
"""

from __future__ import annotations

import os

from flask import Flask, Response, jsonify, request, send_from_directory

from backend.simulator import constants as c
from backend.simulator.engine import run_simulation
from backend.simulator.models import SimConfig
from backend.simulator.scenarios import SCENARIOS, describe_scenario

_FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")

app = Flask(__name__, static_folder=_FRONTEND_DIR, static_url_path="")

# --- Input bounds (defensive limits, not given by the task but required by
# the security/coding-style rules: validate all external input, fail fast).
MIN_DURATION_S = 10
MAX_DURATION_S = 3600
MIN_INITIAL_FREQUENCY_HZ = 40.0
MAX_INITIAL_FREQUENCY_HZ = 60.0


class ValidationError(ValueError):
    """Raised when a request parameter fails validation."""


def _parse_duration(raw: str | None) -> int:
    if raw is None:
        return 300
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"duration must be an integer, got {raw!r}") from exc
    if not (MIN_DURATION_S <= value <= MAX_DURATION_S):
        raise ValidationError(
            f"duration must be between {MIN_DURATION_S} and {MAX_DURATION_S} seconds"
        )
    return value


def _parse_initial_frequency(raw: str | None) -> float:
    if raw is None:
        return c.NOMINAL_FREQUENCY_HZ
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"initial_frequency must be a number, got {raw!r}") from exc
    if not (MIN_INITIAL_FREQUENCY_HZ <= value <= MAX_INITIAL_FREQUENCY_HZ):
        raise ValidationError(
            f"initial_frequency must be between {MIN_INITIAL_FREQUENCY_HZ} "
            f"and {MAX_INITIAL_FREQUENCY_HZ} Hz"
        )
    return value


def _parse_scenario(raw: str | None) -> str:
    value = raw or "normal"
    if value not in SCENARIOS:
        raise ValidationError(f"scenario must be one of {SCENARIOS}, got {value!r}")
    return value


def _parse_event_time(raw: str | None, duration_s: int, default: int) -> int:
    if raw is None:
        return min(default, max(duration_s - 1, 0))
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"event time must be an integer, got {raw!r}") from exc
    if not (0 <= value < duration_s):
        raise ValidationError(f"event time must be within [0, {duration_s})")
    return value


def _parse_bool(raw: str | None) -> bool:
    return str(raw).strip().lower() in {"1", "true", "yes", "on"} if raw is not None else False


@app.get("/api/scenarios")
def list_scenarios() -> Response:
    return jsonify(
        {
            "success": True,
            "data": [{"name": name, "description": describe_scenario(name)} for name in SCENARIOS],
            "error": None,
        }
    )


@app.get("/api/simulate")
def simulate() -> tuple[Response, int] | Response:
    try:
        duration_s = _parse_duration(request.args.get("duration"))
        initial_frequency_hz = _parse_initial_frequency(request.args.get("initial_frequency"))
        scenario = _parse_scenario(request.args.get("scenario"))
        fault_time_s = _parse_event_time(request.args.get("fault_time"), duration_s, default=60)
        generator_failure_time_s = _parse_event_time(
            request.args.get("generator_failure_time"), duration_s, default=60
        )
        disable_sensor_validation = _parse_bool(request.args.get("disable_sensor_validation"))
    except ValidationError as exc:
        return jsonify({"success": False, "data": None, "error": str(exc)}), 400

    config = SimConfig(
        duration_s=duration_s,
        initial_frequency_hz=initial_frequency_hz,
        scenario=scenario,
        fault_time_s=fault_time_s,
        generator_failure_time_s=generator_failure_time_s,
        disable_sensor_validation=disable_sensor_validation,
    )

    try:
        result = run_simulation(config)
    except Exception as exc:  # noqa: BLE001 - convert any internal error to a safe envelope
        app.logger.exception("simulation failed")
        return jsonify({"success": False, "data": None, "error": "simulation failed internally"}), 500

    payload = {
        "config": {
            "duration_s": config.duration_s,
            "initial_frequency_hz": config.initial_frequency_hz,
            "scenario": config.scenario,
            "fault_time_s": config.fault_time_s,
            "generator_failure_time_s": config.generator_failure_time_s,
            "disable_sensor_validation": config.disable_sensor_validation,
        },
        "limits": {
            "nominal_hz": c.NOMINAL_FREQUENCY_HZ,
            "lower_hz": c.LOWER_LIMIT_HZ,
            "upper_hz": c.UPPER_LIMIT_HZ,
        },
        "summary": {
            "powered_fraction": result.powered_fraction,
            "trip_count": result.trip_count,
            "limit_violations": result.limit_violations,
        },
        "steps": [
            {
                "t": s.t,
                "true_frequency_hz": s.true_frequency_hz,
                "measured_frequency_hz": s.measured_frequency_hz,
                "generator_on": s.generator_on,
                "breaker_closed": s.breaker_closed,
                "sensor_fault_injected": s.sensor_fault_injected,
                "reading_rejected": s.reading_rejected,
                "sensor_degraded": s.sensor_degraded,
                "tripped": s.tripped,
                "note": s.note,
            }
            for s in result.steps
        ],
    }
    return jsonify({"success": True, "data": payload, "error": None})


@app.get("/")
def index() -> Response:
    return send_from_directory(_FRONTEND_DIR, "index.html")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
