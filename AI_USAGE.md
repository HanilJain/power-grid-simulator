# AI Usage

## 1. Model / tool used and how it was accessed

**Claude Code** (Anthropic), using the **Claude Opus** and **Claude Sonnet**
model families across the session, accessed through the Claude Code CLI /
VS Code extension in agentic mode (the assistant read the task PDF, asked
clarifying questions, wrote a phase-by-phase plan, then implemented and
tested the code directly via file-write and shell-execution tools in this
environment).

No local open-source model (Qwen, GLM, DeepSeek) was used for this
submission — noted honestly per the task's request, even though the task
says local models "would be viewed favorably." The reasoning: this
environment already had Claude Code configured and working end-to-end
(file edits, test execution, live server verification all in one
continuous session), and swapping to a separately-hosted local model mid
-task would have cost significant setup time without changing the design
substance. If this is weighed heavily, the honest answer is simply that the
convenience of an already-integrated tool won out over the "favorably
viewed" option, and that's a real trade-off worth naming rather than
hiding.

## 2–3. Prompts and responses (condensed, in actual session order)

The prompts below are condensed/paraphrased from the real session for
readability; the technical content and sequence are accurate.

### Prompt 1 — initial task handoff
> "build this task in with exact guidance given in this doc and also use
> python for backend and html, css+JS for frontend. Use framework only if
> required" (with the Power Grid Simulator task PDF attached).

**Response:** Claude read the PDF, extracted the system components,
design assumptions, and rate table, and — before writing any code — flagged
a non-obvious consequence of the given rates: with the generator ON, CLOSED
always loses frequency (−0.05 Hz/s) and OPEN always gains it (+0.12 Hz/s),
so there is no steady "just leave it closed" state. It then asked three
clarifying questions via the tool's structured question mechanism:
(a) Flask vs. a zero-dependency stdlib server vs. no live server at all;
(b) scope — required-only vs. required + a generator-failure scenario vs.
full scope including a faulty-sensor scenario; (c) static charts vs.
interactive charts with playback controls.

**My input / modification:** I chose Flask (minimal), full scope (including
the faulty-sensor scenario, since the task background story is literally
about a faulty sensor causing an unnecessary trip — it felt wrong to build
a simulator and leave out the one scenario the prompt is framed around),
and interactive charts with playback.

### Prompt 2 — plan structure
> "make this plan phase wise break up"

**Response:** Claude restructured its single implementation plan into 8
discrete phases (0: scaffolding → 1: physics core → 2: models/control →
3: sensor/validator → 4: engine/scenarios → 5: Flask API → 6: frontend →
7: docs), each with an explicit TDD order (test file before implementation)
and an exit check, instead of one flat plan description.

**My input:** Accepted the phase breakdown as-is; no changes requested
before implementation began.

### Prompt 3 — go
> "start"

**Response:** Claude executed every phase in order: wrote the test file
for a module first, ran it to confirm RED (import error / missing
function), wrote the implementation, reran to confirm GREEN, then moved to
the next phase. It ran the real test suite and a live Flask server (via
HTTP calls against `http://127.0.0.1:5000`) to verify all three scenarios
behaved correctly before writing documentation.

## 4. Modifications made to AI-generated output, and why

This section is the "build it, break it, fix it" part the task specifically
asks for — these are real issues hit during the session, not hypothetical
ones:

### Bug found: floating-point boundary test failure in the sensor validator
The first version of `sensor.validate()` compared the reading's implied
rate against the plausibility threshold with a plain `<=`. A test asserting
the *exact* boundary value is accepted (`measured_hz == last_valid_hz -
MAX_PLAUSIBLE_DELTA_HZ`) failed, because `MAX_PLAUSIBLE_DELTA_HZ` is itself
computed from a multiplication (`(0.15 + 0.02) * 1.0`) while the test
computed the boundary via subtraction — standard binary floating-point
representation meant the two sides of the comparison differed by about
1e-16.

**Fix:** added a small `float_epsilon = 1e-9` tolerance to the comparison
in `sensor.py`. This was a genuine implementation bug (acting on an
untested edge case), not a bad test — the test correctly describes the
intended behavior (accept readings exactly at the boundary), and the fix
makes the code match that intent instead of being at the mercy of binary
float representation.

### Design correction: "trip" semantics in the engine
The first version of `engine.py` marked `StepResult.tripped = True` any
time the breaker transitioned CLOSED → OPEN, including the ordinary
hysteresis reopen that happens continuously in the *normal* scenario (duty
-cycling is expected, routine behavior — not an incident). This caused two
test failures: `test_no_trips_in_normal_scenario` and the validated-sensor
test both expected zero trips, but got double digits, because every
routine hysteresis reopen was being counted as a "trip."

**Fix:** redefined `tripped` to mean specifically a **protection**-layer
intervention (an abnormal, limit-at-risk event — exactly what "trip" means
in the task's own background story: "a faulty frequency reading caused an
unnecessary breaker trip"), distinguished from ordinary **policy** -layer
duty-cycling, by checking whether `decide_breaker`'s returned reason string
starts with `"protection"` vs `"policy"`. This is a meaningful semantic
distinction, not just a test-chasing hack: the whole point of the
faulty-sensor scenario is to show a trip (protection firing on a bad
reading) that the normal scenario should never produce, and the original
code couldn't tell the two apart.

### Judgment call: what "AI gave garbage" looked like here
Neither of the above was really "the AI gave garbage" in the sense of
producing wrong code from a vague prompt — both were self-caught during
the TDD RED→GREEN cycle Claude was already running (write test, confirm it
fails for the right reason, implement, rerun). The more interesting
judgment call was architectural: an earlier, simpler design would have had
the controller react only at the hard 49.50/50.50 limits. That would
satisfy the letter of Design Assumption 3 but would make the breaker
chatter open/closed every single second once frequency settled near a
limit (since the physics allows no steady closed state). The two-layer
protection+hysteresis design was chosen specifically to avoid that
pathological-but-technically-compliant behavior, and is documented with
its full rationale in `backend/simulator/constants.py` and the README so a
reviewer doesn't have to reverse-engineer the 49.70/50.30 "magic numbers."

### Prompt 4 — review and fix
> "Run a code review and security review before finalizing."

**Response:** Claude ran a security-reviewer agent and a code-reviewer
agent in parallel against the finished codebase. The security review came
back clean (no critical/high/medium findings — strict input allowlisting,
`debug=False`, loopback-only binding, no secrets, no dangerous sinks,
`textContent`-only DOM writes on the frontend), with one LOW doc-drift
note. The code review found three MEDIUM and two LOW issues, all genuine:

1. **Documented-but-unimplemented debounce.** `constants.py` and
   `sensor.py` *claimed* that `DEBOUNCE_SAMPLES` gated the controller's
   reaction to noisy readings, but nothing in `control.py` or `engine.py`
   ever read `consecutive_rejections` — the constant was dead, and the
   docstring was describing behavior the code didn't have.
   **Fix:** added `sensor.is_sensor_degraded()`, which turns true only
   once rejections persist for `DEBOUNCE_SAMPLES` readings in a row (a
   *sustained* sensor failure, distinct from the single-sample fault the
   faulty_sensor scenario injects), wired it through `engine.py` into a new
   `StepResult.sensor_degraded` field, exposed it in the API response, and
   added three new tests proving the gating actually works (single
   rejection ⇒ not degraded; `DEBOUNCE_SAMPLES` in a row ⇒ degraded; an
   accepted reading resets the streak).
2. **Chart.js global-registration anti-pattern.** The cursor-line plugin
   was being recreated and passed to the *global* `Chart.register()` on
   every single "Run simulation" click (up to 3× per click), rather than
   being attached per-chart — functionally harmless today but the wrong
   pattern, and a latent bug risk if the app grew more chart instances.
   **Fix:** hoisted the plugin to a single module-level `CURSOR_LINE_PLUGIN`
   object and attached it via each chart's own `plugins: [...]` config
   array instead of touching Chart.js's process-wide registry.
3. **Untested error/validation branches in `app.py`.** Coverage showed the
   out-of-range `initial_frequency` branch, the invalid/out-of-range
   `fault_time`/`generator_failure_time` branches, and the 500-error
   envelope path (what happens if `run_simulation` itself raises) were
   never exercised by `test_app.py`.
   **Fix:** added six tests, including one that monkeypatches
   `run_simulation` to raise and asserts the client-facing error message
   never leaks the real exception text — closing the gap from 94% to 99%
   backend coverage (67 tests total, up from 58).

Two LOW nits (a local `import` inside a property body instead of at module
scope in `models.py`; a comment in `control.py` that could be misread as
implying the protection check varies by `prev_breaker_closed`, when it's
actually always evaluated) were also fixed for clarity, though neither was
a functional bug.

**My input / modification:** Accepted all five findings as correctly
scoped (none felt like review theater — each pointed at a real gap between
what the code claimed and what it did, or a real anti-pattern). Fixed all
of them directly rather than just updating docs to match the weaker
behavior, since the stronger (debounce-respecting, non-leaking,
better-tested) behavior was easy to add and clearly the right outcome.

### What was *not* changed
The overall plan structure, module boundaries (physics / control / sensor
/ engine / scenarios as separate files), and the Flask API's response
envelope (`{success, data, error}`) were accepted as proposed and matched
the project's existing coding conventions (small cohesive files, immutable
dataclasses, validate-at-the-boundary). No architectural rework was needed
beyond the two fixes above.
