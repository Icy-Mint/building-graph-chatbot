"""Postgres/TimescaleDB connection construction, shared by schema.py,
generate.py, and plot.py (and later, the tool layer)."""
import os

import psycopg
from dotenv import load_dotenv

load_dotenv()


def get_connection():
    """Admin credentials -- schema.py, generate.py, readonly_role.py."""
    required = ["POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB", "POSTGRES_HOST", "POSTGRES_PORT"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(
            "Missing " + ", ".join(missing) + ". Copy .env.example to .env and fill them in."
        )
    return psycopg.connect(
        host=os.getenv("POSTGRES_HOST"),
        port=os.getenv("POSTGRES_PORT"),
        user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
        dbname=os.getenv("POSTGRES_DB"),
    )


def get_readonly_connection():
    """Read-only role credentials -- the tool layer (app/tools/timeseries_tools.py)
    uses this, never get_connection(). Unlike Neo4j Community (see
    app/graph/client.py), this is a genuine DB-enforced read-only role, not
    an application-level guard -- run `python -m app.timeseries.readonly_role`
    once to create it."""
    required = ["POSTGRES_READONLY_USER", "POSTGRES_READONLY_PASSWORD", "POSTGRES_DB", "POSTGRES_HOST", "POSTGRES_PORT"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(
            "Missing " + ", ".join(missing) + ". Copy .env.example to .env and fill them in, "
            "then run `python -m app.timeseries.readonly_role`."
        )
    return psycopg.connect(
        host=os.getenv("POSTGRES_HOST"),
        port=os.getenv("POSTGRES_PORT"),
        user=os.getenv("POSTGRES_READONLY_USER"),
        password=os.getenv("POSTGRES_READONLY_PASSWORD"),
        dbname=os.getenv("POSTGRES_DB"),
    )
