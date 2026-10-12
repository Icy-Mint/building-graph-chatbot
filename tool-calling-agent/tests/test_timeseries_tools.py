"""Live tests for the time-series tool layer -- real SQL against
TimescaleDB, run through the genuine read-only role (get_readonly_connection).

Requires `docker compose up -d timescaledb`, `python -m app.timeseries.schema`,
and `python -m app.timeseries.generate` (default --seed 42 --start 2024-01-01)
to have been run first, plus `python -m app.timeseries.readonly_role`."""
from datetime import datetime, timezone

from app.tools.timeseries_tools import forecast_occupancy, get_hot_periods, get_occupancy, get_temp_readings

START = datetime(2024, 1, 1, tzinfo=timezone.utc)
END_2D = datetime(2024, 1, 3, tzinfo=timezone.utc)
END_14D = datetime(2024, 1, 15, tzinfo=timezone.utc)


def test_get_temp_readings_hourly_for_two_days():
    result = get_temp_readings(["101"], START, END_2D, agg="hourly")
    assert result["status"] == "ok"
    assert len(result["readings"]) == 48  # 2 days x 24 hours


def test_get_temp_readings_partial_room_errors_still_returns_valid_rooms():
    """Partial validity is intentional: one bad room number shouldn't fail
    the whole call when other rooms in the same request are fine."""
    result = get_temp_readings(["101", "201", "999"], START, END_2D, agg="daily")
    assert result["status"] == "ok"
    assert len(result["readings"]) == 2  # just room 101's 2 daily buckets
    assert result["room_errors"]["201"]["status"] == "no_sensors"
    assert result["room_errors"]["999"]["status"] == "error"


def test_get_temp_readings_invalid_agg_is_error():
    result = get_temp_readings(["101"], START, END_2D, agg="weekly")
    assert result["status"] == "error"


def test_get_temp_readings_bad_date_range_is_error():
    result = get_temp_readings(["101"], END_2D, START, agg="hourly")
    assert result["status"] == "error"


def test_get_hot_periods_only_lists_sun_rooms_above_27c():
    """sun rooms (101/102/104: base 23 + amplitude 5 = 28 peak) should cross
    27C sometimes; shaded rooms (103/105/106: base 20 + amplitude 3.5 = 23.5
    peak) should never reach it -- ties the tool's output back to the Step 2
    generator's ground truth, not just "the query runs"."""
    result = get_hot_periods(27.0, START, END_14D)
    assert result["status"] == "ok"
    assert set(result["by_room"]) == {"101", "102", "104"}


def test_get_hot_periods_impossible_threshold_is_no_data():
    result = get_hot_periods(100.0, START, END_14D)
    assert result["status"] == "no_data"


def test_get_occupancy_daily_for_full_week():
    result = get_occupancy(["101"], START, datetime(2024, 1, 8, tzinfo=timezone.utc), agg="daily")
    assert result["status"] == "ok"
    assert len(result["readings"]) == 7
    for row in result["readings"]:
        assert 0.0 <= row["occupancy_rate"] <= 1.0


def test_forecast_occupancy_returns_requested_horizon_length():
    result = forecast_occupancy("101", 48)
    assert result["status"] == "ok"
    assert len(result["forecast"]) == 48
    assert result["weeks_of_history"] > 1.0


def test_forecast_occupancy_mechanical_room_is_no_sensors():
    result = forecast_occupancy("201", 24)
    assert result["status"] == "no_sensors"


def test_forecast_occupancy_horizon_too_large_is_error():
    result = forecast_occupancy("101", 24 * 365)
    assert result["status"] == "error"


def test_forecast_occupancy_probabilities_are_plausible():
    """Every forecast point should be a real probability recovered from
    history, not None/NaN -- and both profiles here are occupied most hours
    (lowest weekday probabilities are still 0.10, for the ~8h/day at class),
    so the daily average across a full 24h horizon should land well above 0."""
    result = forecast_occupancy("101", 24)
    probs = [p["probability_occupied"] for p in result["forecast"]]
    assert all(p is not None for p in probs)
    assert all(0.0 <= p <= 1.0 for p in probs)
    assert sum(probs) / len(probs) > 0.3
