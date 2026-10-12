"""Creates (or updates the password of) a genuine read-only Postgres role
for the tool layer -- unlike Neo4j Community, Postgres has no edition split
for this: GRANT/REVOKE-based roles are a standard, free feature. Run with
the admin credentials (POSTGRES_USER/PASSWORD); the tool layer then
connects as POSTGRES_READONLY_USER instead.

Idempotent -- safe to re-run.

Run:
    python -m app.timeseries.readonly_role
"""
import os

from dotenv import load_dotenv
from psycopg import sql

from app.timeseries.client import get_connection

load_dotenv()


def main():
    role = os.getenv("POSTGRES_READONLY_USER")
    password = os.getenv("POSTGRES_READONLY_PASSWORD")
    database = os.getenv("POSTGRES_DB")
    if not all([role, password, database]):
        raise RuntimeError(
            "Missing POSTGRES_READONLY_USER/POSTGRES_READONLY_PASSWORD/POSTGRES_DB. "
            "Copy .env.example to .env and fill them in."
        )

    conn = get_connection()  # admin credentials
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            # Postgres DDL doesn't accept bind parameters for PASSWORD (confirmed
            # live -- "syntax error at or near $1"), so the password is composed
            # client-side via sql.Literal, which still quotes/escapes it safely;
            # it just isn't a server-side bind parameter the way %s is elsewhere.
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
            if cur.fetchone():
                cur.execute(
                    sql.SQL("ALTER ROLE {} WITH PASSWORD {}").format(
                        sql.Identifier(role), sql.Literal(password)
                    )
                )
            else:
                cur.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                        sql.Identifier(role), sql.Literal(password)
                    )
                )

            cur.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(database), sql.Identifier(role)
                )
            )
            cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
            cur.execute(
                sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(sql.Identifier(role))
            )
            # So tables created after this script runs (e.g. a future migration)
            # are also read-only for this role by default, not accidentally open.
            cur.execute(
                sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {}").format(
                    sql.Identifier(role)
                )
            )
        print(f"Read-only role '{role}' ready with SELECT on all current and future tables in 'public'.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
