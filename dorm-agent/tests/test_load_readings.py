"""Live test for app/graph/load_readings.py -- confirms the Reading nodes
duplicated into Neo4j actually match what's in TimescaleDB, not just that
the load ran without error.

Requires `python -m app.timeseries.generate` and `python -m app.graph.load_readings`
to have been run first against the default seed/date range."""
from app.graph.client import get_driver


def test_reading_node_count_matches_timescaledb_row_count():
    driver = get_driver()
    try:
        with driver.session() as session:
            result = session.run("MATCH (rd:Reading) RETURN count(rd) AS n").data()
    finally:
        driver.close()
    assert result[0]["n"] == 48384  # 14 days x 6 rooms x 2 sensor types x 288 samples/day


def test_latest_reading_matches_known_generator_ground_truth():
    """Same value independently confirmed three ways earlier in this
    project: the TimescaleDB row, a generator-replay computation, and now
    the Neo4j Reading node -- all three must agree."""
    driver = get_driver()
    try:
        with driver.session() as session:
            result = session.run(
                "MATCH (s:Sensor {sensor_id: 'TEMP_101'})-[:RECORDED]->(rd:Reading) "
                "RETURN rd.value AS value, toString(rd.timestamp) AS ts "
                "ORDER BY rd.timestamp DESC LIMIT 1"
            ).data()
    finally:
        driver.close()
    assert round(result[0]["value"], 2) == 20.67
    assert result[0]["ts"].startswith("2024-01-14T23:55:00")
