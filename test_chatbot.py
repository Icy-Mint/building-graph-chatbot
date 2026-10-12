"""Unit tests for chatbot.py's ask() dispatch logic, plus require_env_vars.

These run entirely offline: chains/helpers are fakes with scripted return
values, so no live Neo4j instance and no OpenAI key are needed.
"""
import pandas as pd
import pytest

from chatbot import ask, require_env_vars, parser, SensorHelper


class FakeChain:
    """Stands in for LLMChain. `responses` is a list of strings returned in
    order, one per call to .run()."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class FakeGraphHelper:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, cypher):
        self.calls.append(cypher)
        return self.result


def make_sensor_helper():
    """A SensorHelper with in-memory tables instead of reading CSVs.

    Each room's all-time peak/low deliberately differs from its latest
    ("current") reading, so tests can tell apart the current_* actions
    (latest row) from the alltime_* actions (historical max/min) -- the
    exact distinction a past bug collapsed into one meaning.
    """
    sh = SensorHelper.__new__(SensorHelper)  # bypass __init__'s disk read
    sh.tables = {
        "101": pd.DataFrame({
            "timestamp": pd.to_datetime(["2024-01-01 00:00", "2024-01-01 03:00", "2024-01-01 06:00"]),
            "temperature": [20.0, 30.0, 25.0],  # all-time peak 30.0 @ 03:00, latest 25.0 @ 06:00
            "occupancy": [0, 1, 0],  # latest (06:00) is vacant
        }),
        "102": pd.DataFrame({
            "timestamp": pd.to_datetime(["2024-01-01 00:00", "2024-01-01 03:00", "2024-01-01 06:00"]),
            "temperature": [18.0, 19.0, 17.0],  # all-time peak 19.0 @ 03:00, latest 17.0 @ 06:00
            "occupancy": [1, 1, 1],  # latest (06:00) is occupied
        }),
    }
    return sh


def classify(action, room):
    """A chain response shaped like the real LLM's structured output."""
    room_json = "null" if room is None else repr(room) if isinstance(room, int) else f'"{room}"'
    return f'```json\n{{"action": "{action}", "room": {room_json}}}\n```'


def test_alltime_hottest_with_known_room_returns_that_room_only():
    chain = FakeChain([classify("alltime_hottest", "101")])
    sensor_helper = make_sensor_helper()

    answer, action, room = ask(
        "what's the highest temperature room 101 has ever recorded?",
        chain, FakeChain([]), parser, FakeGraphHelper([]), sensor_helper
    )

    assert "Room 101" in answer and "102" not in answer
    assert action == "alltime_hottest"
    assert room == "101"


def test_room_returned_as_int_is_normalized_to_string():
    """Regression test: the LLM can return room as a bare JSON number (not a
    quoted string). sh.tables is keyed by strings, so without normalizing,
    `room in sensor_helper.tables` would silently miss and fall through to
    "all rooms" instead of answering about the specific room asked."""
    chain = FakeChain([classify("alltime_hottest", 101)])
    sensor_helper = make_sensor_helper()

    answer, action, room = ask(
        "highest ever in room 101?", chain, FakeChain([]), parser, FakeGraphHelper([]), sensor_helper
    )

    assert room == "101"
    assert "Room 101" in answer and "Room 102" not in answer


def test_no_room_returns_all_rooms():
    chain = FakeChain([classify("alltime_coldest", None)])
    sensor_helper = make_sensor_helper()

    answer, action, room = ask(
        "what's the lowest temperature ever recorded in any room?",
        chain, FakeChain([]), parser, FakeGraphHelper([]), sensor_helper
    )

    assert "Room 101" in answer and "Room 102" in answer
    assert room is None


# ───────────────────────────────────────────────────────────────────────
# current_* actions vs alltime_* actions: the fixture's rooms have an
# all-time peak that differs from their latest reading specifically so
# these can't pass by accident.
# ───────────────────────────────────────────────────────────────────────

def test_current_temp_reports_latest_reading_not_alltime_peak():
    chain = FakeChain([classify("current_temp", "101")])
    sensor_helper = make_sensor_helper()

    answer, action, room = ask(
        "what's the current temperature in room 101?",
        chain, FakeChain([]), parser, FakeGraphHelper([]), sensor_helper
    )

    assert "25.0" in answer  # latest reading, not the 30.0 all-time peak
    assert "30.0" not in answer
    assert action == "current_temp"


def test_alltime_hottest_reports_historical_peak_not_latest():
    chain = FakeChain([classify("alltime_hottest", "101")])
    sensor_helper = make_sensor_helper()

    answer, action, room = ask(
        "what's the highest temperature room 101 has ever hit?",
        chain, FakeChain([]), parser, FakeGraphHelper([]), sensor_helper
    )

    assert "30.0" in answer  # the all-time peak, not the 25.0 latest reading


def test_current_hottest_compares_latest_readings_across_rooms():
    # latest readings: 101 -> 25.0, 102 -> 17.0; 101 should win despite 102
    # having a higher all-time peak (19.0) than 101's latest (25.0 > 19.0 anyway,
    # but this also confirms it's not accidentally using all-time 101-peak 30.0)
    chain = FakeChain([classify("current_hottest", None)])
    sensor_helper = make_sensor_helper()

    answer, action, room = ask(
        "which room is hottest right now?", chain, FakeChain([]), parser, FakeGraphHelper([]), sensor_helper
    )

    assert "Room 101" in answer
    assert "25.0" in answer
    assert action == "current_hottest"


def test_current_coldest_compares_latest_readings_across_rooms():
    # latest readings: 101 -> 25.0, 102 -> 17.0; 102 is colder right now
    chain = FakeChain([classify("current_coldest", None)])
    sensor_helper = make_sensor_helper()

    answer, action, room = ask(
        "which room is coldest right now?", chain, FakeChain([]), parser, FakeGraphHelper([]), sensor_helper
    )

    assert "Room 102" in answer
    assert "17.0" in answer


def test_current_occupancy_reports_latest_status():
    chain = FakeChain([classify("current_occupancy", "101")])
    sensor_helper = make_sensor_helper()

    answer, action, room = ask(
        "is room 101 occupied right now?", chain, FakeChain([]), parser, FakeGraphHelper([]), sensor_helper
    )

    assert "vacant" in answer.lower()  # latest reading for 101 is occupancy=0
    assert action == "current_occupancy"


def test_occupancy_pattern_still_reports_historical_hours():
    chain = FakeChain([classify("occupancy_pattern", "102")])
    sensor_helper = make_sensor_helper()

    answer, action, room = ask(
        "when is room 102 typically occupied?",
        chain, FakeChain([]), parser, FakeGraphHelper([]), sensor_helper
    )

    assert "occupied during" in answer
    assert action == "occupancy_pattern"


def make_forecast_sensor_helper():
    """Occupancy=1 at every hour of day for room 101, occupancy=0 at every
    hour for room 102 -- deterministic regardless of the real wall-clock
    hour when the test runs, since occupancy_forecast() looks at
    (real_now + 1 hour)."""
    hours = pd.date_range("2024-01-01 00:00", periods=24, freq="h")
    sh = SensorHelper.__new__(SensorHelper)
    sh.tables = {
        "101": pd.DataFrame({"timestamp": hours, "temperature": [21.0] * 24, "occupancy": [1] * 24}),
        "102": pd.DataFrame({"timestamp": hours, "temperature": [21.0] * 24, "occupancy": [0] * 24}),
    }
    return sh


def test_occupancy_forecast_specific_room_reports_probability():
    chain = FakeChain([classify("occupancy_forecast", "101")])
    sensor_helper = make_forecast_sensor_helper()

    answer, action, room = ask(
        "will room 101 be occupied in the next hour?",
        chain, FakeChain([]), parser, FakeGraphHelper([]), sensor_helper
    )

    assert action == "occupancy_forecast"
    assert "100%" in answer


def test_occupancy_forecast_no_room_lists_likely_occupied_rooms():
    chain = FakeChain([classify("occupancy_forecast", None)])
    sensor_helper = make_forecast_sensor_helper()

    answer, action, room = ask(
        "what's the occupancy forecast for the next hour?",
        chain, FakeChain([]), parser, FakeGraphHelper([]), sensor_helper
    )

    assert "101" in answer
    assert "102" not in answer  # room 102 is always-vacant, below the 50% threshold


def test_ac_mapping_formats_rows():
    chain = FakeChain([classify("ac_mapping", None)])
    graph_helper = FakeGraphHelper([
        {"ac_unit": "AC1", "rooms": ["101", "102", "103"]},
        {"ac_unit": "AC2", "rooms": ["104", "105", "106"]},
    ])

    answer, action, room = ask(
        "which AC unit serves which rooms?", chain, FakeChain([]), parser, graph_helper, make_sensor_helper()
    )

    assert "AC1" in answer and "101" in answer
    assert "AC2" in answer and "106" in answer
    assert action == "ac_mapping"


def test_ac_mapping_with_no_rows_gives_a_clear_message():
    chain = FakeChain([classify("ac_mapping", None)])
    graph_helper = FakeGraphHelper([])

    answer, action, room = ask(
        "ac mapping?", chain, FakeChain([]), parser, graph_helper, make_sensor_helper()
    )

    assert "couldn" in answer.lower()


def test_ac_mapping_propagates_cypher_error_string():
    chain = FakeChain([classify("ac_mapping", None)])
    graph_helper = FakeGraphHelper("⚠️ Cypher error : boom")

    answer, action, room = ask(
        "ac mapping?", chain, FakeChain([]), parser, graph_helper, make_sensor_helper()
    )

    assert answer.startswith("⚠️")


def test_fallback_action_uses_fallback_chain():
    chain = FakeChain([classify("fallback", None)])
    fallback_chain = FakeChain(["Here's a general answer."])

    answer, action, room = ask(
        "what's the building's address?", chain, fallback_chain, parser, FakeGraphHelper([]), make_sensor_helper()
    )

    assert answer == "Here's a general answer."
    assert action == "fallback"
    assert len(fallback_chain.calls) == 1


def test_malformed_classification_output_returns_error_without_crashing():
    chain = FakeChain(["not valid json at all"])

    answer, action, room = ask(
        "???", chain, FakeChain([]), parser, FakeGraphHelper([]), make_sensor_helper()
    )

    assert answer.startswith("❌")
    assert action is None
    assert room is None


# ───────────────────────────────────────────────────────────────────────
# require_env_vars
# ───────────────────────────────────────────────────────────────────────

def test_require_env_vars_passes_when_all_set(monkeypatch):
    monkeypatch.setenv("NEO4J_URI", "bolt://x")
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "pw")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    import chatbot
    monkeypatch.setattr(chatbot, "LLM_PROVIDER", "openai")

    require_env_vars()  # should not raise


def test_require_env_vars_lists_every_missing_var(monkeypatch):
    for name in ["NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "OPENAI_API_KEY"]:
        monkeypatch.delenv(name, raising=False)
    import chatbot
    monkeypatch.setattr(chatbot, "LLM_PROVIDER", "openai")

    with pytest.raises(RuntimeError) as exc_info:
        require_env_vars()

    message = str(exc_info.value)
    for name in ["NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "OPENAI_API_KEY"]:
        assert name in message


def test_require_env_vars_checks_the_active_providers_key(monkeypatch):
    monkeypatch.setenv("NEO4J_URI", "bolt://x")
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "pw")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    import chatbot
    monkeypatch.setattr(chatbot, "LLM_PROVIDER", "gemini")

    with pytest.raises(RuntimeError) as exc_info:
        require_env_vars()

    assert "GOOGLE_API_KEY" in str(exc_info.value)
