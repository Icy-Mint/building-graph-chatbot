"""Idempotent Neo4j seed script for the dorm building graph. Every write uses
MERGE, so re-running this is safe and produces no duplicates -- the
uniqueness constraints below also make that a hard guarantee, not just a
convention.

Run:
    python -m app.graph.seed
"""
from app.graph.client import get_driver
from app.model import (
    AC_ASSIGNMENTS,
    BUILDING_NAME,
    DORM_ROOMS,
    MECHANICAL_ROOMS,
    occupancy_sensor_label,
    temp_sensor_label,
)


def apply_constraints(session):
    session.run(
        "CREATE CONSTRAINT room_number_unique IF NOT EXISTS "
        "FOR (r:Room) REQUIRE r.number IS UNIQUE"
    )
    session.run(
        "CREATE CONSTRAINT sensor_label_unique IF NOT EXISTS "
        "FOR (s:Sensor) REQUIRE s.label IS UNIQUE"
    )
    session.run(
        "CREATE CONSTRAINT acunit_label_unique IF NOT EXISTS "
        "FOR (a:ACUnit) REQUIRE a.label IS UNIQUE"
    )


def seed(session):
    session.run("MERGE (:Building {name: $name})", name=BUILDING_NAME)

    for number in DORM_ROOMS:
        session.run(
            "MATCH (b:Building {name: $bname}) "
            "MERGE (r:Room {number: $number}) SET r.type = 'dorm' "
            "MERGE (b)-[:HAS_ROOM]->(r)",
            bname=BUILDING_NAME, number=number,
        )
    for number in MECHANICAL_ROOMS:
        session.run(
            "MATCH (b:Building {name: $bname}) "
            "MERGE (r:Room {number: $number}) SET r.type = 'mechanical' "
            "MERGE (b)-[:HAS_ROOM]->(r)",
            bname=BUILDING_NAME, number=number,
        )

    for ac_label, info in AC_ASSIGNMENTS.items():
        session.run(
            "MERGE (a:ACUnit {label: $ac_label}) "
            "WITH a "
            "MATCH (mech:Room {number: $mech_room}) "
            "MERGE (a)-[:LOCATED_IN]->(mech)",
            ac_label=ac_label, mech_room=info["mechanical_room"],
        )
        for dorm in info["dorms"]:
            session.run(
                "MATCH (a:ACUnit {label: $ac_label}), (r:Room {number: $dorm}) "
                "MERGE (a)-[:SERVICES]->(r)",
                ac_label=ac_label, dorm=dorm,
            )
            session.run(
                "MATCH (r:Room {number: $dorm}), (a:ACUnit {label: $ac_label}) "
                "MERGE (t:Sensor {label: $temp_label}) SET t.type = 'temperature' "
                "MERGE (r)-[:HAS_SENSOR]->(t) "
                "MERGE (t)-[:MONITORS_FOR]->(a) "
                "MERGE (o:Sensor {label: $occ_label}) SET o.type = 'occupancy' "
                "MERGE (r)-[:HAS_SENSOR]->(o)",
                dorm=dorm,
                ac_label=ac_label,
                temp_label=temp_sensor_label(dorm),
                occ_label=occupancy_sensor_label(dorm),
            )


def main():
    driver = get_driver()
    try:
        with driver.session() as session:
            apply_constraints(session)
            seed(session)
        print("Seed complete.")
    finally:
        driver.close()


if __name__ == "__main__":
    main()
