"""Creates the sensor_readings hypertable and the hourly-average-temperature
continuous aggregate. Idempotent -- safe to re-run.

Run:
    python -m app.timeseries.schema
"""
from app.timeseries.client import get_connection

CREATE_EXTENSION = "CREATE EXTENSION IF NOT EXISTS timescaledb;"

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS sensor_readings (
    ts           TIMESTAMPTZ NOT NULL,
    sensor_label TEXT NOT NULL,
    room_number  TEXT NOT NULL,
    sensor_type  TEXT NOT NULL,  -- 'temperature' | 'occupancy'
    value        DOUBLE PRECISION NOT NULL
);
"""

CREATE_HYPERTABLE = "SELECT create_hypertable('sensor_readings', 'ts', if_not_exists => TRUE);"

CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_sensor_readings_sensor_ts
    ON sensor_readings (sensor_label, ts DESC);
"""

CREATE_CONTINUOUS_AGGREGATE = """
CREATE MATERIALIZED VIEW IF NOT EXISTS hourly_temp_by_room
WITH (timescaledb.continuous) AS
SELECT
    room_number,
    time_bucket('1 hour', ts) AS bucket,
    avg(value) AS avg_temp_c
FROM sensor_readings
WHERE sensor_type = 'temperature'
GROUP BY room_number, bucket
WITH NO DATA;
"""

ADD_REFRESH_POLICY = """
SELECT add_continuous_aggregate_policy('hourly_temp_by_room',
    start_offset => INTERVAL '30 days',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE
);
"""


def main():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(CREATE_EXTENSION)
            cur.execute(CREATE_TABLE)
            cur.execute(CREATE_HYPERTABLE)
            cur.execute(CREATE_INDEX)
            cur.execute(CREATE_CONTINUOUS_AGGREGATE)
            cur.execute(ADD_REFRESH_POLICY)
        conn.commit()
        print("Schema ready: sensor_readings hypertable + hourly_temp_by_room continuous aggregate.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
