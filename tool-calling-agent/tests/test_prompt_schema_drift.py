"""Drift guard (Step 6): fails if the system prompt's hand-written
description of the building, or the tool JSON schemas the model sees,
disagree with the actual Neo4j schema or the real tool function
signatures. The prompt and the tool specs are both hand-maintained text
that nothing forces to stay in sync with the code they describe -- this
test is that force. Requires the live Neo4j container (same convention as
test_load_readings.py).
"""
import inspect

from app.agent.prompts import SYSTEM_PROMPT
from app.agent.tool_specs import TOOLS, _DISPATCH
from app.graph.client import get_driver
from app.model import AC_ASSIGNMENTS, DORM_ROOMS, MECHANICAL_ROOMS


def _live_counts():
    driver = get_driver()
    try:
        with driver.session() as session:
            room_count = session.run("MATCH (r:Room) RETURN count(r) AS n").single()["n"]
            ac_count = session.run("MATCH (a:AC_Unit) RETURN count(a) AS n").single()["n"]
            rooms_per_ac = session.run(
                "MATCH (a:AC_Unit)-[:SERVICES]->(r:Room) RETURN a.ac_id AS ac, count(r) AS n"
            ).data()
            ac_ids = sorted(session.run("MATCH (a:AC_Unit) RETURN a.ac_id AS id").value("id"))
    finally:
        driver.close()
    return {
        "room_count": room_count,
        "ac_count": ac_count,
        "rooms_per_ac": {row["ac"]: row["n"] for row in rooms_per_ac},
        "ac_ids": ac_ids,
    }


def test_model_constants_match_live_neo4j():
    """model.py is supposed to be the single source of truth seed.py,
    validate.py, and the tool layer all import from -- if it's ever edited
    without re-seeding, this catches the mismatch before the prompt-drift
    checks below build on top of a false assumption."""
    live = _live_counts()
    assert live["room_count"] == len(DORM_ROOMS) + len(MECHANICAL_ROOMS)
    assert live["ac_count"] == len(AC_ASSIGNMENTS)
    assert live["ac_ids"] == sorted(AC_ASSIGNMENTS)
    for ac, info in AC_ASSIGNMENTS.items():
        assert live["rooms_per_ac"].get(ac) == len(info["dorms"])


def test_system_prompt_room_and_ac_counts_match_model():
    """The numbers in SYSTEM_PROMPT are typed prose, not derived from
    model.py -- if someone changes the building size without updating the
    prompt (or vice versa), the agent would describe a building that
    doesn't match what its tools actually see."""
    total_rooms = len(DORM_ROOMS) + len(MECHANICAL_ROOMS)
    assert f"{total_rooms} rooms" in SYSTEM_PROMPT, (
        f"SYSTEM_PROMPT doesn't mention '{total_rooms} rooms' -- model.py has "
        f"{len(DORM_ROOMS)} dorm + {len(MECHANICAL_ROOMS)} mechanical rooms"
    )
    assert str(len(AC_ASSIGNMENTS)) in SYSTEM_PROMPT or "two" in SYSTEM_PROMPT.lower(), (
        f"SYSTEM_PROMPT doesn't mention the AC unit count ({len(AC_ASSIGNMENTS)})"
    )
    for ac in AC_ASSIGNMENTS:
        assert ac in SYSTEM_PROMPT, f"AC unit id {ac!r} (from model.py) isn't mentioned in SYSTEM_PROMPT"
    dorms_per_ac = {len(info["dorms"]) for info in AC_ASSIGNMENTS.values()}
    assert len(dorms_per_ac) == 1, "AC_ASSIGNMENTS has an uneven dorms-per-AC split -- SYSTEM_PROMPT's '3 dorm rooms' claim assumes they're even"
    assert str(dorms_per_ac.pop()) in SYSTEM_PROMPT


def test_system_prompt_room_numbers_match_model():
    dorm_range = f"{DORM_ROOMS[0]}-{DORM_ROOMS[-1]}"
    mech_range = f"{MECHANICAL_ROOMS[0]}-{MECHANICAL_ROOMS[-1]}"
    assert dorm_range in SYSTEM_PROMPT, f"SYSTEM_PROMPT doesn't mention dorm room range {dorm_range!r}"
    assert mech_range in SYSTEM_PROMPT, f"SYSTEM_PROMPT doesn't mention mechanical room range {mech_range!r}"


def test_tool_specs_cover_every_dispatch_entry():
    """Every tool the model can be told to call must have a dispatch entry,
    and vice versa -- a tool removed from one but not the other is either
    dead code or an uncallable tool definition."""
    spec_names = {t["name"] for t in TOOLS}
    assert spec_names == set(_DISPATCH), (
        f"TOOLS and _DISPATCH disagree: in TOOLS only: {spec_names - set(_DISPATCH)}, "
        f"in _DISPATCH only: {set(_DISPATCH) - spec_names}"
    )


def test_tool_spec_schemas_match_real_function_signatures():
    """The JSON schema the model sees for each tool's arguments must name
    the same parameters the real Python function takes -- otherwise the
    model could be invited to pass an argument the function will reject,
    or never told about one it needs."""
    for tool in TOOLS:
        fn = _DISPATCH[tool["name"]]
        real_params = set(inspect.signature(fn).parameters)
        spec_params = set(tool["input_schema"].get("properties", {}))
        assert spec_params <= real_params, (
            f"{tool['name']}: spec declares {spec_params - real_params} which isn't "
            f"a real parameter of {fn.__name__}{inspect.signature(fn)}"
        )
        required = set(tool["input_schema"].get("required", []))
        params_without_defaults = {
            name for name, p in inspect.signature(fn).parameters.items() if p.default is inspect.Parameter.empty
        }
        assert params_without_defaults <= spec_params, (
            f"{tool['name']}: {fn.__name__} requires {params_without_defaults} but the spec "
            f"only declares {spec_params}"
        )
        assert required <= real_params, f"{tool['name']}: spec requires {required - real_params}, not real parameters"
