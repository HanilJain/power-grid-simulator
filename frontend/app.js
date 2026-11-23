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
};

const state = {
  steps: [],
  limits: null,
  cursor: 0,
  playing: false,
  playTimer: null,
};

const el = (id) => document.getElementById(id);

function scenarioNeedsField(scenario) {
  return {
    showFault: scenario === "faulty_sensor",
    showFailure: scenario === "generator_failure",
    showValidationToggle: scenario === "faulty_sensor",
  };
}

function updateControlVisibility() {
  const scenario = el("scenario").value;
  const flags = scenarioNeedsField(scenario);
  el("fault-time-group").hidden = !flags.showFault;
  el("failure-time-group").hidden = !flags.showFailure;
  el("validation-toggle-group").hidden = !flags.showValidationToggle;
  el("scenario-description").textContent = SCENARIO_DESCRIPTIONS[scenario] || "";
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

// --- Wiring ---------------------------------------------------------------

function init() {
  updateControlVisibility();
  el("scenario").addEventListener("change", updateControlVisibility);
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

  runSimulation();
}

document.addEventListener("DOMContentLoaded", init);
