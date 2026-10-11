"""Generates synthetic sensor readings and loads them into TimescaleDB.

Deterministic given --seed: re-running with the same seed reproduces the
exact same data. Clears any previously generated readings first, so reruns
(same seed or different) never duplicate/append -- the table always reflects
exactly one generation run.

Run:
    python -m app.timeseries.generate --seed 42 --days 14
"""
import argparse
from datetime import datetime, timedelta, timezone

import numpy as np

from app.model import (
    DORM_ROOMS,
    OCCUPANCY_PROFILES,
    TEMP_PROFILES,
    occupancy_sensor_label,
    room_occupancy_profile,
    room_temp_profile,
    temp_sensor_label,
)
from app.timeseries.client import get_connection


def _temp_series(room: str, timestamps, rng: np.random.Generator) -> np.ndarray:
    """base + amplitude * sin(2*pi*(hour - peak_shift)/24) + gaussian noise.
    peak_shift is derived from peak_hour so the curve's maximum lands there
    (sin peaks at argument=pi/2, i.e. when hour == peak_shift + 6)."""
    profile = TEMP_PROFILES[room_temp_profile(room)]
    peak_shift = profile["peak_hour"] - 6

    hour_frac = np.array([ts.hour + ts.minute / 60.0 for ts in timestamps])
    signal = profile["base_c"] + profile["amplitude_c"] * np.sin(2 * np.pi * (hour_frac - peak_shift) / 24)
    noise = rng.normal(0, profile["noise_sigma_c"], size=len(timestamps))
    return signal + noise


def _occupancy_series(room: str, timestamps, rng: np.random.Generator) -> np.ndarray:
    """Bernoulli draw per timestamp using the room's weekday/weekend hourly
    probability -- gives forecast_occupancy (Step 3) a real, recoverable
    day-of-week + hour-of-day signal instead of a noiseless step function."""
    profile = OCCUPANCY_PROFILES[room_occupancy_profile(room)]
    probs = np.array([
        profile["weekend_hourly_prob"][ts.hour] if ts.weekday() >= 5
        else profile["weekday_hourly_prob"][ts.hour]
        for ts in timestamps
    ])
    draws = rng.random(len(timestamps))
    return (draws < probs).astype(float)


def generate_timestamps(start: datetime, days: int, interval_minutes: int) -> list:
    total_steps = int(days * 24 * 60 / interval_minutes)
    return [start + timedelta(minutes=i * interval_minutes) for i in range(total_steps)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--interval-minutes", type=int, default=5)
    parser.add_argument(
        "--start", type=str, default="2024-01-01",
        help="YYYY-MM-DD, UTC. Falls on a Monday by default for clean weekday/weekend alignment.",
    )
    args = parser.parse_args()

    if args.days < 14:
        print(f"Warning: spec asks for at least 14 days of data; got --days {args.days}.")

    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    timestamps = generate_timestamps(start, args.days, args.interval_minutes)

    # Independent, reproducible RNG stream per (room, sensor_type), spawned
    # from one top-level seed in a fixed sorted order -- the mapping from
    # seed to per-sensor data never depends on dict/iteration order.
    sensor_keys = sorted((room, kind) for room in DORM_ROOMS for kind in ("temperature", "occupancy"))
    child_seeds = np.random.SeedSequence(args.seed).spawn(len(sensor_keys))
    rngs = {key: np.random.default_rng(seed) for key, seed in zip(sensor_keys, child_seeds)}

    rows = []
    for room in DORM_ROOMS:
        temp_values = _temp_series(room, timestamps, rngs[(room, "temperature")])
        occ_values = _occupancy_series(room, timestamps, rngs[(room, "occupancy")])
        temp_label = temp_sensor_label(room)
        occ_label = occupancy_sensor_label(room)
        for ts, temp_v, occ_v in zip(timestamps, temp_values, occ_values):
            rows.append((ts, temp_label, room, "temperature", float(temp_v)))
            rows.append((ts, occ_label, room, "occupancy", float(occ_v)))

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sensor_readings;")
            with cur.copy(
                "COPY sensor_readings (ts, sensor_label, room_number, sensor_type, value) FROM STDIN"
            ) as copy:
                for row in rows:
                    copy.write_row(row)
        conn.commit()
        print(
            f"Inserted {len(rows)} readings ({len(timestamps)} timestamps x {len(DORM_ROOMS)} rooms x 2 sensors) "
            f"from {start.isoformat()} over {args.days} days (seed={args.seed})."
        )

        # refresh_continuous_aggregate() must run outside a transaction block.
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("CALL refresh_continuous_aggregate('hourly_temp_by_room', NULL, NULL);")
        print("Refreshed hourly_temp_by_room continuous aggregate.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
