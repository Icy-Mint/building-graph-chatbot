"""Offline tests for the time-series generation math (no TimescaleDB
needed) -- determinism, value sanity, and the spec's cross-cutting
requirement encoded as an actual assertion instead of just a comment."""
from datetime import datetime, timezone

import numpy as np

from app.model import AC_ASSIGNMENTS, DORM_ROOMS, room_temp_profile
from app.timeseries.generate import _occupancy_series, _temp_series, generate_timestamps


def test_generate_timestamps_count_matches_days_and_interval():
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    timestamps = generate_timestamps(start, days=14, interval_minutes=5)
    assert len(timestamps) == 14 * 24 * 60 // 5  # 4032
    assert timestamps[0] == start
    assert (timestamps[1] - timestamps[0]).total_seconds() == 5 * 60


def test_temp_series_is_deterministic_given_same_seed():
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    timestamps = generate_timestamps(start, days=2, interval_minutes=5)

    series_a = _temp_series("101", timestamps, np.random.default_rng(42))
    series_b = _temp_series("101", timestamps, np.random.default_rng(42))
    series_c = _temp_series("101", timestamps, np.random.default_rng(99))

    assert np.array_equal(series_a, series_b)
    assert not np.array_equal(series_a, series_c)


def test_occupancy_series_is_binary():
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    timestamps = generate_timestamps(start, days=3, interval_minutes=5)
    values = _occupancy_series("101", timestamps, np.random.default_rng(1))
    assert set(np.unique(values)).issubset({0.0, 1.0})


def test_sun_rooms_run_warmer_on_average_than_shaded_rooms():
    """Regression-style check on the generator itself, not a hardcoded
    assumption: pulls room->profile from the same model.py the generator
    uses, so this breaks loudly if the profile assignment or formula ever
    stops producing the intended sun/shaded temperature gap."""
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    timestamps = generate_timestamps(start, days=14, interval_minutes=5)

    sun_rooms = [r for r in DORM_ROOMS if room_temp_profile(r) == "sun"]
    shaded_rooms = [r for r in DORM_ROOMS if room_temp_profile(r) == "shaded"]
    assert sun_rooms and shaded_rooms

    sun_mean = np.mean([
        _temp_series(r, timestamps, np.random.default_rng(42)).mean() for r in sun_rooms
    ])
    shaded_mean = np.mean([
        _temp_series(r, timestamps, np.random.default_rng(42)).mean() for r in shaded_rooms
    ])
    assert sun_mean > shaded_mean + 1.0  # comfortably more than noise could explain


def test_each_ac_unit_has_rooms_from_both_temp_profiles():
    """Encodes the spec's explicit requirement that temperature-profile
    assignment cuts across AC groupings (e.g. 'AC1 services 2 sun rooms
    and 1 shaded room') as a real test, not just a comment in model.py."""
    for ac_label, info in AC_ASSIGNMENTS.items():
        profiles = {room_temp_profile(r) for r in info["dorms"]}
        assert len(profiles) > 1, f"{ac_label}'s rooms are all one temp profile: {profiles}"
