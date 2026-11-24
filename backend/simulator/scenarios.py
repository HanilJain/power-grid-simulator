"""Named scenario registry and human-readable descriptions for the UI.

Scenarios themselves are expressed as SimConfig values (engine.run_simulation
reads config.scenario to decide what scripted events to apply); this module
just centralizes the set of valid names and their descriptions so the
frontend/API have one place to look them up.
"""

from __future__ import annotations

from backend.simulator.models import Scenario

SCENARIOS: tuple[Scenario, ...] = ("normal", "generator_failure", "faulty_sensor", "manual")

_DESCRIPTIONS: dict[str, str] = {
    "normal": (
        "Generator G101 runs continuously. The controller duty-cycles "
        "CB101 to keep the bus frequency inside the safe band, since the "
        "physics admits no steady closed state."
    ),
    "generator_failure": (
        "G101 fails partway through the run. Protection must open CB101 "
        "before frequency drops below the lower limit, then hold it open "
        "(no source left to power the load)."
    ),
    "faulty_sensor": (
        "FS101 reports a single physically-impossible low reading, "
        "reproducing last winter's 2am incident. With sensor validation "
        "enabled the bad reading is rejected and nothing trips; with it "
        "disabled the controller is fooled into an unnecessary trip."
    ),
    "manual": (
        "You script a sequence of G101/CB101 actions at chosen times "
        "instead of picking a fixed scenario. Predictive protection can "
        "optionally override an unsafe forced CLOSE (toggle "
        "'protection_enabled') so you can compare a guarded run against "
        "one where you are given full, unguarded control."
    ),
}


def describe_scenario(name: str) -> str:
    """Return the human-readable description for a registered scenario name.

    Raises KeyError for any name not in SCENARIOS.
    """
    return _DESCRIPTIONS[name]
