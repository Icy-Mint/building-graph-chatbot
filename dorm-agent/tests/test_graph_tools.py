"""Live tests for the graph tool layer -- these run real Cypher against the
Neo4j container, not mocks, since the tools are thin wrappers over
hand-written queries and the point is confirming the query + validation +
response-shape behavior together, not re-testing the Neo4j driver.

Requires `docker compose up -d neo4j` and `python -m app.graph.seed` to
have been run first (the same data Step 1's validate.py checks)."""
from app.tools.graph_tools import get_ac_for_room, get_room_info, get_rooms_serviced_by_ac


def test_get_rooms_serviced_by_ac_specific():
    result = get_rooms_serviced_by_ac("AC-1")
    assert result["status"] == "ok"
    assert [r["room_number"] for r in result["results"]] == ["101", "102", "103"]


def test_get_rooms_serviced_by_ac_all():
    result = get_rooms_serviced_by_ac(None)
    assert result["status"] == "ok"
    assert len(result["results"]) == 6


def test_get_rooms_serviced_by_ac_unknown():
    result = get_rooms_serviced_by_ac("AC-99")
    assert result["status"] == "error"
    assert result["valid_ac_units"] == ["AC-1", "AC-2"]


def test_get_ac_for_room_dorm():
    result = get_ac_for_room("104")
    assert result == {"status": "ok", "room_number": "104", "ac_label": "AC-2"}


def test_get_ac_for_room_mechanical_is_no_data_not_error():
    result = get_ac_for_room("201")
    assert result["status"] == "no_data"


def test_get_ac_for_room_unknown_is_error_with_valid_rooms_listed():
    result = get_ac_for_room("999")
    assert result["status"] == "error"
    assert "999" not in result["valid_rooms"]
    assert len(result["valid_rooms"]) == 8


def test_get_room_info_dorm_has_both_sensors():
    result = get_room_info("101")
    assert result["status"] == "ok"
    assert result["type"] == "dorm"
    assert sorted(s["type"] for s in result["sensors"]) == ["occupancy", "temperature"]
    assert result["serviced_by_ac"] == "AC-1"
    assert result["houses_ac"] is None


def test_get_room_info_mechanical_has_no_sensors_but_still_ok():
    """The key distinction this protects: a mechanical room is a valid
    answer here (status=ok, sensors=[]), not an error or a no_sensors
    short-circuit -- that status is reserved for tools that specifically
    need sensor data, not for "describe this room"."""
    result = get_room_info("201")
    assert result["status"] == "ok"
    assert result["type"] == "mechanical"
    assert result["sensors"] == []
    assert result["houses_ac"] == "AC-1"


def test_get_room_info_unknown_room_is_error():
    result = get_room_info("999")
    assert result["status"] == "error"
