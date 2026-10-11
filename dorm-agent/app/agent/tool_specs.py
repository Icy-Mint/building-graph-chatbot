"""Anthropic tool-use definitions for the 7 typed tools (app/tools/), plus
the dispatch table + argument adapter the agent loop uses to actually call
them. Kept separate from the tool functions themselves so the model-facing
description text can be hand-tuned independently of the Python signatures.
"""
from datetime import datetime

from app.tools.graph_tools import get_ac_for_room, get_room_info, get_rooms_serviced_by_ac
from app.tools.timeseries_tools import forecast_occupancy, get_hot_periods, get_occupancy, get_temp_readings

TOOLS = [
    {
        "name": "get_rooms_serviced_by_ac",
        "description": (
            "List the dorm rooms serviced by one AC unit, or by every AC unit if ac_label is omitted. "
            "Use this to resolve an AC-unit-to-room relationship, e.g. before explaining why a group of "
            "rooms shares a temperature pattern."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ac_label": {
                    "type": ["string", "null"],
                    "description": "e.g. 'AC-1' or 'AC-2'. Omit or pass null for all AC units.",
                },
            },
        },
    },
    {
        "name": "get_ac_for_room",
        "description": (
            "Which AC unit services a given dorm room. Mechanical rooms don't have a servicing AC -- "
            "they house one instead. Use get_room_info if you need that distinction."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"room_number": {"type": "string", "description": "e.g. '101'."}},
            "required": ["room_number"],
        },
    },
    {
        "name": "get_room_info",
        "description": (
            "Type (dorm/mechanical), sensors, and AC relationship for any room, dorm or mechanical. "
            "Use this first when you're not sure whether a room has sensors."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"room_number": {"type": "string"}},
            "required": ["room_number"],
        },
    },
    {
        "name": "get_temp_readings",
        "description": (
            "Temperature readings (Celsius) for one or more dorm rooms over a date range, either "
            "pre-aggregated (hourly/daily average) or raw 5-minute samples. Capped at 2000 rows -- if "
            "you get a row-cap error, use a coarser agg or a narrower date range, don't retry as-is."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "room_numbers": {"type": "array", "items": {"type": "string"}},
                "start": {"type": "string", "description": "ISO 8601 datetime, e.g. '2024-01-01T00:00:00Z'."},
                "end": {"type": "string", "description": "ISO 8601 datetime."},
                "agg": {"type": "string", "enum": ["raw", "hourly", "daily"], "default": "hourly"},
            },
            "required": ["room_numbers", "start", "end"],
        },
    },
    {
        "name": "get_hot_periods",
        "description": (
            "Across all dorm rooms, which hours of day exceeded a temperature threshold during a date "
            "range, how often, and the max temperature seen. Use this to find which rooms run hot before "
            "asking which AC unit services them."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "threshold_c": {"type": "number"},
                "start": {"type": "string", "description": "ISO 8601 datetime."},
                "end": {"type": "string", "description": "ISO 8601 datetime."},
            },
            "required": ["threshold_c", "start", "end"],
        },
    },
    {
        "name": "get_occupancy",
        "description": (
            "Occupancy rate (fraction of time occupied, 0-1) for one or more dorm rooms over a date "
            "range, aggregated hourly or daily."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "room_numbers": {"type": "array", "items": {"type": "string"}},
                "start": {"type": "string", "description": "ISO 8601 datetime."},
                "end": {"type": "string", "description": "ISO 8601 datetime."},
                "agg": {"type": "string", "enum": ["hourly", "daily"], "default": "hourly"},
            },
            "required": ["room_numbers", "start", "end"],
        },
    },
    {
        "name": "forecast_occupancy",
        "description": (
            "Deterministic statistical forecast of occupancy probability for one dorm room over the next "
            "horizon_hours, computed from historical day-of-week + hour-of-day averages. This is the ONLY "
            "way to answer a forecasting question -- never estimate or guess a probability yourself."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "room_number": {"type": "string"},
                "horizon_hours": {"type": "integer", "description": "How many hours ahead to forecast, e.g. 24."},
            },
            "required": ["room_number", "horizon_hours"],
        },
    },
]

_DATETIME_ARGS = {
    "get_temp_readings": ("start", "end"),
    "get_hot_periods": ("start", "end"),
    "get_occupancy": ("start", "end"),
}

_DISPATCH = {
    "get_rooms_serviced_by_ac": get_rooms_serviced_by_ac,
    "get_ac_for_room": get_ac_for_room,
    "get_room_info": get_room_info,
    "get_temp_readings": get_temp_readings,
    "get_hot_periods": get_hot_periods,
    "get_occupancy": get_occupancy,
    "forecast_occupancy": forecast_occupancy,
}


def parse_datetime_arg(value: str) -> datetime:
    """Claude emits ISO 8601 strings for date args; the typed tools expect
    datetime objects. Accepts a trailing 'Z' (Claude will commonly produce
    one) as UTC, since datetime.fromisoformat() alone doesn't."""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def call_tool(name: str, raw_args: dict) -> dict:
    """Parses Claude's raw JSON tool-call arguments into what the typed
    functions expect, calls the function, and returns its structured
    result. Never raises -- a bad call becomes a {"status": "error", ...}
    result fed back to the model, same shape as every other tool error."""
    if name not in _DISPATCH:
        return {"status": "error", "reason": f"Unknown tool '{name}'."}

    args = dict(raw_args)
    for field in _DATETIME_ARGS.get(name, ()):
        if field in args and isinstance(args[field], str):
            try:
                args[field] = parse_datetime_arg(args[field])
            except ValueError as e:
                return {"status": "error", "reason": f"Could not parse '{field}'='{args[field]}' as a datetime: {e}"}

    try:
        return _DISPATCH[name](**args)
    except TypeError as e:
        return {"status": "error", "reason": f"Bad arguments for {name}: {e}"}
