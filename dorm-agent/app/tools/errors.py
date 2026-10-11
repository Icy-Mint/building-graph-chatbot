"""Shared argument validation for the tool layer. Every tool validates its
arguments BEFORE querying, and every tool returns one of:
    {"status": "ok", ...}
    {"status": "no_data", ...}
    {"status": "no_sensors", "reason": "..."}
    {"status": "error", "reason": "..."}

"no_sensors" is specifically for sensor-data requests (temperature/
occupancy) against a mechanical room -- it must stay distinguishable from
both "error" (bad input) and "no_data" (valid input, nothing matched), per
the spec. Graph tools that aren't about sensor data (e.g. "what AC unit
services this room") use validate_known_room instead, since a mechanical
room is a perfectly valid thing to ask about there, just one with a
different answer shape.
"""
from app.model import DORM_ROOMS, MECHANICAL_ROOMS

ALL_ROOMS = DORM_ROOMS + MECHANICAL_ROOMS
MAX_ROW_CAP = 2000
MAX_FORECAST_HORIZON_HOURS = 24 * 14  # don't forecast further than we plausibly have signal for


def validate_known_room(room_number: str):
    if room_number not in ALL_ROOMS:
        return {"status": "error", "reason": f"Unknown room '{room_number}'.", "valid_rooms": ALL_ROOMS}
    return None


def validate_room_has_sensors(room_number: str):
    """For tools that read sensor data. Distinguishes an unknown room
    (error) from a known-but-sensorless mechanical room (no_sensors) --
    these must not collapse into the same status."""
    err = validate_known_room(room_number)
    if err:
        return err
    if room_number in MECHANICAL_ROOMS:
        return {"status": "no_sensors", "reason": f"Room {room_number} is a mechanical room and has no sensors."}
    return None


def validate_date_range(start, end):
    if start >= end:
        return {"status": "error", "reason": f"start ({start}) must be before end ({end})."}
    return None


def validate_horizon(horizon_hours: int):
    if horizon_hours <= 0:
        return {"status": "error", "reason": f"horizon_hours must be positive, got {horizon_hours}."}
    if horizon_hours > MAX_FORECAST_HORIZON_HOURS:
        return {
            "status": "error",
            "reason": f"horizon_hours {horizon_hours} exceeds the max of {MAX_FORECAST_HORIZON_HOURS}.",
        }
    return None


def validate_agg(agg: str, allowed: tuple) -> dict | None:
    if agg not in allowed:
        return {"status": "error", "reason": f"agg must be one of {allowed}, got '{agg}'."}
    return None
