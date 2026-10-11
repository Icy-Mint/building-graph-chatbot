"""Typed tools over the Neo4j graph. Each function runs exactly one
hand-written, parameterized Cypher query -- the LLM picks which tool to
call and what arguments to pass, but never writes Cypher itself. Every
query goes through run_read_only() (see app/graph/client.py)."""
from app.graph.client import get_driver, run_read_only
from app.model import AC_ASSIGNMENTS, DORM_ROOMS, MECHANICAL_ROOMS
from app.tools.errors import validate_known_room

ALL_ROOMS = DORM_ROOMS + MECHANICAL_ROOMS


def get_rooms_serviced_by_ac(ac_label: str | None = None) -> dict:
    """Rooms serviced by one AC unit, or by every AC unit if ac_label is None."""
    if ac_label is not None and ac_label not in AC_ASSIGNMENTS:
        return {
            "status": "error",
            "reason": f"Unknown AC unit '{ac_label}'.",
            "valid_ac_units": sorted(AC_ASSIGNMENTS),
        }

    driver = get_driver()
    try:
        with driver.session() as session:
            if ac_label is not None:
                rows = run_read_only(
                    session,
                    "MATCH (a:ACUnit {label: $ac_label})-[:SERVICES]->(r:Room) "
                    "RETURN a.label AS ac_label, r.number AS room_number "
                    "ORDER BY r.number",
                    ac_label=ac_label,
                ).data()
            else:
                rows = run_read_only(
                    session,
                    "MATCH (a:ACUnit)-[:SERVICES]->(r:Room) "
                    "RETURN a.label AS ac_label, r.number AS room_number "
                    "ORDER BY a.label, r.number",
                ).data()
    finally:
        driver.close()

    if not rows:
        return {"status": "no_data", "ac_label": ac_label, "results": []}
    return {"status": "ok", "ac_label": ac_label, "results": rows}


def get_ac_for_room(room_number: str) -> dict:
    """Which AC unit services this room. Mechanical rooms don't have a
    servicing AC (they house one instead -- see get_room_info's houses_ac),
    so that's a no_data result here, not an error."""
    err = validate_known_room(room_number)
    if err:
        return err
    if room_number in MECHANICAL_ROOMS:
        return {
            "status": "no_data",
            "room_number": room_number,
            "reason": f"Room {room_number} is a mechanical room; it houses an AC unit rather than being serviced by one.",
        }

    driver = get_driver()
    try:
        with driver.session() as session:
            rows = run_read_only(
                session,
                "MATCH (a:ACUnit)-[:SERVICES]->(r:Room {number: $room_number}) RETURN a.label AS ac_label",
                room_number=room_number,
            ).data()
    finally:
        driver.close()

    if not rows:
        return {"status": "no_data", "room_number": room_number, "reason": "No AC unit services this room."}
    return {"status": "ok", "room_number": room_number, "ac_label": rows[0]["ac_label"]}


def get_room_info(room_number: str) -> dict:
    """Type, sensors, and AC relationship for a room. Works for both dorm
    and mechanical rooms -- unlike the sensor-data tools, a mechanical room
    is a complete, valid answer here (type='mechanical', sensors=[]), not a
    no_sensors short-circuit, since "what is this room" is meaningful even
    for a room with no sensors."""
    err = validate_known_room(room_number)
    if err:
        return err

    driver = get_driver()
    try:
        with driver.session() as session:
            room_row = run_read_only(
                session, "MATCH (r:Room {number: $room_number}) RETURN r.type AS type", room_number=room_number
            ).data()
            sensors = run_read_only(
                session,
                "MATCH (r:Room {number: $room_number})-[:HAS_SENSOR]->(s:Sensor) "
                "RETURN s.label AS label, s.type AS type ORDER BY s.type",
                room_number=room_number,
            ).data()
            serviced_by = run_read_only(
                session,
                "MATCH (a:ACUnit)-[:SERVICES]->(r:Room {number: $room_number}) RETURN a.label AS ac_label",
                room_number=room_number,
            ).data()
            houses = run_read_only(
                session,
                "MATCH (a:ACUnit)-[:LOCATED_IN]->(r:Room {number: $room_number}) RETURN a.label AS ac_label",
                room_number=room_number,
            ).data()
    finally:
        driver.close()

    return {
        "status": "ok",
        "room_number": room_number,
        "type": room_row[0]["type"],
        "sensors": sensors,
        "serviced_by_ac": serviced_by[0]["ac_label"] if serviced_by else None,
        "houses_ac": houses[0]["ac_label"] if houses else None,
    }
