"""Shared constants describing the dorm building graph model. Both seed.py
and validate.py import from here so the two can't silently drift apart.

Graph shape:
    (:Building)-[:HAS_ROOM]->(:Room {number, type})
    (:ACUnit {label})-[:LOCATED_IN]->(:Room {type: "mechanical"})
    (:ACUnit)-[:SERVICES]->(:Room {type: "dorm"})          exactly 3 dorms per AC
    (:Room)-[:HAS_SENSOR]->(:Sensor {label, type})         type: temperature | occupancy
    (:Sensor {type: "temperature"})-[:MONITORS_FOR]->(:ACUnit)

Mechanical rooms intentionally have no sensors.
"""

BUILDING_NAME = "Dorm Hall A"

DORM_ROOMS = ["101", "102", "103", "104", "105", "106"]
MECHANICAL_ROOMS = ["201", "202"]

# Which AC unit is located in which mechanical room and services which dorms.
AC_ASSIGNMENTS = {
    "AC-1": {"mechanical_room": "201", "dorms": ["101", "102", "103"]},
    "AC-2": {"mechanical_room": "202", "dorms": ["104", "105", "106"]},
}


def temp_sensor_label(room_number: str) -> str:
    return f"TEMP_{room_number}"


def occupancy_sensor_label(room_number: str) -> str:
    return f"OCC_{room_number}"
