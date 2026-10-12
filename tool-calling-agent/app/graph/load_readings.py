"""Loads sensor_readings from TimescaleDB into Neo4j as Reading nodes,
duplicating what's already in TimescaleDB so chatbot_nl2cypher.py's existing
NL-to-Cypher design (which expects sensor data to live in Neo4j as graph
nodes) works unmodified against this database. TimescaleDB remains the
source of truth -- this script only ever reads from it via the read-only
role, never writes back.

Idempotent: clears previously-loaded Reading nodes before inserting, so
reruns never duplicate.

Run:
    python -m app.graph.load_readings
"""
from app.graph.client import get_driver
from app.timeseries.client import get_readonly_connection

BATCH_SIZE = 2000

_LOAD_BATCH_QUERY = (
    "UNWIND $rows AS row "
    "MATCH (s:Sensor {sensor_id: row.sensor_id}) "
    "CREATE (rd:Reading {"
    "  timestamp: row.timestamp, value: row.value, "
    "  sensor_type: row.sensor_type, room_number: row.room_number"
    "}) "
    "CREATE (s)-[:RECORDED]->(rd)"
)


def fetch_readings():
    """Streams every row from TimescaleDB, read-only."""
    conn = get_readonly_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ts, sensor_label, room_number, sensor_type, value "
                "FROM sensor_readings ORDER BY ts"
            )
            cols = [d.name for d in cur.description]
            for row in cur:
                yield dict(zip(cols, row))
    finally:
        conn.close()


def load(session, rows) -> int:
    session.run("MATCH (rd:Reading) DETACH DELETE rd")  # clear any previous load first

    batch = []
    total = 0
    for row in rows:
        batch.append({
            "sensor_id": row["sensor_label"],
            "timestamp": row["ts"],
            "value": row["value"],
            "sensor_type": row["sensor_type"],
            "room_number": row["room_number"],
        })
        if len(batch) >= BATCH_SIZE:
            session.run(_LOAD_BATCH_QUERY, rows=batch)
            total += len(batch)
            batch = []
    if batch:
        session.run(_LOAD_BATCH_QUERY, rows=batch)
        total += len(batch)
    return total


def main():
    driver = get_driver()
    try:
        with driver.session() as session:
            count = load(session, fetch_readings())
        print(f"Loaded {count} Reading nodes into Neo4j from TimescaleDB.")
    finally:
        driver.close()


if __name__ == "__main__":
    main()
