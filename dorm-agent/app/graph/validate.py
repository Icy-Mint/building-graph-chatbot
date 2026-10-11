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
            rooms = q(session, "MATCH (r:Room) RETURN r.number AS number, r.type AS type")
            check("exactly 8 Room nodes", len(rooms) == 8, f"found {len(rooms)}")

            dorms = [r for r in rooms if r["type"] == "dorm"]
            mech = [r for r in rooms if r["type"] == "mechanical"]
            check("exactly 6 dorm rooms", len(dorms) == len(DORM_ROOMS), f"found {len(dorms)}")
            check("exactly 2 mechanical rooms", len(mech) == len(MECHANICAL_ROOMS), f"found {len(mech)}")

            acs = q(session, "MATCH (a:ACUnit) RETURN a.label AS label")
            check("exactly 2 ACUnit nodes", len(acs) == len(AC_ASSIGNMENTS), f"found {len(acs)}")

            for ac in acs:
                serviced = q(
                    session,
                    "MATCH (a:ACUnit {label: $label})-[:SERVICES]->(r:Room) "
                    "RETURN r.number AS number, r.type AS type",
                    label=ac["label"],
                )
                check(f"{ac['label']} services exactly 3 rooms", len(serviced) == 3, f"found {len(serviced)}")
                check(
                    f"{ac['label']} only services dorm rooms",
                    all(r["type"] == "dorm" for r in serviced),
                    f"types={[r['type'] for r in serviced]}",
                )

                located = q(
                    session,
                    "MATCH (a:ACUnit {label: $label})-[:LOCATED_IN]->(r:Room) "
                    "RETURN r.number AS number, r.type AS type",
                    label=ac["label"],
                )
                check(
                    f"{ac['label']} located in exactly 1 mechanical room",
                    len(located) == 1 and located[0]["type"] == "mechanical",
                    f"found {located}",
                )

            for dorm in dorms:
                sensors = q(
                    session,
                    "MATCH (r:Room {number: $number})-[:HAS_SENSOR]->(s:Sensor) "
                    "RETURN s.label AS label, s.type AS type",
                    number=dorm["number"],
                )
                types = sorted(s["type"] for s in sensors)
                check(
                    f"room {dorm['number']} has exactly 1 temperature + 1 occupancy sensor",
                    types == ["occupancy", "temperature"],
                    f"found {types}",
                )

            for m in mech:
                sensors = q(
                    session,
                    "MATCH (r:Room {number: $number})-[:HAS_SENSOR]->(s:Sensor) RETURN s",
                    number=m["number"],
                )
                check(
                    f"mechanical room {m['number']} has no sensors",
                    len(sensors) == 0,
                    f"found {len(sensors)}",
                )

            temp_sensors = q(session, "MATCH (s:Sensor {type: 'temperature'}) RETURN s.label AS label")
            for ts in temp_sensors:
                monitors = q(
                    session,
                    "MATCH (s:Sensor {label: $label})-[:MONITORS_FOR]->(a:ACUnit) RETURN a.label AS label",
                    label=ts["label"],
                )
                has_one = check(
                    f"{ts['label']} has exactly 1 MONITORS_FOR edge",
                    len(monitors) == 1,
                    f"found {len(monitors)}",
                )
                if has_one:
                    servicing = q(
                        session,
                        "MATCH (s:Sensor {label: $label})<-[:HAS_SENSOR]-(r:Room) "
                        "MATCH (a:ACUnit)-[:SERVICES]->(r) "
                        "RETURN a.label AS label",
                        label=ts["label"],
                    )
                    servicing_labels = {row["label"] for row in servicing}
                    check(
                        f"{ts['label']}'s MONITORS_FOR AC matches its room's SERVICES AC",
                        monitors[0]["label"] in servicing_labels,
                        f"MONITORS_FOR={monitors[0]['label']}, SERVICES={servicing_labels}",
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
