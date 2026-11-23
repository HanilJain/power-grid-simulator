# Power Grid Simulator

A simulator for an Arctic research station's single-bus power distribution
system: generator **G101**, circuit breaker **CB101**, station load **L101**,
and frequency sensor **FS101**. It models the physical process, implements
the protective/control logic, and displays the system state graphically.

Backend: **Python** (pure standard library simulation core + **Flask** for
the HTTP API — a framework was used only where one was actually needed, to
serve the frontend and expose `/api/simulate`).
Frontend: plain **HTML / CSS / JavaScript** with **Chart.js** (via CDN, no
build step, no framework).

## Why this isn't a toy "keep the number between two bounds" script

The frequency-rate table given in the task has an important consequence:

| G101 | CB101 | Rate |
|------|-------|------|
| ON | CLOSED | **−0.05 Hz/s** |
| ON | OPEN | **+0.12 Hz/s** |
| OFF | CLOSED | **−0.15 Hz/s** |
| OFF | OPEN | **0.00 Hz/s** |

With the generator running (the only state where the station is actually
powered), **CLOSED always loses frequency and OPEN always gains it** — there
is no steady state where the breaker can simply stay closed forever. A
correct controller must duty-cycle CB101 between a reopen and a reclose
threshold. In steady state the fraction of time the station can be powered
is fixed by the physics itself:

```
powered_fraction = 0.12 / (0.05 + 0.12) ≈ 0.706  (~71%)
```

The simulator's "Normal" scenario reproduces this ~70% figure, which is the
best any controller can do under these exact rates — it is not a tuning
defect.

## Control design

Two layers, modeled on real protective-relay practice (see
[`backend/simulator/control.py`](backend/simulator/control.py)):

1. **Protection (hard, predictive).** Before committing to a candidate
   breaker state, the controller asks the physics model what frequency
   *would* result one second from now in that state. If staying/going
   CLOSED would cross the 49.50 Hz lower limit, protection forces **OPEN**
   regardless of policy. This directly satisfies Design Assumption 3 ("CB101
   should never be CLOSED when frequency is below 49.50 Hz") for *any*
   generator state, including failures.
2. **Policy (soft, hysteresis, prefers CLOSED).** Reopens at **49.70 Hz**,
   recloses at **50.30 Hz** — a 0.20 Hz margin on each side of the hard
   limits, safely above the steepest possible single-step change
   (0.15 Hz/s × 1 s). This prevents the breaker from chattering open/closed
   every second right at the hard limits, while still honoring Design
   Assumption 4 ("CB101 should be CLOSED whenever feasible").

Protection always overrides policy.

## Scenarios

| Scenario | What it does |
|---|---|
| **normal** | G101 stays ON for the whole run; the controller duty-cycles CB101 to keep frequency in the safe band. |
| **generator_failure** | G101 trips to OFF partway through the run. Protection must open CB101 before the limit is crossed, then hold it open (no source left). |
| **faulty_sensor** | FS101 reports a single physically-impossible low reading at a configured instant — reproducing the task's background incident ("a faulty frequency reading caused an unnecessary breaker trip at 2am"). Toggle *Disable sensor validation* to compare the naive (unprotected) response against the physics-validated one. |

## Project structure

```
power-grid-simulator/
├── backend/
│   ├── app.py                  # Flask app: serves frontend + /api/simulate, /api/scenarios
│   └── simulator/
│       ├── constants.py        # nominal freq, limits, rate table, control thresholds
│       ├── models.py           # frozen dataclasses: SimConfig, StepResult, SimulationResult
│       ├── physics.py          # rate_for() / next_frequency() -- the rate table, as code
│       ├── control.py          # decide_breaker(): protection + hysteresis policy
│       ├── sensor.py           # FS101 measurement, fault injection, plausibility validator
│       ├── engine.py           # discrete 1-second simulation loop
│       └── scenarios.py        # scenario registry + descriptions
├── frontend/
│   ├── index.html              # controls, KPIs, playback, chart containers
│   ├── style.css               # dashboard styling
│   └── app.js                  # fetches /api/simulate, renders Chart.js graphs, drives playback
├── tests/                      # pytest suite (58 tests, 94%+ coverage on backend/)
├── requirements.txt
└── AI_USAGE.md
```

## Installation

Requires Python 3.10+.

```powershell
# from the project root
python -m venv venv
venv\Scripts\Activate.ps1          # PowerShell
# or: venv\Scripts\activate.bat    # cmd.exe
pip install -r requirements.txt
```

## Running the simulator

```powershell
python -m backend.app
```

Then open **http://127.0.0.1:5000** in a browser. Pick a scenario, set the
duration/initial frequency, and click **Run simulation**. Use the playback
controls (Play / Step / Reset / timeline scrubber) to walk through the run
second by second while watching the three synchronized charts:

1. **Bus frequency (FS101)** — true vs. measured frequency, with the 49.50 Hz
   / 50.50 Hz hard limits and the 50.00 Hz nominal line drawn for reference.
2. **Generator G101** — ON/OFF over time.
3. **Circuit breaker CB101** — OPEN/CLOSED over time.

## Running the tests

```powershell
pip install -r requirements.txt   # includes pytest, pytest-cov
pytest --cov=backend --cov-report=term-missing
```

67 tests (99% coverage on `backend/`) cover the physics rate table, the
control invariants (frequency never leaves the safe band; CB101 is never
closed below 49.50 Hz; CB101 prefers closed), the sensor fault/validation/
debounce model, full-run engine invariants for every scenario, and the
Flask API's input validation, error handling, and response contracts.

## Assumptions made beyond the task spec

These are engineering choices, documented here per the task's request to
"document any assumptions made during development":

- **Integration method:** forward Euler at Δt = 1 s using the active state's
  rate (`frequency[t+1] = frequency[t] + rate · dt`), per Functional
  Requirement 1's "discrete time steps (per second)".
- **Decision ordering per step:** the controller decides CB101's next state
  from the *current* (validated) reading, then frequency is advanced under
  the *actual* resulting generator/breaker state — not the possibly-wrong
  measured state, since the real bus doesn't react to what the sensor
  reported.
- **Hysteresis thresholds** (49.70 Hz reopen / 50.30 Hz reclose) and the
  **sensor plausibility tolerance** (max physical rate + a small slack, see
  `constants.py`) are tunable constants, not given by the task; their
  derivation and margin-of-safety reasoning are documented inline in
  `constants.py` and above.
- **Sensor fault model:** the faulty reading is injected as a single fixed
  low value at a configured instant, independent of true frequency — this
  matches the task's description of a bad *reading*, not a real
  under-frequency event.
- **Debounce (`DEBOUNCE_SAMPLES`):** a single rejected reading is absorbed
  transparently (the validator already falls back to the last trusted
  value, which is what defeats the single-sample fault scenario). A
  separate `sensor_degraded` flag only turns on once rejections persist for
  `DEBOUNCE_SAMPLES` readings in a row — i.e. FS101 itself is failing
  continuously, not just producing one bad sample — exposed in the API/UI
  as a distinct condition from an ordinary single rejection.
- Input bounds on the API (e.g. duration 10–3600 s, initial frequency
  40–60 Hz) are defensive limits, not part of the task spec, added per the
  project's "validate all input at system boundaries" convention.

## Known limitations / things that could be improved further

- The sensor model injects exactly one fault per run; a more complete model
  would support configurable noise, drift, or multiple fault injections.
- There's no persistence — every run is stateless and recomputed from
  scratch; acceptable for a training/rehearsal tool, but a real deployment
  might want to log historical runs.
- The frontend re-renders full Chart.js instances on every "Run simulation"
  click rather than incrementally updating datasets; fine at these data
  volumes (≤3600 points) but not optimized for much longer runs.
