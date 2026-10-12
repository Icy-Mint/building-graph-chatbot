"""Neo4j driver construction, shared by seed.py, validate.py, and the tool
layer (app/tools/graph_tools.py)."""
import os
import re

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

_WRITE_CLAUSE_RE = re.compile(r"\b(CREATE|MERGE|DELETE|SET|REMOVE|DROP)\b", re.I)


def get_driver():
    uri = os.getenv("NEO4J_URI")
    username = os.getenv("NEO4J_USERNAME")
    password = os.getenv("NEO4J_PASSWORD")
    if not all([uri, username, password]):
        raise RuntimeError(
            "Missing NEO4J_URI/NEO4J_USERNAME/NEO4J_PASSWORD. "
            "Copy .env.example to .env and fill them in."
        )
    return GraphDatabase.driver(uri, auth=(username, password))


def ensure_read_only_cypher(query: str) -> None:
    if _WRITE_CLAUSE_RE.search(query):
        raise PermissionError(f"Refusing to execute a non-read-only Cypher statement: {query!r}")


def run_read_only(session, query: str, **params):
    """All tool-layer Cypher goes through this, not session.run() directly.

    Neo4j Community edition (what docker-compose.yml runs) has no per-role
    access control at all -- that's an Enterprise-only feature -- so this is
    an application-level substitute, not a DB-level guarantee. It's lower
    risk here than it was for the sibling chatbot_nl2cypher.py project: every
    query the tool layer runs is hand-written and fixed (see
    app/tools/graph_tools.py), never LLM-generated, so this is defense in
    depth against a future bug, not the primary line of defense against
    prompt injection.
    """
    ensure_read_only_cypher(query)
    return session.run(query, **params)
