# dorm-agent

A dormitory building chat agent: Claude (Anthropic tool use) answers
questions about the building by calling fixed, typed tools against a Neo4j
graph (building structure) and TimescaleDB (sensor time series). The model
never writes raw Cypher or SQL.

This is a separate subproject from the Streamlit bots one level up
(`chatbot.py`, `chatbotForecast.py`) — different stack (FastAPI, Postgres,
Docker Compose, Claude tool-use agent vs. single-file Streamlit apps),
different dependencies, its own venv.

## Status: Step 1 of 6

Only the Neo4j graph model (seed + validation) is built so far. Steps 2–6
(time-series data, tool layer, agent loop, API/UI, eval harness) are not
started.

## Graph model

```
(:Building)-[:HAS_ROOM]->(:Room {number, type})
(:ACUnit {label})-[:LOCATED_IN]->(:Room {type: "mechanical"})
(:ACUnit)-[:SERVICES]->(:Room {type: "dorm"})          exactly 3 dorms per AC
(:Room)-[:HAS_SENSOR]->(:Sensor {label, type})         type: temperature | occupancy
(:Sensor {type: "temperature"})-[:MONITORS_FOR]->(:ACUnit)
```

8 rooms (101–106 dorm, 201–202 mechanical), 2 AC units, each dorm room has
one temperature and one occupancy sensor. Mechanical rooms intentionally
have no sensors — the tool layer (Step 3) needs to return a distinguishable
`no_sensors` status for that case, not an empty result.

`app/graph/model.py` holds the room/AC assignments as the single source of
truth; both `seed.py` and `validate.py` import from it so they can't drift
out of sync with each other.

## Running Step 1

```bash
cp .env.example .env        # fill in NEO4J_PASSWORD at minimum
docker compose up -d neo4j  # timescaledb isn't needed until Step 2
python -m venv .venv && source .venv/Scripts/activate  # or .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
python -m app.graph.seed      # idempotent -- safe to re-run
python -m app.graph.validate  # asserts the invariants below; exits non-zero on failure
```

`validate.py` checks: exactly 8 rooms (6 dorm / 2 mechanical), exactly 2 AC
units each servicing exactly 3 dorm rooms, every dorm has exactly one
temperature + one occupancy sensor, every mechanical room has none, and
every temperature sensor's `MONITORS_FOR` edge points to the same AC unit
that `SERVICES` its room (graph-path consistency, not just node counts).

## Note: read-only access (relevant from Step 3 onward)

The spec requires a read-only DB role for the tool layer. `docker-compose.yml`
currently runs Neo4j **Community edition**, which has no per-role access
control at all (any authenticated user has full read/write access) — that's
an Enterprise-only feature. When Step 3 gets there, the options are: switch
the compose file to `neo4j:5-enterprise` (free for local/dev under Neo4j's
evaluation license, via `NEO4J_ACCEPT_LICENSE_AGREEMENT=yes`) for a real DB
role, or enforce read-only at the application layer (wrap the driver so any
non-`MATCH`/`RETURN` query is rejected before it executes, the same approach
used in the sibling `chatbotForecast.py` project after its Aura Free tier
turned out not to support custom roles either). Not deciding this now since
it isn't needed until Step 3 — flagging it here so it isn't a surprise.

## Roadmap (not yet built)

2. Time-series generator (TimescaleDB hypertable, continuous aggregate, sanity plot)
3. Tool layer (typed functions, unit-tested, no LLM)
4. Agent loop (Anthropic tool use, logging)
5. FastAPI + Streamlit UI
6. Eval harness (25+ golden cases, deterministic scoring, drift guard)
