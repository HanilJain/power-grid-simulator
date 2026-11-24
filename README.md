# Power Grid Simulator

A simulator for an Arctic research station's single-bus power distribution
system: generator **G101**, circuit breaker **CB101**, station load **L101**,
and frequency sensor **FS101**. It models the physical process, implements
the protective/control logic, and displays the system state graphically.

Backend: **Python** (pure standard library simulation core + **Flask** for
the HTTP API — a framework was used only where one was actually needed, to
serve the frontend and expose `/api/simulate`, `/api/step`, `/api/scenarios`).
Frontend: plain **HTML / CSS / JavaScript** with **Chart.js** (vendored
locally in `frontend/vendor/`, no build step, no framework) plus an inline
SVG circuit diagram.

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

## Scenarios (presets)

The **Scenario** dropdown is the main thing you choose before clicking **Run
simulation**. Each preset answers a different question about the station;
pick the one that matches what you want to see, set its extra fields (each
scenario reveals only the fields relevant to it), then run it.

| Scenario | Question it answers | What to watch for | Extra controls it reveals |
|---|---|---|---|
| **Normal operation** | What does healthy, continuous operation look like? | CB101 duty-cycles open/closed the whole run (this is *expected*, not a bug — see "Why this isn't a toy..." above); frequency never leaves the 49.50–50.50 Hz band; **Powered time** settles near ~71%; **Protective trips** stays at 0. | None — just Duration and Initial frequency. |
| **Generator failure** | Does protection catch a real loss of generation before the limit is crossed? | At **Failure time**, G101 flips OFF (watch the G101 chart/diagram go grey). CB101 must open at or before the 49.50 Hz line and then **stay open** for the rest of the run (there's no source left to restore power). **Limit violations** must stay at 0. | **Failure time (s)** — when G101 trips. |
| **Faulty sensor (2am incident)** | Can a single bad sensor reading alone cause an unnecessary trip? | This reproduces the task's background incident. At **Fault time**, FS101's *measured* line jumps to an impossible low value while the *true* line is unaffected. With validation **enabled** (default), the reading is rejected and nothing trips. Check **Disable sensor validation** to reproduce the original incident: the controller is fooled and CB101 trips even though true frequency was fine — compare the two runs' **Protective trips** KPI. | **Fault time (s)**, **Disable sensor validation** checkbox. |
| **Manual control** | What if *I* want to drive the station instead of watching a preset? | See [Manual mode](#manual-mode) below — this is the only scenario with no fixed script; you provide it live or write your own. | **Manual mode** (Live stepping / Scripted events), **Protection safety net** checkbox. |

## Using the dashboard

A walkthrough of every control, panel, and what "good" looks like.

### The controls panel

| Control | Effect |
|---|---|
| **Scenario** | Selects one of the four presets above. Changing it reveals/hides the fields that only apply to that scenario. |
| **Duration (seconds)** | How many 1-second steps to simulate (10–3600). Longer runs take the same wall-clock time to compute (the whole run happens server-side in one pass) but produce more data to scrub through. |
| **Initial frequency (Hz)** | The bus frequency at t = 0 (40–60 Hz). Start it near a limit (e.g. 49.6) to see protection react almost immediately. |
| **Fault time (s)** *(faulty_sensor only)* | The second at which FS101 reports its single bad reading. |
| **Failure time (s)** *(generator_failure only)* | The second at which G101 trips to OFF. |
| **Disable sensor validation** *(faulty_sensor only)* | Unchecked (default) = protected path, the bad reading is rejected. Checked = naive path, reproducing the original 2am incident. |
| **Manual mode** *(manual only)* | **Live stepping** — you drive the system one second at a time. **Scripted events** — you write a list of timed actions and run them all at once like a preset. |
| **Protection safety net** *(manual only)* | When checked (default), a manual CLOSE that would breach the 49.50 Hz limit is automatically forced OPEN instead (same rule the automatic scenarios always enforce). Uncheck it to see what happens with no guard at all. |
| **Run simulation** | Executes the configured run server-side and loads the result into the charts/diagram/playback controls below. (Hidden while in Manual → Live stepping, where you advance the run yourself instead — see [Manual mode](#manual-mode).) |

### Playback controls

Once a run has loaded, use these to move through it; all three charts and
the circuit diagram stay in sync with whichever second you're viewing:

| Control | Effect |
|---|---|
| **▶ Play / ⏸ Pause** | Auto-advances the cursor through the run (one step every 80ms) until it reaches the end or you pause it. |
| **⏭ Step** | Advances the cursor by exactly one second, then stops. |
| **↺ Reset** | Returns the cursor to t = 0 without re-running the simulation. |
| **Timeline scrubber** | Drag to jump directly to any second in the run. |

### Reading the KPI cards

| KPI | What "good" looks like |
|---|---|
| **Current frequency** | Tracks the cursor position. Red if outside 49.50–50.50 Hz (should never happen except when you've deliberately disabled manual protection). |
| **G101** | ON/OFF at the cursor position. |
| **CB101** | CLOSED/OPEN at the cursor position. |
| **Powered time** | Fraction of the whole run spent CLOSED. ~71% in **Normal operation** — see "Why this isn't a toy..." above for why that's the physical ceiling, not a tuning defect. Lower in **Generator failure** (no power once tripped) or in a **Manual** run where you leave CB101 open. |
| **Protective trips** | Count of *protection-driven* breaker openings (as opposed to routine hysteresis reopening). Should be 0 in **Normal operation**; expected to be exactly 1 in **Generator failure**; appears in the naive **Faulty sensor** run; also counts manual protection overrides. |
| **Limit violations** | Count of steps where true frequency actually left the safe band. Should always be **0** — this is the hard invariant the whole control design exists to guarantee. The only way to see a nonzero value is to run **Manual control** with the protection safety net turned off. |

### Reading the charts & circuit diagram

- **Bus frequency (FS101) chart** — true frequency (solid) vs. the sensor's
  measured value (dashed); the 49.50/50.50 Hz hard limits and the 50.00 Hz
  nominal line are drawn for reference. The two lines diverge exactly when a
  sensor fault is injected or a reading is rejected.
- **Generator G101 / Circuit breaker CB101 charts** — simple ON/OFF and
  OPEN/CLOSED step charts over the whole run, useful for seeing the duty
  cycle (or failure point) at a glance.
- **Circuit diagram** — an animated single-line schematic (G101 → CB101 →
  bus → L101, with FS101 tapped off the bus) that mirrors whatever second
  the cursor/scrubber is on:
  - **G101** fills green when ON, grey when OFF.
  - **CB101**'s contact arm visibly swings open or closed; it flashes red on
    a protective trip.
  - **Wires and the bus node** glow green only when the source-to-load path
    is actually live (G101 ON *and* CB101 CLOSED); otherwise grey. They tint
    red if true frequency has actually left the safe band.
  - **L101** shows POWERED/UNPOWERED matching the wire state.
  - **FS101** rings amber when its latest reading was rejected by validation,
    and red once rejections have persisted long enough to be flagged as a
    degraded sensor.

### Manual mode

Manual control is the one scenario with no fixed script — you decide what
happens. It has two sub-modes:

**Live stepping.** You are the operator. Toggle the **G101** and **CB101**
buttons to set your intended state, then click **Step +1s** to advance the
simulation by exactly one second and see the true frequency respond. Each
step is computed by the same physics/protection functions the automatic
scenarios use (`backend/simulator/physics.py`, `backend/simulator/control.py`
via the `/api/step` endpoint), so it can never disagree with how a preset run
would behave in the same state. With the **Protection safety net** checked,
attempting to CLOSE CB101 when doing so would breach 49.50 Hz is
automatically overridden back to OPEN — a badge appears when this happens.
Uncheck the safety net to drive the system however you like, including
straight through the limit, to see what the protection logic normally
prevents. **Reset live run** starts over from Initial frequency with G101 ON
and CB101 CLOSED.

**Scripted events.** Instead of stepping live, write out a small script:
pick a second and an action (G101 ON/OFF, CB101 OPEN/CLOSE), click **Add
event**, and repeat for as many events as you want. Click **Run simulation**
to execute the whole sequence at once — it behaves exactly like any preset
run afterward, with full chart/diagram/timeline playback. This is useful for
planning out a specific sequence (e.g. "fail the generator at t=30, then
manually reclose at t=90") without stepping through it by hand.

## Project structure

```
power-grid-simulator/
├── backend/
│   ├── app.py                  # Flask app: serves frontend + /api/simulate, /api/step, /api/scenarios
│   └── simulator/
│       ├── constants.py        # nominal freq, limits, rate table, control thresholds
│       ├── models.py           # frozen dataclasses: SimConfig, ManualEvent, StepResult, SimulationResult
│       ├── physics.py          # rate_for() / next_frequency() -- the rate table, as code
│       ├── control.py          # decide_breaker() + protection_would_open(): protection + hysteresis policy
│       ├── sensor.py           # FS101 measurement, fault injection, plausibility validator
│       ├── engine.py           # discrete 1-second simulation loop (incl. scripted "manual" events)
│       └── scenarios.py        # scenario registry + descriptions
├── frontend/
│   ├── index.html              # controls, manual panels, circuit diagram, KPIs, playback, charts
│   ├── style.css                # dashboard + circuit diagram + manual-mode styling
│   ├── app.js                   # fetches /api/simulate & /api/step, renders charts + circuit diagram, drives playback and manual mode
│   └── vendor/chart.min.js       # Chart.js, vendored locally (no CDN dependency)
├── tests/                       # pytest suite (95 tests, 98%+ coverage on backend/)
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
second by second while watching the three synchronized charts and the
animated circuit diagram. See [Using the dashboard](#using-the-dashboard)
above for a full walkthrough of every control, KPI, and the manual-control
mode.

## Running the tests

```powershell
pip install -r requirements.txt   # includes pytest, pytest-cov
pytest --cov=backend --cov-report=term-missing
```

95 tests (98% coverage on `backend/`) cover the physics rate table, the
control invariants (frequency never leaves the safe band; CB101 is never
closed below 49.50 Hz; CB101 prefers closed), the sensor fault/validation/
debounce model, full-run engine invariants for every scenario (including
`manual`'s scripted events and protection-override toggle), and the Flask
API's input validation, error handling, and response contracts for
`/api/simulate`, `/api/step`, and `/api/scenarios`.

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
- There's no persistence — every run (including each manual live-mode step,
  via the stateless `/api/step` endpoint) is recomputed from scratch with no
  server-side memory between calls; acceptable for a training/rehearsal
  tool, but a real deployment might want to log historical runs.
- The frontend re-renders full Chart.js instances on every "Run simulation"
  click (and on every manual live-mode step) rather than incrementally
  updating datasets; fine at these data volumes (≤3600 points) but not
  optimized for much longer runs.
- **Manual mode's live stepping** holds its running history only in the
  browser tab's memory (`state.steps`, mirroring the same
  recompute-from-scratch philosophy as the rest of the app) — refreshing the
  page discards an in-progress live run. Use **Scripted events** instead if
  you want a manual run you can replay or revisit via the timeline after a
  reload.
- The circuit diagram's layout is fixed (five components in a single line,
  matching this task's single-bus topology); it isn't a general-purpose
  schematic editor and wouldn't generalize to a multi-bus or multi-generator
  model without rework.
