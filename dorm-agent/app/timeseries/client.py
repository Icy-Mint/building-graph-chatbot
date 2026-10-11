"""Postgres/TimescaleDB connection construction, shared by schema.py,
generate.py, and plot.py (and later, the tool layer)."""
import os

import psycopg
from dotenv import load_dotenv

load_dotenv()


def get_connection():
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
