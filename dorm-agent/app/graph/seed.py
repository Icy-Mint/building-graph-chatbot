"""Idempotent Neo4j seed script for the dorm building graph. Every write uses
MERGE, so re-running this is safe and produces no duplicates -- the
uniqueness constraints below also make that a hard guarantee, not just a
convention.

Schema matches chatbot.py/chatbotForecast.py's existing Aura schema (see
app/model.py's docstring) so this database can be used as a drop-in local
alternative to Aura for those two files.

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
        "FOR (r:Room) REQUIRE r.room_number IS UNIQUE"
    )
    session.run(
        "CREATE CONSTRAINT sensor_id_unique IF NOT EXISTS "
        "FOR (s:Sensor) REQUIRE s.sensor_id IS UNIQUE"
    )
    session.run(
        "CREATE CONSTRAINT ac_id_unique IF NOT EXISTS "
        "FOR (a:AC_Unit) REQUIRE a.ac_id IS UNIQUE"
    )


def seed(session):
    session.run("MERGE (:Building {name: $name})", name=BUILDING_NAME)

    for room_number in DORM_ROOMS:
        session.run(
            "MATCH (b:Building {name: $bname}) "
            "MERGE (r:Room {room_number: $room_number}) SET r.type = 'dorm' "
            "MERGE (b)-[:HAS_ROOM]->(r)",
            bname=BUILDING_NAME, room_number=room_number,
        )
    for room_number in MECHANICAL_ROOMS:
        session.run(
            "MATCH (b:Building {name: $bname}) "
            "MERGE (r:Room {room_number: $room_number}) SET r.type = 'mechanical' "
            "MERGE (b)-[:HAS_ROOM]->(r)",
            bname=BUILDING_NAME, room_number=room_number,
        )

    for ac_id, info in AC_ASSIGNMENTS.items():
        session.run(
            "MERGE (a:AC_Unit {ac_id: $ac_id}) "
            "WITH a "
            "MATCH (mech:Room {room_number: $mech_room}) "
            "MERGE (mech)-[:CONTAINS]->(a)",
            ac_id=ac_id, mech_room=info["mechanical_room"],
        )
        for dorm in info["dorms"]:
            session.run(
                "MATCH (a:AC_Unit {ac_id: $ac_id}), (r:Room {room_number: $dorm}) "
                "MERGE (a)-[:SERVICES]->(r)",
                ac_id=ac_id, dorm=dorm,
            )
            session.run(
                "MATCH (r:Room {room_number: $dorm}), (a:AC_Unit {ac_id: $ac_id}) "
                "MERGE (t:Sensor {sensor_id: $temp_id}) SET t.sensor_type = 'temperature' "
                "MERGE (r)-[:HAS_SENSOR]->(t) "
                "MERGE (t)-[:REPORTS_TO]->(a) "
                "MERGE (o:Sensor {sensor_id: $occ_id}) SET o.sensor_type = 'occupancy' "
                "MERGE (r)-[:HAS_SENSOR]->(o)",
                dorm=dorm,
                ac_id=ac_id,
                temp_id=temp_sensor_label(dorm),
                occ_id=occupancy_sensor_label(dorm),
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
