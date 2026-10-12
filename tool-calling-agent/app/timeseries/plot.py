"""Sanity-check plot: saves a PNG of a few days of two sensors' readings so
you can eyeball that the generated data looks like what the model intended
(sinusoidal temperature, step-like occupancy) before trusting it further.

Run:
    python -m app.timeseries.plot
"""
import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from app.timeseries.client import get_connection


def fetch_readings(conn, sensor_label: str, days: int):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ts, value FROM sensor_readings WHERE sensor_label = %s ORDER BY ts LIMIT %s",
            (sensor_label, days * 24 * 60 // 5),
        )
        rows = cur.fetchall()
    return [r[0] for r in rows], [r[1] for r in rows]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--temp-sensor", default="TEMP_101")
    parser.add_argument("--occ-sensor", default="OCC_102")
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--out", default="sanity_check.png")
    args = parser.parse_args()

    conn = get_connection()
    try:
        temp_ts, temp_vals = fetch_readings(conn, args.temp_sensor, args.days)
        occ_ts, occ_vals = fetch_readings(conn, args.occ_sensor, args.days)
    finally:
        conn.close()

    if not temp_ts or not occ_ts:
        raise SystemExit(
            f"No data found for {args.temp_sensor} or {args.occ_sensor}. "
            "Run `python -m app.timeseries.generate` first."
        )

    fig, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    axes[0].plot(temp_ts, temp_vals, color="tab:red")
    axes[0].set_ylabel("Temperature (C)")
    axes[0].set_title(f"{args.temp_sensor} -- {args.days} days")

    axes[1].step(occ_ts, occ_vals, where="post", color="tab:blue")
    axes[1].set_ylabel("Occupied (0/1)")
    axes[1].set_title(f"{args.occ_sensor} -- {args.days} days")
    axes[1].set_xlabel("Time")

    fig.tight_layout()
    out_path = Path(__file__).resolve().parent.parent.parent / args.out
    fig.savefig(out_path, dpi=120)
    print(f"Saved {out_path}")


if __name__ == "__main__":
    main()
