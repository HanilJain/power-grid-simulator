"""Flask application: serves the frontend and the simulation API.

Routes:
  GET  /                    -> frontend/index.html (explicit route below)
  GET  /<other assets>      -> other frontend files (style.css, app.js, ...),
                               served automatically by Flask's built-in
                               static handler (static_folder=frontend dir,
                               static_url_path=""); not a custom route here,
                               so Flask's own safe path resolution applies
  GET  /api/simulate        -> run a simulation, return JSON timeseries
                               (scenario=manual accepts a scripted "events"
                               list -- see _parse_events below)
  POST /api/step             -> advance one manual/live step given the
                               operator's current state + intended action
  GET  /api/scenarios        -> list available scenarios + descriptions

Input validation happens here, at the system boundary, per the project's
coding-style rule: never trust external data. All query parameters/JSON
fields are parsed and range-checked before being handed to
SimConfig/run_simulation or the control/physics functions directly.
"""

from __future__ import annotations

import json
import os

from flask import Flask, Response, jsonify, request, send_from_directory

from backend.simulator import constants as c
from backend.simulator.control import protection_would_open
from backend.simulator.engine import run_simulation
from backend.simulator.models import ManualEvent, SimConfig
from backend.simulator.physics import next_frequency
from backend.simulator.scenarios import SCENARIOS, describe_scenario

_FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")

app = Flask(__name__, static_folder=_FRONTEND_DIR, static_url_path="")

# --- Input bounds (defensive limits, not given by the task but required by
# the security/coding-style rules: validate all external input, fail fast).
MIN_DURATION_S = 10
MAX_DURATION_S = 3600
MIN_INITIAL_FREQUENCY_HZ = 40.0
MAX_INITIAL_FREQUENCY_HZ = 60.0
MAX_MANUAL_EVENTS = 200  # defensive cap on scripted-event payload size
_VALID_MANUAL_ACTIONS = {"generator_on", "generator_off", "breaker_open", "breaker_close"}


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


def _parse_events(raw: str | None, duration_s: int) -> tuple[ManualEvent, ...]:
    """Parse the "manual" scenario's scripted-event list.

    `raw` is a JSON-encoded array of {"t": int, "action": str} objects, e.g.
    '[{"t":30,"action":"breaker_open"}]'. Empty/missing input means no
    events. Every event is validated: known action, integer t within
    [0, duration_s), and the whole list is capped at MAX_MANUAL_EVENTS to
    bound request size.
    """
    if raw is None or raw == "":
        return ()
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"events must be valid JSON, got {raw!r}") from exc
    if not isinstance(parsed, list):
        raise ValidationError("events must be a JSON array")
    if len(parsed) > MAX_MANUAL_EVENTS:
        raise ValidationError(f"events must contain at most {MAX_MANUAL_EVENTS} entries")

    events: list[ManualEvent] = []
    for i, item in enumerate(parsed):
        if not isinstance(item, dict) or "t" not in item or "action" not in item:
            raise ValidationError(f"events[{i}] must be an object with 't' and 'action'")
        t_raw, action = item["t"], item["action"]
        if not isinstance(t_raw, int) or isinstance(t_raw, bool):
            raise ValidationError(f"events[{i}].t must be an integer, got {t_raw!r}")
        if not (0 <= t_raw < duration_s):
            raise ValidationError(f"events[{i}].t must be within [0, {duration_s})")
        if action not in _VALID_MANUAL_ACTIONS:
            raise ValidationError(
                f"events[{i}].action must be one of {sorted(_VALID_MANUAL_ACTIONS)}, got {action!r}"
            )
        events.append(ManualEvent(t=t_raw, action=action))
    return tuple(events)


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
        events = _parse_events(request.args.get("events"), duration_s)
        # Protection defaults ON for manual runs (mirrors the automatic
        # scenarios' always-on protection); only "manual" ever reads this.
        protection_enabled = (
            _parse_bool(request.args.get("protection_enabled"))
            if request.args.get("protection_enabled") is not None
            else True
        )
    except ValidationError as exc:
        return jsonify({"success": False, "data": None, "error": str(exc)}), 400

    config = SimConfig(
        duration_s=duration_s,
        initial_frequency_hz=initial_frequency_hz,
        scenario=scenario,
        fault_time_s=fault_time_s,
        generator_failure_time_s=generator_failure_time_s,
        disable_sensor_validation=disable_sensor_validation,
        events=events,
        protection_enabled=protection_enabled,
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
            "events": [{"t": e.t, "action": e.action} for e in config.events],
            "protection_enabled": config.protection_enabled,
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
                "protection_overridden": s.protection_overridden,
            }
            for s in result.steps
        ],
    }
    return jsonify({"success": True, "data": payload, "error": None})


def _parse_step_frequency(raw: object) -> float:
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        raise ValidationError(f"current_frequency_hz must be a number, got {raw!r}")
    value = float(raw)
    if not (MIN_INITIAL_FREQUENCY_HZ <= value <= MAX_INITIAL_FREQUENCY_HZ):
        raise ValidationError(
            f"current_frequency_hz must be between {MIN_INITIAL_FREQUENCY_HZ} "
            f"and {MAX_INITIAL_FREQUENCY_HZ} Hz"
        )
    return value


def _parse_step_flag(raw: object, field_name: str) -> bool:
    if not isinstance(raw, bool):
        raise ValidationError(f"{field_name} must be a boolean, got {raw!r}")
    return raw


@app.post("/api/step")
def step() -> tuple[Response, int] | Response:
    """Advance one manual/live step from the operator's current snapshot.

    Stateless by design (matches the project's existing recompute-from-
    scratch model, see README "Known limitations"): the frontend owns the
    running state across steps and resends it each call; this endpoint has
    no memory of previous steps. Body (JSON):
      current_frequency_hz: float  - true frequency at the start of this step
      generator_on: bool           - operator's current G101 state
      breaker_closed: bool         - operator's INTENDED CB101 state for
                                      this step (what they want it set to)
      protection_enabled: bool     - whether predictive protection may
                                      override an unsafe intended CLOSE

    Reuses the same pure functions as the automatic scenarios
    (control.protection_would_open, physics.next_frequency) so live manual
    mode can never disagree with the engine about what is physically or
    protectively valid.
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"success": False, "data": None, "error": "request body must be a JSON object"}), 400

    try:
        current_frequency_hz = _parse_step_frequency(body.get("current_frequency_hz"))
        generator_on = _parse_step_flag(body.get("generator_on"), "generator_on")
        intended_breaker_closed = _parse_step_flag(body.get("breaker_closed"), "breaker_closed")
        protection_enabled = _parse_step_flag(body.get("protection_enabled"), "protection_enabled")
    except ValidationError as exc:
        return jsonify({"success": False, "data": None, "error": str(exc)}), 400

    breaker_closed = intended_breaker_closed
    protection_overridden = False
    reason = "manual: operator-selected breaker state"
    if protection_enabled and breaker_closed:
        must_open, protection_reason = protection_would_open(current_frequency_hz, generator_on)
        if must_open:
            breaker_closed = False
            reason = protection_reason
            protection_overridden = True

    next_frequency_hz = next_frequency(current_frequency_hz, generator_on, breaker_closed)

    return jsonify(
        {
            "success": True,
            "data": {
                "true_frequency_hz": current_frequency_hz,
                "next_frequency_hz": next_frequency_hz,
                "generator_on": generator_on,
                "breaker_closed": breaker_closed,
                "protection_overridden": protection_overridden,
                "tripped": protection_overridden,
                "note": reason,
            },
            "error": None,
        }
    )


@app.get("/")
def index() -> Response:
    return send_from_directory(_FRONTEND_DIR, "index.html")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
