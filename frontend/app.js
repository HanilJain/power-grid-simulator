"use strict";

/**
 * Power Grid Simulator frontend.
 *
 * Fetches a full simulation run from /api/simulate, renders three Chart.js
 * graphs (frequency with safe-band limits, G101 state, CB101 state), and
 * drives a play/step/reset timeline cursor across all three in sync.
 */

const SCENARIO_DESCRIPTIONS = {
  normal:
    "Generator G101 runs continuously. The controller duty-cycles CB101 to keep bus frequency inside the safe band.",
  generator_failure:
    "G101 fails partway through the run. Protection must open CB101 before the lower limit is crossed, then hold it open.",
  faulty_sensor:
    "FS101 reports one physically-impossible low reading, reproducing the 2am incident. Toggle validation to compare the naive vs. protected response.",
  manual:
    "You drive G101/CB101 yourself — either live, one second at a time, or by scripting a list of actions to run all at once.",
};

const state = {
  steps: [],
  limits: null,
  cursor: 0,
  playing: false,
  playTimer: null,
  manual: {
    events: [], // scripted sub-mode: [{t, action}]
    live: {
      frequencyHz: 50.0,
      generatorOn: true,
      breakerClosed: true,
    },
  },
};

const el = (id) => document.getElementById(id);

function scenarioNeedsField(scenario) {
  return {
    showFault: scenario === "faulty_sensor",
    showFailure: scenario === "generator_failure",
    showValidationToggle: scenario === "faulty_sensor",
    showManualMode: scenario === "manual",
  };
}

function currentManualMode() {
  return el("manual-mode").value; // "live" | "scripted"
}

function updateControlVisibility() {
  const scenario = el("scenario").value;
  const flags = scenarioNeedsField(scenario);
  el("fault-time-group").hidden = !flags.showFault;
  el("failure-time-group").hidden = !flags.showFailure;
  el("validation-toggle-group").hidden = !flags.showValidationToggle;
  el("manual-mode-group").hidden = !flags.showManualMode;
  el("manual-protection-group").hidden = !flags.showManualMode;
  el("scenario-description").textContent = SCENARIO_DESCRIPTIONS[scenario] || "";
  updateManualModeVisibility();
}

function updateManualModeVisibility() {
  const scenario = el("scenario").value;
  const isManual = scenario === "manual";
  const mode = currentManualMode();
  const isLive = isManual && mode === "live";
  const isScripted = isManual && mode === "scripted";

  el("manual-live-panel").hidden = !isLive;
  el("manual-scripted-panel").hidden = !isScripted;
  // In live mode, playback/charts are driven by stepping, not a single
  // upfront /api/simulate call, so the normal Run button is redundant.
  el("run-btn").closest(".control-group").hidden = isLive;

  if (isLive) {
    resetManualLiveRun();
  }
}

function buildQueryParams() {
  const scenario = el("scenario").value;
  const params = new URLSearchParams({
    scenario,
    duration: el("duration").value,
    initial_frequency: el("initial-frequency").value,
  });
  if (scenario === "faulty_sensor") {
    params.set("fault_time", el("fault-time").value);
    params.set("disable_sensor_validation", el("disable-validation").checked ? "true" : "false");
  }
  if (scenario === "generator_failure") {
    params.set("generator_failure_time", el("generator-failure-time").value);
  }
  if (scenario === "manual") {
    params.set("protection_enabled", el("manual-protection").checked ? "true" : "false");
    params.set("events", JSON.stringify(state.manual.events));
  }
  return params;
}

async function runSimulation() {
  setStatus("Running simulation…", null);
  stopPlayback();
  el("run-btn").disabled = true;

  try {
    const params = buildQueryParams();
    const response = await fetch(`/api/simulate?${params.toString()}`);
    const body = await response.json();

    if (!response.ok || !body.success) {
      setStatus(`Error: ${body.error || "unknown error"}`, "danger");
      return;
    }

    state.steps = body.data.steps;
    state.limits = body.data.limits;
    state.cursor = 0;

    renderCharts();
    updateSummary(body.data.summary);
    enablePlayback();
    updateCursorDisplay();
    setStatus(
      `Simulation complete: ${state.steps.length} steps, ` +
        `${body.data.summary.trip_count} protective trip(s), ` +
        `${body.data.summary.limit_violations} limit violation(s).`,
      body.data.summary.limit_violations > 0 ? "danger" : "ok"
    );
  } catch (err) {
    setStatus(`Request failed: ${err.message}`, "danger");
  } finally {
    el("run-btn").disabled = false;
  }
}

function setStatus(message, kind) {
  const target = el("status-message");
  target.textContent = message;
  target.classList.remove("danger", "ok");
  if (kind) target.classList.add(kind);
}

function updateSummary(summary) {
  el("kpi-powered").textContent = `${(summary.powered_fraction * 100).toFixed(1)}%`;
  el("kpi-trips").textContent = String(summary.trip_count);
  el("kpi-trips").className = "kpi-value " + (summary.trip_count > 0 ? "state-warning" : "state-ok");
  el("kpi-violations").textContent = String(summary.limit_violations);
  el("kpi-violations").className =
    "kpi-value " + (summary.limit_violations > 0 ? "state-danger" : "state-ok");
}

// --- Chart.js setup ---------------------------------------------------

let frequencyChart = null;
let generatorChart = null;
let breakerChart = null;

function renderCharts() {
  const labels = state.steps.map((s) => s.t);
  const trueFreq = state.steps.map((s) => s.true_frequency_hz);
  const measuredFreq = state.steps.map((s) => s.measured_frequency_hz);
  const generatorY = state.steps.map((s) => (s.generator_on ? 1 : 0));
  const breakerY = state.steps.map((s) => (s.breaker_closed ? 1 : 0));

  const { lower_hz: lower, upper_hz: upper, nominal_hz: nominal } = state.limits;

  if (frequencyChart) frequencyChart.destroy();
  if (generatorChart) generatorChart.destroy();
  if (breakerChart) breakerChart.destroy();

  frequencyChart = new Chart(el("frequency-chart"), {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "True frequency (Hz)",
          data: trueFreq,
          borderColor: "#4fc3f7",
          backgroundColor: "transparent",
          pointRadius: 0,
          borderWidth: 2,
          tension: 0,
        },
        {
          label: "Measured (FS101)",
          data: measuredFreq,
          borderColor: "#ffcc66",
          backgroundColor: "transparent",
          pointRadius: 0,
          borderWidth: 1,
          borderDash: [4, 3],
        },
        {
          label: "Upper limit",
          data: labels.map(() => upper),
          borderColor: "#ff6b6b",
          borderWidth: 1,
          pointRadius: 0,
          borderDash: [6, 4],
        },
        {
          label: "Lower limit",
          data: labels.map(() => lower),
          borderColor: "#ff6b6b",
          borderWidth: 1,
          pointRadius: 0,
          borderDash: [6, 4],
        },
        {
          label: "Nominal",
          data: labels.map(() => nominal),
          borderColor: "#6ee7a0",
          borderWidth: 1,
          pointRadius: 0,
          borderDash: [2, 2],
        },
      ],
    },
    options: chartOptions("Hz", lower - 0.3, upper + 0.3),
    plugins: [CURSOR_LINE_PLUGIN],
  });

  generatorChart = new Chart(el("generator-chart"), {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "G101 (1 = ON, 0 = OFF)",
          data: generatorY,
          borderColor: "#4fc3f7",
          backgroundColor: "rgba(79,195,247,0.15)",
          stepped: true,
          pointRadius: 0,
          borderWidth: 2,
          fill: true,
        },
      ],
    },
    options: chartOptions("", -0.2, 1.2, [0, 1]),
    plugins: [CURSOR_LINE_PLUGIN],
  });

  breakerChart = new Chart(el("breaker-chart"), {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "CB101 (1 = CLOSED, 0 = OPEN)",
          data: breakerY,
          borderColor: "#6ee7a0",
          backgroundColor: "rgba(110,231,160,0.15)",
          stepped: true,
          pointRadius: 0,
          borderWidth: 2,
          fill: true,
        },
      ],
    },
    options: chartOptions("", -0.2, 1.2, [0, 1]),
    plugins: [CURSOR_LINE_PLUGIN],
  });
}

function chartOptions(yLabel, yMin, yMax, ticks) {
  return {
    responsive: true,
    animation: false,
    interaction: { mode: "index", intersect: false },
    scales: {
      x: { title: { display: true, text: "Time (s)" }, ticks: { color: "#9fb0c7" } },
      y: {
        min: yMin,
        max: yMax,
        title: { display: Boolean(yLabel), text: yLabel, color: "#9fb0c7" },
        ticks: ticks ? { callback: (v) => (ticks.includes(v) ? v : ""), color: "#9fb0c7" } : { color: "#9fb0c7" },
        grid: { color: "#22304a" },
      },
    },
    plugins: {
      legend: { labels: { color: "#e7edf5", boxWidth: 12 } },
    },
  };
}

// Vertical cursor line showing current playback position, drawn via a
// small Chart.js plugin so all three charts share the same approach.
//
// Defined once at module scope (not re-created/re-registered on every run):
// it's attached per-chart through each chart's own `plugins: [...]` config
// array at construction time, rather than via the global `Chart.register`,
// so repeated "Run simulation" clicks don't keep touching Chart.js's
// process-wide plugin registry.
const CURSOR_LINE_PLUGIN = {
  id: "cursorLine",
  afterDraw(chart) {
    const index = state.cursor;
    if (index == null || index < 0) return;
    const meta = chart.getDatasetMeta(0);
    if (!meta || !meta.data || !meta.data[index]) return;
    const x = meta.data[index].x;
    const { top, bottom } = chart.chartArea;
    const ctx = chart.ctx;
    ctx.save();
    ctx.strokeStyle = "#ffffff80";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, top);
    ctx.lineTo(x, bottom);
    ctx.stroke();
    ctx.restore();
  },
};

// --- Playback controls --------------------------------------------------

function enablePlayback() {
  el("play-btn").disabled = false;
  el("step-btn").disabled = false;
  el("reset-btn").disabled = false;
  const timeline = el("timeline");
  timeline.disabled = false;
  timeline.min = 0;
  timeline.max = state.steps.length - 1;
  timeline.value = 0;
}

function updateCursorDisplay() {
  const step = state.steps[state.cursor];
  if (!step) return;

  el("timeline").value = state.cursor;
  el("timeline-label").textContent = `t = ${step.t} s`;

  el("kpi-frequency").textContent = `${step.true_frequency_hz.toFixed(3)} Hz`;
  const freqClass =
    step.true_frequency_hz < state.limits.lower_hz || step.true_frequency_hz > state.limits.upper_hz
      ? "state-danger"
      : "state-ok";
  el("kpi-frequency").className = "kpi-value " + freqClass;

  el("kpi-generator").textContent = step.generator_on ? "ON" : "OFF";
  el("kpi-generator").className = "kpi-value " + (step.generator_on ? "state-ok" : "state-warning");

  el("kpi-breaker").textContent = step.breaker_closed ? "CLOSED" : "OPEN";
  el("kpi-breaker").className = "kpi-value " + (step.breaker_closed ? "state-ok" : "state-warning");

  [frequencyChart, generatorChart, breakerChart].forEach((chart) => chart && chart.update("none"));
  renderCircuit(step);
}

// --- Circuit diagram ----------------------------------------------------
//
// Pure render: takes one step snapshot and sets classes/text on the
// pre-built SVG skeleton in index.html. Called from updateCursorDisplay(),
// which already runs on every cursor move/step/play-tick/scrub and every
// live-stepping tick, so the diagram stays in sync "for free".

function renderCircuit(step) {
  const svg = el("circuit-diagram");
  if (!svg || !step) return;

  const sourceLive = step.generator_on && step.breaker_closed;
  const outOfBand =
    state.limits &&
    (step.true_frequency_hz < state.limits.lower_hz || step.true_frequency_hz > state.limits.upper_hz);
  const tripped = Boolean(step.tripped);

  // Generator
  const genBody = svg.querySelector(".circuit-generator-body");
  genBody.classList.toggle("gen-on", step.generator_on);
  el("circuit-generator-state").textContent = step.generator_on ? "ON" : "OFF";

  // Breaker arm: rotates open/closed; flashes red on a protective trip.
  const arm = el("breaker-arm");
  arm.classList.toggle("breaker-open", !step.breaker_closed);
  arm.classList.toggle("breaker-trip", tripped);
  el("circuit-breaker-state").textContent = step.breaker_closed ? "CLOSED" : "OPEN";

  // Wires + bus node: live (green) only when source->load path is closed;
  // red tint takes priority if the true frequency has actually left the
  // safe band (should only ever happen with protection intentionally off).
  const wireIds = ["wire-gen-cb", "wire-cb-bus", "wire-bus-load", "wire-bus-sensor"];
  wireIds.forEach((id) => {
    const wire = el(id);
    wire.classList.toggle("wire-live", sourceLive && !outOfBand);
    wire.classList.toggle("wire-trip", outOfBand);
  });
  el("bus-node").classList.toggle("bus-live", sourceLive && !outOfBand);
  el("bus-node").classList.toggle("bus-trip", outOfBand);

  // Load: powered exactly when the source->load path is live.
  const loadBody = svg.querySelector(".circuit-load-body");
  loadBody.classList.toggle("load-powered", sourceLive && !outOfBand);
  el("circuit-load-state").textContent = sourceLive ? "POWERED" : "UNPOWERED";

  // Sensor: amber ring on a rejected reading, red ring once persistently degraded.
  const sensorRing = el("sensor-ring");
  sensorRing.classList.toggle("sensor-rejected", Boolean(step.reading_rejected));
  sensorRing.classList.toggle("sensor-degraded", Boolean(step.sensor_degraded));
}

function stepForward() {
  if (state.cursor >= state.steps.length - 1) {
    stopPlayback();
    return;
  }
  state.cursor += 1;
  updateCursorDisplay();
}

function startPlayback() {
  if (state.playing || state.steps.length === 0) return;
  state.playing = true;
  el("play-btn").textContent = "⏸ Pause";
  state.playTimer = setInterval(stepForward, 80);
}

function stopPlayback() {
  state.playing = false;
  el("play-btn").textContent = "▶ Play";
  if (state.playTimer) {
    clearInterval(state.playTimer);
    state.playTimer = null;
  }
}

function togglePlayback() {
  if (state.playing) {
    stopPlayback();
  } else {
    startPlayback();
  }
}

function resetPlayback() {
  stopPlayback();
  state.cursor = 0;
  updateCursorDisplay();
}

// --- Manual mode: live stepping ------------------------------------------
//
// Stateless backend design (mirrors /api/simulate): the frontend owns the
// running history in state.steps/state.manual.live and resends the current
// snapshot to POST /api/step each tick; the server has no memory between
// calls. This lets the existing chart/circuit/KPI render path
// (updateCursorDisplay) work unchanged for live mode too.

function resetManualLiveRun() {
  stopPlayback();
  const initialFrequency = Number(el("initial-frequency").value) || 50.0;
  state.manual.live = { frequencyHz: initialFrequency, generatorOn: true, breakerClosed: true };
  state.limits = state.limits || { nominal_hz: 50.0, lower_hz: 49.5, upper_hz: 50.5 };
  state.steps = [
    {
      t: 0,
      true_frequency_hz: initialFrequency,
      measured_frequency_hz: initialFrequency,
      generator_on: true,
      breaker_closed: true,
      sensor_fault_injected: false,
      reading_rejected: false,
      sensor_degraded: false,
      tripped: false,
      protection_overridden: false,
      note: "manual: initial state",
    },
  ];
  state.cursor = 0;
  renderCharts();
  enablePlayback();
  updateCursorDisplay();
  updateManualToggleButtons();
  el("manual-override-badge").hidden = true;
  setStatus("Live manual run reset. Toggle G101/CB101 and click Step.", null);
}

function updateManualToggleButtons() {
  const { generatorOn, breakerClosed } = state.manual.live;
  const genBtn = el("manual-generator-toggle");
  genBtn.textContent = generatorOn ? "ON" : "OFF";
  genBtn.classList.toggle("state-off", !generatorOn);
  const cbBtn = el("manual-breaker-toggle");
  cbBtn.textContent = breakerClosed ? "CLOSED" : "OPEN";
  cbBtn.classList.toggle("state-off", !breakerClosed);
}

async function manualStepForward() {
  const live = state.manual.live;
  el("manual-step-btn").disabled = true;
  try {
    const response = await fetch("/api/step", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        current_frequency_hz: live.frequencyHz,
        generator_on: live.generatorOn,
        breaker_closed: live.breakerClosed,
        protection_enabled: el("manual-protection").checked,
      }),
    });
    const body = await response.json();
    if (!response.ok || !body.success) {
      setStatus(`Step failed: ${body.error || "unknown error"}`, "danger");
      return;
    }

    const data = body.data;
    const nextT = state.steps.length;
    state.steps.push({
      t: nextT,
      true_frequency_hz: data.true_frequency_hz,
      measured_frequency_hz: data.true_frequency_hz,
      generator_on: data.generator_on,
      breaker_closed: data.breaker_closed,
      sensor_fault_injected: false,
      reading_rejected: false,
      sensor_degraded: false,
      tripped: data.tripped,
      protection_overridden: data.protection_overridden,
      note: data.note,
    });

    // Carry the resulting breaker state back into the toggle buttons so
    // a protection override is visibly reflected, not silently reverted
    // on the next step.
    live.frequencyHz = data.next_frequency_hz;
    live.breakerClosed = data.breaker_closed;
    state.cursor = state.steps.length - 1;

    el("timeline").max = state.steps.length - 1;
    renderCharts();
    updateCursorDisplay();
    updateManualToggleButtons();

    el("manual-override-badge").hidden = !data.protection_overridden;
    el("manual-readout").textContent = `t = ${nextT} s · ${data.true_frequency_hz.toFixed(3)} Hz`;
    setStatus(
      data.protection_overridden
        ? "Protection overrode your CLOSE request — CB101 forced OPEN."
        : `Stepped to t = ${nextT} s.`,
      data.protection_overridden ? "danger" : "ok"
    );
  } catch (err) {
    setStatus(`Request failed: ${err.message}`, "danger");
  } finally {
    el("manual-step-btn").disabled = false;
  }
}

// --- Manual mode: scripted events -----------------------------------------

function renderManualEventList() {
  const list = el("manual-event-list");
  list.innerHTML = "";
  if (state.manual.events.length === 0) {
    const empty = document.createElement("li");
    empty.className = "manual-event-list-empty";
    empty.textContent = "No events yet — add one above, then Run simulation.";
    list.appendChild(empty);
    return;
  }

  const sorted = [...state.manual.events].sort((a, b) => a.t - b.t);
  sorted.forEach((event) => {
    const item = document.createElement("li");
    const label = document.createElement("span");
    label.textContent = `t = ${event.t}s → ${ACTION_LABELS[event.action] || event.action}`;
    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "remove-event-btn";
    removeBtn.textContent = "✖";
    removeBtn.setAttribute("aria-label", "Remove event");
    removeBtn.addEventListener("click", () => {
      state.manual.events = state.manual.events.filter((e) => e !== event);
      renderManualEventList();
    });
    item.appendChild(label);
    item.appendChild(removeBtn);
    list.appendChild(item);
  });
}

const ACTION_LABELS = {
  generator_on: "G101 ON",
  generator_off: "G101 OFF",
  breaker_open: "CB101 OPEN",
  breaker_close: "CB101 CLOSE",
};

function addManualEvent() {
  const t = Number(el("manual-event-time").value);
  const action = el("manual-event-action").value;
  if (!Number.isInteger(t) || t < 0) {
    setStatus("Event time must be a non-negative whole number of seconds.", "danger");
    return;
  }
  state.manual.events.push({ t, action });
  renderManualEventList();
}

// --- Wiring ---------------------------------------------------------------

function init() {
  updateControlVisibility();
  el("scenario").addEventListener("change", updateControlVisibility);
  el("manual-mode").addEventListener("change", updateManualModeVisibility);
  el("run-btn").addEventListener("click", runSimulation);
  el("play-btn").addEventListener("click", togglePlayback);
  el("step-btn").addEventListener("click", () => {
    stopPlayback();
    stepForward();
  });
  el("reset-btn").addEventListener("click", resetPlayback);
  el("timeline").addEventListener("input", (event) => {
    stopPlayback();
    state.cursor = Number(event.target.value);
    updateCursorDisplay();
  });

  // Manual mode: live sub-mode
  el("manual-generator-toggle").addEventListener("click", () => {
    state.manual.live.generatorOn = !state.manual.live.generatorOn;
    updateManualToggleButtons();
  });
  el("manual-breaker-toggle").addEventListener("click", () => {
    state.manual.live.breakerClosed = !state.manual.live.breakerClosed;
    updateManualToggleButtons();
  });
  el("manual-step-btn").addEventListener("click", manualStepForward);
  el("manual-live-reset-btn").addEventListener("click", resetManualLiveRun);

  // Manual mode: scripted sub-mode
  el("manual-add-event-btn").addEventListener("click", addManualEvent);
  renderManualEventList();

  runSimulation();
}

document.addEventListener("DOMContentLoaded", init);
