"""Shared constants describing the dorm building graph model. seed.py,
validate.py, and the tool layer all import from here so none of them can
silently drift apart.

Graph shape (standardized to match chatbot.py/chatbotForecast.py's existing
Aura schema, so the local Neo4j this project seeds can be used as a drop-in
alternative to Aura for those two files -- no code changes on their side,
just a connection string):
    (:Building)-[:HAS_ROOM]->(:Room {room_number, type})
    (:Room {type: "mechanical"})-[:CONTAINS]->(:AC_Unit {ac_id})
    (:AC_Unit)-[:SERVICES]->(:Room {type: "dorm"})              exactly 3 dorms per AC
    (:Room)-[:HAS_SENSOR]->(:Sensor {sensor_id, sensor_type})   sensor_type: temperature | occupancy
    (:Sensor {sensor_type: "temperature"})-[:REPORTS_TO]->(:AC_Unit)
    (:Sensor)-[:RECORDED]->(:Reading {timestamp, value, sensor_type, room_number})

Mechanical rooms intentionally have no sensors. Reading nodes are a
duplicate of TimescaleDB's sensor_readings table (see
app/graph/load_readings.py) -- they exist only so chatbotForecast.py's
existing NL-to-Cypher design, which expects sensor data to live in Neo4j,
keeps working unmodified against this database. TimescaleDB remains the
source of truth; Reading nodes are loaded from it, never the reverse.
"""

BUILDING_NAME = "Dorm Hall A"

DORM_ROOMS = ["101", "102", "103", "104", "105", "106"]
MECHANICAL_ROOMS = ["201", "202"]

# Which AC unit is located in which mechanical room and services which dorms.
AC_ASSIGNMENTS = {
    "AC1": {"mechanical_room": "201", "dorms": ["101", "102", "103"]},
    "AC2": {"mechanical_room": "202", "dorms": ["104", "105", "106"]},
}


def temp_sensor_label(room_number: str) -> str:
    return f"TEMP_{room_number}"


def occupancy_sensor_label(room_number: str) -> str:
    return f"OCC_{room_number}"


# ─────────────────────────────────────────
# Time-series generation profiles (Step 2)
# Deliberately assigned to cut ACROSS the AC groupings above, so "which
# rooms on AC1 run hot" is an actual question with a non-trivial answer
# instead of "all of them, because AC1 happens to equal the sun group."
# AC1 = {101,102,103} contributes 2 sun rooms + 1 shaded; AC2 = {104,105,106}
# contributes 1 sun room + 2 shaded.
# ─────────────────────────────────────────
TEMP_PROFILES = {
    "sun": {
        "rooms": ["101", "102", "104"],
        "base_c": 23.0,
        "amplitude_c": 5.0,
        "peak_hour": 16,  # mid-afternoon/early-evening peak
        "noise_sigma_c": 0.5,
    },
    "shaded": {
        "rooms": ["103", "105", "106"],
        "base_c": 20.0,
        "amplitude_c": 3.5,
        "peak_hour": 14,  # earlier, smaller peak than the sun-facing rooms
        "noise_sigma_c": 0.5,
    },
}

# Hourly occupancy PROBABILITY (not a hard on/off window) so 14 days of
# samples gives forecast_occupancy (Step 3) real day-of-week/hour-of-day
# signal to recover, rather than a noiseless step function.
OCCUPANCY_PROFILES = {
    "full_time_student": {
        "rooms": ["101", "103", "105"],
        # home 7-9am, 1-3pm, 8-10pm, and overnight
        "weekday_hourly_prob": [
            0.95, 0.95, 0.95, 0.95, 0.95, 0.95, 0.95,  # 0-6: overnight
            0.90, 0.90,                                 # 7-8: home before class
            0.15, 0.10, 0.10,                           # 9-11: at class
            0.15,                                       # 12: transition
            0.85, 0.85,                                 # 13-14: home 1-3pm
            0.15, 0.10, 0.10, 0.10,                      # 15-18: at class/out
            0.15,                                       # 19: transition
            0.85, 0.85,                                 # 20-21: home 8-10pm
            0.50,                                       # 22: transition
            0.90,                                       # 23: overnight begins
        ],
        # no class to structure the day around; home most of the time
        # except a short midday outing
        "weekend_hourly_prob": (
            [0.90] * 11 +   # 0-10
            [0.20] * 3 +    # 11-13: out
            [0.85] * 10     # 14-23
        ),
    },
    "night_shift_student": {
        "rooms": ["102", "104", "106"],
        # home 6-8am, 4-6pm, 9-11pm, and overnight
        "weekday_hourly_prob": [
            0.90, 0.90, 0.90, 0.90, 0.90, 0.90,  # 0-5: overnight
            0.85, 0.85,                          # 6-7: home 6-8am
            0.10, 0.10, 0.10, 0.10, 0.10, 0.10, 0.10, 0.10,  # 8-15: class/work
            0.85, 0.85,                          # 16-17: home 4-6pm
            0.10, 0.10, 0.10,                    # 18-20: evening shift
            0.80, 0.80,                          # 21-22: home 9-11pm
            0.90,                                # 23: overnight begins
        ],
        "weekend_hourly_prob": (
            [0.85] * 12 +   # 0-11
            [0.20] * 3 +    # 12-14: out
            [0.80] * 9      # 15-23
        ),
    },
}


def room_temp_profile(room_number: str) -> str:
    for name, info in TEMP_PROFILES.items():
        if room_number in info["rooms"]:
            return name
    raise KeyError(f"No temperature profile assigned to room {room_number}")


def room_occupancy_profile(room_number: str) -> str:
    for name, info in OCCUPANCY_PROFILES.items():
        if room_number in info["rooms"]:
            return name
    raise KeyError(f"No occupancy profile assigned to room {room_number}")
