"""Asserts the dorm building graph's invariants hold. Exits non-zero if any
check fails, so it's safe to wire into CI or a `make validate` target.

Run:
    python -m app.graph.validate
"""
import sys

from app.graph.client import get_driver
from app.model import AC_ASSIGNMENTS, DORM_ROOMS, MECHANICAL_ROOMS

_failures = []


def q(session, cypher, **params):
    return [r.data() for r in session.run(cypher, **params)]


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" -- {detail}" if detail and not condition else ""))
    if not condition:
        _failures.append(label)
    return condition


def main():
    driver = get_driver()
    try:
        with driver.session() as session:
            rooms = q(session, "MATCH (r:Room) RETURN r.room_number AS room_number, r.type AS type")
            check("exactly 8 Room nodes", len(rooms) == 8, f"found {len(rooms)}")

            dorms = [r for r in rooms if r["type"] == "dorm"]
            mech = [r for r in rooms if r["type"] == "mechanical"]
            check("exactly 6 dorm rooms", len(dorms) == len(DORM_ROOMS), f"found {len(dorms)}")
            check("exactly 2 mechanical rooms", len(mech) == len(MECHANICAL_ROOMS), f"found {len(mech)}")

            acs = q(session, "MATCH (a:AC_Unit) RETURN a.ac_id AS ac_id")
            check("exactly 2 AC_Unit nodes", len(acs) == len(AC_ASSIGNMENTS), f"found {len(acs)}")

            for ac in acs:
                serviced = q(
                    session,
                    "MATCH (a:AC_Unit {ac_id: $ac_id})-[:SERVICES]->(r:Room) "
                    "RETURN r.room_number AS room_number, r.type AS type",
                    ac_id=ac["ac_id"],
                )
                check(f"{ac['ac_id']} services exactly 3 rooms", len(serviced) == 3, f"found {len(serviced)}")
                check(
                    f"{ac['ac_id']} only services dorm rooms",
                    all(r["type"] == "dorm" for r in serviced),
                    f"types={[r['type'] for r in serviced]}",
                )

                contained = q(
                    session,
                    "MATCH (r:Room)-[:CONTAINS]->(a:AC_Unit {ac_id: $ac_id}) "
                    "RETURN r.room_number AS room_number, r.type AS type",
                    ac_id=ac["ac_id"],
                )
                check(
                    f"{ac['ac_id']} contained in exactly 1 mechanical room",
                    len(contained) == 1 and contained[0]["type"] == "mechanical",
                    f"found {contained}",
                )

            for dorm in dorms:
                sensors = q(
                    session,
                    "MATCH (r:Room {room_number: $room_number})-[:HAS_SENSOR]->(s:Sensor) "
                    "RETURN s.sensor_id AS sensor_id, s.sensor_type AS sensor_type",
                    room_number=dorm["room_number"],
                )
                types = sorted(s["sensor_type"] for s in sensors)
                check(
                    f"room {dorm['room_number']} has exactly 1 temperature + 1 occupancy sensor",
                    types == ["occupancy", "temperature"],
                    f"found {types}",
                )

            for m in mech:
                sensors = q(
                    session,
                    "MATCH (r:Room {room_number: $room_number})-[:HAS_SENSOR]->(s:Sensor) RETURN s",
                    room_number=m["room_number"],
                )
                check(
                    f"mechanical room {m['room_number']} has no sensors",
                    len(sensors) == 0,
                    f"found {len(sensors)}",
                )

            temp_sensors = q(session, "MATCH (s:Sensor {sensor_type: 'temperature'}) RETURN s.sensor_id AS sensor_id")
            for ts in temp_sensors:
                reports = q(
                    session,
                    "MATCH (s:Sensor {sensor_id: $sensor_id})-[:REPORTS_TO]->(a:AC_Unit) RETURN a.ac_id AS ac_id",
                    sensor_id=ts["sensor_id"],
                )
                has_one = check(
                    f"{ts['sensor_id']} has exactly 1 REPORTS_TO edge",
                    len(reports) == 1,
                    f"found {len(reports)}",
                )
                if has_one:
                    servicing = q(
                        session,
                        "MATCH (s:Sensor {sensor_id: $sensor_id})<-[:HAS_SENSOR]-(r:Room) "
                        "MATCH (a:AC_Unit)-[:SERVICES]->(r) "
                        "RETURN a.ac_id AS ac_id",
                        sensor_id=ts["sensor_id"],
                    )
                    servicing_ids = {row["ac_id"] for row in servicing}
                    check(
                        f"{ts['sensor_id']}'s REPORTS_TO AC matches its room's SERVICES AC",
                        reports[0]["ac_id"] in servicing_ids,
                        f"REPORTS_TO={reports[0]['ac_id']}, SERVICES={servicing_ids}",
                    )
    finally:
        driver.close()

    if _failures:
        print(f"\n{len(_failures)} check(s) FAILED.")
        sys.exit(1)
    print("\nAll checks passed.")
    sys.exit(0)


if __name__ == "__main__":
    main()
