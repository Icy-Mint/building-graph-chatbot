# dorm-agent

A dormitory building chat agent: Claude (Anthropic tool use) answers
questions about the building by calling fixed, typed tools against a Neo4j
graph (building structure) and TimescaleDB (sensor time series). The model
never writes raw Cypher or SQL.

This is a separate subproject from the Streamlit bots one level up
(`chatbot.py`, `chatbotForecast.py`) — different stack (FastAPI, Postgres,
Docker Compose, Claude tool-use agent vs. single-file Streamlit apps),
different dependencies, its own venv.

## Status: Step 2 of 6

The Neo4j graph model and the TimescaleDB time-series generator are built.
Steps 3–6 (tool layer, agent loop, API/UI, eval harness) are not started.

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

`app/model.py` holds the room/AC assignments (and, as of Step 2, the
temperature/occupancy profile assignments) as the single source of truth —
`seed.py`, `validate.py`, and the Step 2 generator all import from it so
none of them can drift out of sync with each other.

## Time-series model (Step 2)

`sensor_readings` is a TimescaleDB hypertable: `(ts, sensor_label,
room_number, sensor_type, value)`, 5-minute intervals, 14 days by default.
`hourly_temp_by_room` is a continuous aggregate (hourly avg temperature per
room) built on top of it.

**Temperature**: `base + amplitude * sin(2*pi*(hour - peak_shift)/24) +
gaussian noise`, per room, peaking in the afternoon. Two profiles —
`sun` (base 23°C, warmer) and `shaded` (base 20°C, cooler) — assigned to
rooms *across* the AC groupings, not aligned with them: AC-1 (101/102/103)
ends up with 2 sun rooms + 1 shaded; AC-2 (104/105/106) with 1 sun + 2
shaded. That's deliberate — it's what makes "do AC-1's rooms run hotter"
a real question instead of a tautology.

**Occupancy**: a Bernoulli draw per 5-minute sample from an hourly
probability table, not a hard on/off window — two profiles
(`full_time_student`, `night_shift_student`) with distinct weekday *and*
weekend hour-of-day patterns, so there's real day-of-week signal for
`forecast_occupancy` (Step 3) to recover from history later.

## Running Steps 1–2

```bash
cp .env.example .env   # fill in NEO4J_PASSWORD and POSTGRES_PASSWORD at minimum
docker compose up -d   # both neo4j and timescaledb
python -m venv .venv && source .venv/Scripts/activate  # or .venv/bin/activate on macOS/Linux
pip install -r requirements.txt

python -m app.graph.seed          # idempotent -- safe to re-run
python -m app.graph.validate      # exits non-zero if any invariant fails

python -m app.timeseries.schema   # creates the hypertable + continuous aggregate
python -m app.timeseries.generate --seed 42 --days 14   # clears + regenerates every run
python -m app.timeseries.plot     # sanity_check.png: TEMP_101 + OCC_102 over 3 days
```

`validate.py` checks: exactly 8 rooms (6 dorm / 2 mechanical), exactly 2 AC
units each servicing exactly 3 dorm rooms, every dorm has exactly one
temperature + one occupancy sensor, every mechanical room has none, and
every temperature sensor's `MONITORS_FOR` edge points to the same AC unit
that `SERVICES` its room (graph-path consistency, not just node counts).

`generate.py` is deterministic given `--seed` (verified: same seed → byte-
identical series; different seed → different series) and clears previous
readings before inserting, so reruns never duplicate/append. Verified live:
48,384 rows generated (14 days × 6 rooms × 2 sensors × 288 samples/day),
sun rooms averaged 23.00°C and shaded rooms 20.00°C (exactly matching the
profile bases, as expected over a full sinusoidal period), full-time-student
rooms averaged ~65% occupancy vs. ~58% for night-shift rooms, and the
continuous aggregate populated exactly 2016 rows (14 days × 24h × 6 rooms).

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

3. Tool layer (typed functions, unit-tested, no LLM)
4. Agent loop (Anthropic tool use, logging)
5. FastAPI + Streamlit UI
6. Eval harness (25+ golden cases, deterministic scoring, drift guard)
