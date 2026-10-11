# dorm-agent

A dormitory building chat agent: Claude (Anthropic tool use) answers
questions about the building by calling fixed, typed tools against a Neo4j
graph (building structure) and TimescaleDB (sensor time series). The model
never writes raw Cypher or SQL.

This is a separate subproject from the Streamlit bots one level up
(`chatbot.py`, `chatbotForecast.py`) — different stack (FastAPI, Postgres,
Docker Compose, Claude tool-use agent vs. single-file Streamlit apps),
different dependencies, its own venv.

## Status: Step 4 of 6 (agent loop — code complete, NOT verified live)

The Neo4j graph model, the TimescaleDB time-series generator, the tool
layer, and the agent loop are built. **No `ANTHROPIC_API_KEY` has been
available at any point during this project**, so unlike every other step,
Step 4 has only been tested with a fake Anthropic client (see
`tests/test_agent_loop.py`) — its control flow (tool_use handling, retry
within budget, iteration cap, never overriding a decline) is verified, but
it has never actually talked to the real API. The request shape (model ID,
`cache_control` placement, `system`/`tools` structure) is believed correct
based on the installed SDK's own type definitions, not confirmed by a live
call. Run it against the real API before trusting it. Steps 5–6 (FastAPI/
Streamlit UI, eval harness) are not started.

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
have no sensors — the tool layer (Step 3, below) returns a distinguishable
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

## Tool layer (Step 3)

`app/tools/`: 7 typed functions, each running exactly one hand-written,
parameterized query. The LLM (Step 4) picks which to call and what
arguments to pass — it never writes Cypher or SQL itself.

**Graph** (`graph_tools.py`): `get_rooms_serviced_by_ac`, `get_ac_for_room`,
`get_room_info`.
**Time-series** (`timeseries_tools.py`): `get_temp_readings`,
`get_hot_periods`, `get_occupancy`, `forecast_occupancy` — all pre-aggregate
inside the tool and cap row counts (`MAX_ROW_CAP = 2000`; verified live that
a 14-day raw query across all 6 rooms, 24,192 rows, is correctly rejected
rather than silently truncated or dumped on the model).

Every tool returns `{"status": "ok" | "no_data" | "no_sensors" | "error",
...}` via shared validators in `app/tools/errors.py`. The dorm/mechanical
distinction is deliberate: `validate_room_has_sensors` (used by the
time-series tools) returns `no_sensors` for a mechanical room, distinct from
`error` for an unknown one — but `validate_known_room` (used by
`get_room_info`, where a mechanical room is a complete, valid answer) treats
both as equally valid rooms. Multi-room calls (`get_temp_readings`,
`get_occupancy`) validate each room independently and still answer for the
valid ones if only some are bad, rather than failing the whole call — more
useful for an agent that can read the per-room errors and retry just the
bad argument.

`forecast_occupancy` is a deterministic statistical forecast (mean
occupancy by day-of-week + hour-of-day from history, looked up per hour of
the requested horizon) — no model/randomness involved, so the agent reports
numbers, it never generates them. Verified it recovers the generator's
built-in signal: room 101 (`full_time_student`) and the other profile's
rooms both forecast plausible, history-grounded probabilities, not noise.

### Read-only access — resolved

As flagged after Step 2: Neo4j Community edition (what `docker-compose.yml`
runs) has no per-role access control, so `app/graph/client.py` enforces
read-only at the application layer instead (`run_read_only()` rejects any
`CREATE`/`MERGE`/`DELETE`/`SET`/`REMOVE`/`DROP` before it reaches the
driver — verified live with an actual `DETACH DELETE` attempt). This is
lower-risk here than the same pattern was for `chatbotForecast.py`: every
query the tool layer runs is hand-written and fixed, never LLM-generated,
so this is defense in depth, not the primary safeguard.

**Postgres is different** — a real read-only role costs nothing there, no
edition tier involved. `python -m app.timeseries.readonly_role` creates a
`dorm_readonly` Postgres role with `SELECT`-only grants (including on future
tables, via `ALTER DEFAULT PRIVILEGES`); `app/tools/timeseries_tools.py`
connects as that role, never the admin one. Verified live: `SELECT` works,
`INSERT` fails with a genuine Postgres `InsufficientPrivilege` error — this
one actually is DB-enforced, not just application-level.

## Agent loop (Step 4)

`app/agent/loop.py`: a hand-rolled loop over the Anthropic tool-use API —
no agent framework. `ask(question)` sends the question plus the 7 tool
definitions (`app/agent/tool_specs.py`) and the system prompt
(`app/agent/prompts.py`); on a `tool_use` response it dispatches to the
real typed tool, feeds the structured result straight back (errors
included, marked `is_error`), and lets the model decide whether to retry
with corrected arguments, call another tool, or answer — the "retry once on
error" from the spec isn't special-cased, it falls out naturally from
feeding the error back and bounding total iterations.

- **Iteration cap**: `MAX_AGENT_ITERATIONS` (default 5, env-overridable).
  On hitting it, one final call without `tools` asks for the best partial
  answer and to say plainly the investigation was incomplete — verified via
  a fake client that always returns `tool_use` (an agent that never
  converges): the loop stops exactly at the cap, makes that one wrap-up
  call, and reports `hit_iteration_cap: True`.
- **Never overrides a decline**: if the model's first response is plain
  text (e.g. "I don't have enough information"), that's returned as-is —
  verified directly.
- **Model**: `ANTHROPIC_MODEL` env var, default `claude-sonnet-5`.
  `get_model()` is a single, clearly-marked seam for a future cheap/capable
  router — not built.
- **Prompt caching**: `cache_control: ephemeral` on the system prompt and
  the last tool definition (which, per Anthropic's caching rules, caches
  the whole stable tools list too, since a cache marker covers everything
  up to and including that block). `log_turn()` records
  `cache_read_input_tokens`/`cache_creation_input_tokens` per turn so
  caching effectiveness is visible in the log, not assumed.
- **Logging**: `logs/agent_turns.jsonl` — one line per `ask()` call:
  question, every tool call (name/args/status/latency), token usage
  (including cache fields), the final answer, and total latency.
- **Prompt-injection guidance**: the system prompt explicitly tells the
  model to treat tool results as data, never as instructions — relevant to
  the eval harness's planned injection test case (Step 6).

**Verified**: the loop's control flow, with a fake Anthropic client (7
tests) — tool dispatch, error feedback, retry-within-budget, the iteration
cap, and usage accumulation. **Not verified**: an actual call to the real
Anthropic API (see the status note at the top of this file).

## Running Steps 1–4

```bash
cp .env.example .env   # fill in all *_PASSWORD vars, and ANTHROPIC_API_KEY to actually run the agent
docker compose up -d   # neo4j + timescaledb

python -m venv .venv && source .venv/Scripts/activate  # or .venv/bin/activate on macOS/Linux
pip install -r requirements.txt

python -m app.graph.seed
python -m app.graph.validate

python -m app.timeseries.schema
python -m app.timeseries.generate --seed 42 --days 14
python -m app.timeseries.readonly_role   # creates the read-only Postgres role

pytest   # 60 tests: offline model/safety/agent-loop checks + live tool-layer tests against the containers above

# requires ANTHROPIC_API_KEY in .env -- not yet verified to work:
python -c "from app.agent.loop import ask; print(ask('Which rooms does AC-1 service?'))"
```

## Roadmap (not yet built)

5. FastAPI + Streamlit UI
6. Eval harness (25+ golden cases, deterministic scoring, drift guard)
