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
    """A SensorHelper with in-memory tables instead of reading CSVs."""
    sh = SensorHelper.__new__(SensorHelper)  # bypass __init__'s disk read
    sh.tables = {
        "101": pd.DataFrame({
            "timestamp": pd.to_datetime(["2024-01-01 00:00", "2024-01-01 06:00"]),
            "temperature": [20.0, 25.0],
            "occupancy": [0, 1],
        }),
        "102": pd.DataFrame({
            "timestamp": pd.to_datetime(["2024-01-01 00:00", "2024-01-01 06:00"]),
            "temperature": [18.0, 19.0],
            "occupancy": [1, 1],
        }),
    }
    return sh


def classify(action, room):
    """A chain response shaped like the real LLM's structured output."""
    room_json = "null" if room is None else repr(room) if isinstance(room, int) else f'"{room}"'
    return f'```json\n{{"action": "{action}", "room": {room_json}}}\n```'


def test_hottest_with_known_room_returns_that_room_only():
    chain = FakeChain([classify("hottest", "101")])
    sensor_helper = make_sensor_helper()

    answer, action, room = ask(
        "hottest room 101?", chain, FakeChain([]), parser, FakeGraphHelper([]), sensor_helper
    )

    assert "Room 101" in answer and "102" not in answer
    assert action == "hottest"
    assert room == "101"


def test_room_returned_as_int_is_normalized_to_string():
    """Regression test: the LLM can return room as a bare JSON number (not a
    quoted string). sh.tables is keyed by strings, so without normalizing,
    `room in sensor_helper.tables` would silently miss and fall through to
    "all rooms" instead of answering about the specific room asked."""
    chain = FakeChain([classify("hottest", 101)])
    sensor_helper = make_sensor_helper()

    answer, action, room = ask(
        "hottest room 101?", chain, FakeChain([]), parser, FakeGraphHelper([]), sensor_helper
    )

    assert room == "101"
    assert "Room 101" in answer and "Room 102" not in answer


def test_no_room_returns_all_rooms():
    chain = FakeChain([classify("coldest", None)])
    sensor_helper = make_sensor_helper()

    answer, action, room = ask(
        "which room is coldest?", chain, FakeChain([]), parser, FakeGraphHelper([]), sensor_helper
    )

    assert "Room 101" in answer and "Room 102" in answer
    assert room is None


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
