"""Typed tools over TimescaleDB. Each pre-aggregates inside the tool and
caps row counts -- the LLM never sees thousands of raw rows. All queries
run through get_readonly_connection() (app/timeseries/client.py), a
genuine DB-enforced read-only Postgres role, not an application guard."""
from datetime import datetime, timedelta

from app.model import MECHANICAL_ROOMS
from app.timeseries.client import get_readonly_connection
from app.tools.errors import (
    MAX_ROW_CAP,
    validate_agg,
    validate_date_range,
    validate_horizon,
    validate_room_has_sensors,
)


def _split_rooms(room_numbers: list[str]):
    """Validates each room, returning (valid_dorm_rooms, errors_by_room).
    Partial validity is intentional: if the agent asks about 3 rooms and
    misspells one, it's more useful to answer for the other 2 and explain
    the mismatch than to fail the whole call."""
    valid, errors = [], {}
    for room in room_numbers:
        err = validate_room_has_sensors(room)
        if err:
            errors[room] = err
        else:
            valid.append(room)
    return valid, errors


def _rows_as_dicts(cur) -> list[dict]:
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_temp_readings(room_numbers: list[str], start: datetime, end: datetime, agg: str = "hourly") -> dict:
    agg_err = validate_agg(agg, ("raw", "hourly", "daily"))
    if agg_err:
        return agg_err
    range_err = validate_date_range(start, end)
    if range_err:
        return range_err

    valid_rooms, room_errors = _split_rooms(room_numbers)
    if not valid_rooms:
        return {"status": "no_sensors", "reason": "No valid dorm rooms with sensors in room_numbers.", "room_errors": room_errors}

    conn = get_readonly_connection()
    try:
        with conn.cursor() as cur:
            if agg == "raw":
                cur.execute(
                    "SELECT room_number, ts, value AS temp_c FROM sensor_readings "
                    "WHERE sensor_type = 'temperature' AND room_number = ANY(%s) AND ts >= %s AND ts < %s "
                    "ORDER BY room_number, ts LIMIT %s",
                    (valid_rooms, start, end, MAX_ROW_CAP + 1),
                )
            else:
                bucket = "1 hour" if agg == "hourly" else "1 day"
                cur.execute(
                    "SELECT room_number, time_bucket(%s, ts) AS bucket, avg(value) AS avg_temp_c "
                    "FROM sensor_readings WHERE sensor_type = 'temperature' AND room_number = ANY(%s) "
                    "AND ts >= %s AND ts < %s GROUP BY room_number, bucket ORDER BY room_number, bucket LIMIT %s",
                    (bucket, valid_rooms, start, end, MAX_ROW_CAP + 1),
                )
            rows = _rows_as_dicts(cur)
    finally:
        conn.close()

    if len(rows) > MAX_ROW_CAP:
        return {"status": "error", "reason": f"Result exceeds the row cap ({MAX_ROW_CAP}); narrow the date range or use a coarser agg."}
    if not rows:
        return {"status": "no_data", "room_numbers": valid_rooms, "start": start.isoformat(), "end": end.isoformat()}
    return {"status": "ok", "agg": agg, "room_errors": room_errors, "readings": rows}


def get_hot_periods(threshold_c: float, start: datetime, end: datetime) -> dict:
    """Per dorm room: which hours of day exceeded threshold_c, how often,
    and the max temperature seen in that hour -- across the whole date
    range, not filtered by room (there's no room_numbers arg in the spec
    for this one; it's meant to surface which rooms run hot at all)."""
    range_err = validate_date_range(start, end)
    if range_err:
        return range_err

    conn = get_readonly_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT room_number, extract(hour from ts)::int AS hour, "
                "count(*) FILTER (WHERE value > %s) AS over_threshold_count, "
                "max(value) AS max_temp_c "
                "FROM sensor_readings WHERE sensor_type = 'temperature' AND ts >= %s AND ts < %s "
                "GROUP BY room_number, hour "
                "HAVING count(*) FILTER (WHERE value > %s) > 0 "
                "ORDER BY room_number, hour",
                (threshold_c, start, end, threshold_c),
            )
            rows = _rows_as_dicts(cur)
    finally:
        conn.close()

    if not rows:
        return {"status": "no_data", "threshold_c": threshold_c, "start": start.isoformat(), "end": end.isoformat()}

    by_room: dict = {}
    for row in rows:
        by_room.setdefault(row["room_number"], []).append(
            {"hour": row["hour"], "count": row["over_threshold_count"], "max_temp_c": row["max_temp_c"]}
        )
    return {"status": "ok", "threshold_c": threshold_c, "by_room": by_room}


def get_occupancy(room_numbers: list[str], start: datetime, end: datetime, agg: str = "hourly") -> dict:
    agg_err = validate_agg(agg, ("hourly", "daily"))
    if agg_err:
        return agg_err
    range_err = validate_date_range(start, end)
    if range_err:
        return range_err

    valid_rooms, room_errors = _split_rooms(room_numbers)
    if not valid_rooms:
        return {"status": "no_sensors", "reason": "No valid dorm rooms with sensors in room_numbers.", "room_errors": room_errors}

    bucket = "1 hour" if agg == "hourly" else "1 day"
    conn = get_readonly_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT room_number, time_bucket(%s, ts) AS bucket, avg(value) AS occupancy_rate "
                "FROM sensor_readings WHERE sensor_type = 'occupancy' AND room_number = ANY(%s) "
                "AND ts >= %s AND ts < %s GROUP BY room_number, bucket ORDER BY room_number, bucket LIMIT %s",
                (bucket, valid_rooms, start, end, MAX_ROW_CAP + 1),
            )
            rows = _rows_as_dicts(cur)
    finally:
        conn.close()

    if len(rows) > MAX_ROW_CAP:
        return {"status": "error", "reason": f"Result exceeds the row cap ({MAX_ROW_CAP}); narrow the date range or use agg='daily'."}
    if not rows:
        return {"status": "no_data", "room_numbers": valid_rooms, "start": start.isoformat(), "end": end.isoformat()}
    return {"status": "ok", "agg": agg, "room_errors": room_errors, "readings": rows}


def forecast_occupancy(room_number: str, horizon_hours: int) -> dict:
    """Deterministic statistical forecast: average occupancy probability by
    (day-of-week, hour-of-day) computed from this room's full history, then
    looked up for each hour of the requested horizon. No LLM/ML randomness
    -- the agent reports these numbers, it doesn't generate them."""
    room_err = validate_room_has_sensors(room_number)
    if room_err:
        return room_err
    horizon_err = validate_horizon(horizon_hours)
    if horizon_err:
        return horizon_err

    conn = get_readonly_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT min(ts), max(ts) FROM sensor_readings WHERE sensor_type = 'occupancy' AND room_number = %s",
                (room_number,),
            )
            min_ts, max_ts = cur.fetchone()
            if min_ts is None:
                return {"status": "no_data", "room_number": room_number, "reason": "No occupancy history for this room."}

            cur.execute(
                "SELECT extract(dow from ts)::int AS dow, extract(hour from ts)::int AS hour, avg(value) AS prob "
                "FROM sensor_readings WHERE sensor_type = 'occupancy' AND room_number = %s "
                "GROUP BY dow, hour",
                (room_number,),
            )
            prob_by_dow_hour = {(dow, hour): prob for dow, hour, prob in cur.fetchall()}
    finally:
        conn.close()

    weeks_of_history = round(max((max_ts - min_ts).days / 7.0, 0.0), 2)

    forecast = []
    for h in range(horizon_hours):
        future_ts = max_ts + timedelta(hours=h + 1)
        # Postgres extract(dow): 0=Sunday..6=Saturday. Python .weekday(): 0=Monday..6=Sunday.
        pg_dow = (future_ts.weekday() + 1) % 7
        forecast.append({
            "ts": future_ts.isoformat(),
            "probability_occupied": prob_by_dow_hour.get((pg_dow, future_ts.hour)),
        })

    return {
        "status": "ok",
        "room_number": room_number,
        "horizon_hours": horizon_hours,
        "weeks_of_history": weeks_of_history,
        "forecast": forecast,
    }
