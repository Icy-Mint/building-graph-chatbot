import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import os

# === Setup ===
dorm_rooms = [f"{i}" for i in range(101, 107)]
start_time = datetime(2024, 1, 1, 0, 0, 0)
end_time = start_time + timedelta(days=7)
time_range = pd.date_range(start=start_time, end=end_time, freq='5min')[:-1]

# === Occupancy Profiles ===
def full_time_student(t):
    return int(
        (t.hour in range(7, 9)) or
        (t.hour in range(13, 15)) or
        (t.hour in range(20, 22)) or
        (t.hour >= 23 or t.hour < 6)
    )

def night_worker(t):
    return int(
        (t.hour in range(6, 8)) or
        (t.hour in range(16, 18)) or
        (t.hour in range(21, 23)) or
        (t.hour >= 0 and t.hour < 5)
    )

# === Temperature Profile ===
def generate_temperature_series(is_sunny_side):
    base_temp = 22
    temp_amplitude = 4
    sunny_boost = 2 if is_sunny_side else 0
    return [round(
        base_temp + sunny_boost + 
        temp_amplitude * np.sin((2 * np.pi / 1440) * (i % 1440)) +
        np.random.normal(0, 0.5), 2
    ) for i in range(len(time_range))]

# === Output Directory ===
output_dir = "sensor_outputs"
os.makedirs(output_dir, exist_ok=True)

# === Generate + Save CSV per Room ===
for i, room in enumerate(dorm_rooms):
    profile_func = full_time_student if i % 2 == 0 else night_worker
    is_sunny = i < 3  # Rooms 101–103 are sunny side
    temperature_series = generate_temperature_series(is_sunny)
    
    data = []
    for j, ts in enumerate(time_range):
        data.append({
            "timestamp": ts,
            "room_number": room,
            "sensor_id_occ": f"OCC_{room}",
            "sensor_id_temp": f"TEMP_{room}",
            "occupancy": profile_func(ts),
            "temperature": temperature_series[j]
        })

    df_room = pd.DataFrame(data)
    df_room.to_csv(f"{output_dir}/room_{room}_timeseries.csv", index=False)

print(f" Done! Files saved in: ./{output_dir}/")
