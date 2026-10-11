"""Offline tests for the tool layer's safety nets: the Neo4j write-clause
guard and the shared argument validators. No live DB needed for any of
these -- ensure_read_only_cypher is pure string matching, and the
validators are pure functions over plain arguments."""
from datetime import datetime, timedelta, timezone

import pytest

from app.graph.client import ensure_read_only_cypher
from app.tools.errors import (
    MAX_FORECAST_HORIZON_HOURS,
    validate_agg,
    validate_date_range,
    validate_horizon,
    validate_known_room,
    validate_room_has_sensors,
)


@pytest.mark.parametrize("cypher", [
    "MATCH (r:Room) RETURN r.number",
    "MATCH (a:ACUnit {label: 'AC-1'})-[:SERVICES]->(r:Room) RETURN r.number",
    "MATCH (r:Room)-[:HAS_SENSOR]->(s:Sensor) RETURN s.label ORDER BY s.label LIMIT 10",
])
def test_ensure_read_only_cypher_allows_real_tool_queries(cypher):
    ensure_read_only_cypher(cypher)  # should not raise


@pytest.mark.parametrize("cypher", [
    "MATCH (r:Room {number: '999'}) DETACH DELETE r",
    "MATCH (r:Room) DELETE r",
    "CREATE (r:Room {number: '999'})",
    "MATCH (r:Room {number: '101'}) SET r.type = 'mechanical'",
    "MERGE (r:Room {number: '999'})",
    "DROP CONSTRAINT room_number_unique",
])
def test_ensure_read_only_cypher_blocks_write_clauses(cypher):
    with pytest.raises(PermissionError):
        ensure_read_only_cypher(cypher)


def test_validate_known_room_accepts_dorm_and_mechanical():
    assert validate_known_room("101") is None
    assert validate_known_room("201") is None


def test_validate_known_room_rejects_unknown_with_valid_list():
    err = validate_known_room("999")
    assert err["status"] == "error"
    assert "999" not in err["valid_rooms"]


def test_validate_room_has_sensors_distinguishes_unknown_from_mechanical():
    assert validate_room_has_sensors("101") is None
    assert validate_room_has_sensors("201")["status"] == "no_sensors"
    assert validate_room_has_sensors("999")["status"] == "error"


def test_validate_date_range_rejects_start_after_end():
    now = datetime.now(timezone.utc)
    assert validate_date_range(now, now - timedelta(hours=1)) is not None
    assert validate_date_range(now, now + timedelta(hours=1)) is None


def test_validate_horizon_rejects_non_positive_and_too_large():
    assert validate_horizon(0) is not None
    assert validate_horizon(-5) is not None
    assert validate_horizon(MAX_FORECAST_HORIZON_HOURS + 1) is not None
    assert validate_horizon(24) is None


def test_validate_agg_rejects_unlisted_values():
    assert validate_agg("raw", ("raw", "hourly", "daily")) is None
    assert validate_agg("weekly", ("raw", "hourly", "daily")) is not None
