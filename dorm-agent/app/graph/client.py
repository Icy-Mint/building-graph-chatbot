"""Neo4j driver construction, shared by seed.py and validate.py (and later,
the tool layer)."""
import os

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()


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
