"""Offline tests for the agent's tool dispatch/argument-adapter layer
(app/agent/tool_specs.py) -- monkeypatches the dispatch table so no live
DB is touched; this is testing the adapter logic (ISO date parsing, unknown
tool names, bad argument shapes), not the tools themselves (see
test_graph_tools.py / test_timeseries_tools.py for those, live)."""
from datetime import datetime, timezone

import app.agent.tool_specs as tool_specs
from app.agent.tool_specs import call_tool, parse_datetime_arg


def test_parse_datetime_arg_handles_trailing_z_as_utc():
    result = parse_datetime_arg("2024-01-01T00:00:00Z")
    assert result == datetime(2024, 1, 1, tzinfo=timezone.utc)


def test_parse_datetime_arg_handles_explicit_offset():
    result = parse_datetime_arg("2024-01-01T00:00:00+00:00")
    assert result == datetime(2024, 1, 1, tzinfo=timezone.utc)


def test_call_tool_rejects_unknown_tool_name():
    result = call_tool("not_a_real_tool", {})
    assert result["status"] == "error"
    assert "not_a_real_tool" in result["reason"]


def test_call_tool_converts_iso_date_strings_before_dispatching(monkeypatch):
    captured = {}

    def fake_get_temp_readings(**kwargs):
        captured.update(kwargs)
        return {"status": "ok", "readings": []}

    monkeypatch.setitem(tool_specs._DISPATCH, "get_temp_readings", fake_get_temp_readings)

    call_tool("get_temp_readings", {
        "room_numbers": ["101"],
        "start": "2024-01-01T00:00:00Z",
        "end": "2024-01-02T00:00:00Z",
        "agg": "hourly",
    })

    assert captured["start"] == datetime(2024, 1, 1, tzinfo=timezone.utc)
    assert captured["end"] == datetime(2024, 1, 2, tzinfo=timezone.utc)
    assert captured["room_numbers"] == ["101"]


def test_call_tool_leaves_non_datetime_tools_args_untouched(monkeypatch):
    captured = {}

    def fake_get_ac_for_room(**kwargs):
        captured.update(kwargs)
        return {"status": "ok"}

    monkeypatch.setitem(tool_specs._DISPATCH, "get_ac_for_room", fake_get_ac_for_room)

    call_tool("get_ac_for_room", {"room_number": "101"})

    assert captured == {"room_number": "101"}


def test_call_tool_returns_structured_error_on_unparseable_date(monkeypatch):
    monkeypatch.setitem(tool_specs._DISPATCH, "get_temp_readings", lambda **kw: {"status": "ok"})

    result = call_tool("get_temp_readings", {
        "room_numbers": ["101"], "start": "not-a-date", "end": "2024-01-02T00:00:00Z",
    })

    assert result["status"] == "error"
    assert "not-a-date" in result["reason"]


def test_call_tool_returns_structured_error_on_bad_argument_shape(monkeypatch):
    def fake_get_ac_for_room(room_number):
        return {"status": "ok"}

    monkeypatch.setitem(tool_specs._DISPATCH, "get_ac_for_room", fake_get_ac_for_room)

    # Model passes an argument name the function doesn't accept.
    result = call_tool("get_ac_for_room", {"room": "101"})

    assert result["status"] == "error"


def test_every_tool_spec_name_has_a_dispatch_entry():
    tool_names = {t["name"] for t in tool_specs.TOOLS}
    assert tool_names == set(tool_specs._DISPATCH.keys())
