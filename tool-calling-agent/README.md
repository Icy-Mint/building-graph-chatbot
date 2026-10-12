# tool-calling-agent

A dormitory building chat agent: Claude (Anthropic tool use) answers
questions about the building by calling fixed, typed tools against a Neo4j
graph (building structure) and TimescaleDB (sensor time series). The model
never writes raw Cypher or SQL.

This is a separate subproject from the Streamlit bots one level up
(`chatbot_classifier.py`, `chatbot_nl2cypher.py`) — different stack (FastAPI, Postgres,
Docker Compose, Claude tool-use agent vs. single-file Streamlit apps),
different dependencies, its own venv.

## Status: Steps 1–4 and 6 done, verified live; Step 5 not started

The Neo4j graph model, the TimescaleDB time-series generator, the tool
layer, the agent loop, and the eval harness are all built and confirmed
against the real Anthropic API (an `ANTHROPIC_API_KEY` became available
after Step 4 was first built against a fake client only — see "Live
verification" below for what that first real run caught). Step 5
(FastAPI/Streamlit UI) is not started -- see "Eval harness" and "Roadmap"
below.

## Graph model

```
(:Building)-[:HAS_ROOM]->(:Room {room_number, type})
(:Room {type: "mechanical"})-[:CONTAINS]->(:AC_Unit {ac_id})
(:AC_Unit)-[:SERVICES]->(:Room {type: "dorm"})              exactly 3 dorms per AC
(:Room)-[:HAS_SENSOR]->(:Sensor {sensor_id, sensor_type})   sensor_type: temperature | occupancy
(:Sensor {sensor_type: "temperature"})-[:REPORTS_TO]->(:AC_Unit)
(:Sensor)-[:RECORDED]->(:Reading {timestamp, value, sensor_type, room_number})
```

(Standardized to match `chatbot_classifier.py`/`chatbot_nl2cypher.py`'s existing Aura
schema -- see "Shared local database" below. `Reading` nodes are a
duplicate of TimescaleDB's `sensor_readings`, loaded by
`app/graph/load_readings.py`; TimescaleDB stays the source of truth.)

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
rooms *across* the AC groupings, not aligned with them: AC1 (101/102/103)
ends up with 2 sun rooms + 1 shaded; AC2 (104/105/106) with 1 sun + 2
shaded. That's deliberate — it's what makes "do AC1's rooms run hotter"
a real question instead of a tautology.

**Occupancy**: a Bernoulli draw per 5-minute sample from an hourly
probability table, not a hard on/off window — two profiles
(`full_time_student`, `night_shift_student`) with distinct weekday *and*
weekend hour-of-day patterns, so there's real day-of-week signal for
`forecast_occupancy` (Step 3) to recover from history later.

`validate.py` checks: exactly 8 rooms (6 dorm / 2 mechanical), exactly 2 AC
units each servicing exactly 3 dorm rooms, every dorm has exactly one
temperature + one occupancy sensor, every mechanical room has none, and
every temperature sensor's `REPORTS_TO` edge points to the same AC unit
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
lower-risk here than the same pattern was for `chatbot_nl2cypher.py`: every
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
- **"Current" is defined explicitly**: the sensor data is historical and the
  model has no real-world clock, so the system prompt tells it to treat
  "current"/"right now" as "the most recent reading available" and how to
  find it (a wide `agg="hourly"` query, not a guessed narrow window) — see
  "Two real bugs" below for why this line exists.

**Verified two ways**: the loop's control flow with a fake Anthropic client
(7 tests, no API key needed) — tool dispatch, error feedback,
retry-within-budget, the iteration cap, usage accumulation — and, once a
key became available, live against the real API.

**Two real bugs the live run caught that the fake-client tests couldn't**:
1. `claude-sonnet-5` produces an extended-thinking block by default, and
   that consumed the entire original `max_tokens=1024` budget before ever
   reaching an answer or a tool call (`stop_reason: "max_tokens"`, empty
   response). This agent dispatches to simple typed tools — it doesn't need
   deep reasoning — so `thinking={"type": "disabled"}` is now passed
   explicitly, and `max_tokens` raised to 2048 as margin. The loop also no
   longer silently returns an empty string on truncation; it says so.
2. Asked "what's the current temperature in room 101," the model correctly
   noticed it has no live clock (the data is historical) but then guessed
   an arbitrary narrow window near the *start* of history instead of
   finding the *actual* latest reading — appropriately cautious in tone,
   but not actually correct. Fixed with the system-prompt guidance above;
   re-verified live afterward: it correctly found the true latest reading
   (`2024-01-14T23:55:00Z`, confirmed against the generator's actual date
   range), resolved the AC relationship, used all 5 tool-call iterations
   doing so, and explicitly said it had hit the iteration cap — the first
   live, real-world confirmation of nearly the entire Step 4 design working
   together end-to-end (also a notable cost/latency data point for the
   three-way comparison below: 5 tool calls for something `chatbot_nl2cypher.py`
   answers in one query).

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

python -m app.graph.load_readings   # duplicates TimescaleDB's readings into Neo4j as Reading
                                     # nodes -- see "Shared local database" below for why

pytest   # 62 tests: offline model/safety/agent-loop checks + live tool-layer tests against the containers above

# requires ANTHROPIC_API_KEY in .env:
python -c "from app.agent.loop import ask; print(ask('Which rooms does AC1 service?'))"
```

`docker-compose.yml`'s top-level `name: dorm-agent` is intentional, not a
leftover from before this folder was renamed -- it pins the Compose
project identity so the already-seeded `neo4j_data`/`timescale_data`
volumes and the `dorm-agent-neo4j`/`dorm-agent-timescaledb` containers
keep resolving correctly regardless of what the folder itself is called.
Don't change it without migrating those volumes first.

## Shared local database (optional)

This Neo4j database can be used as a local, self-hosted alternative to the
`chatbot_classifier.py`/`chatbot_nl2cypher.py` Streamlit bots' Neo4j Aura instance one
level up -- same schema (`Room.room_number`, `AC_Unit.ac_id`, `Sensor
{sensor_id, sensor_type}`, `Reading` nodes), same AC-unit id format (`AC1`/
`AC2`, no hyphen). Point those two files' `NEO4J_URI`/`NEO4J_USERNAME`/
`NEO4J_PASSWORD` at this instance instead of Aura and they work unmodified
-- `chatbot_nl2cypher.py` in particular needs no prompt or schema-description
changes at all, since its existing few-shot examples already describe this
exact naming. `app/graph/load_readings.py` is what makes that possible:
TimescaleDB stays the source of truth for sensor data, and this script
duplicates it into Neo4j as `Reading` nodes purely so `chatbot_nl2cypher.py`'s
single-Cypher-query design (which expects sensor data to live in Neo4j) has
something to query. Re-run it after `generate.py` if you regenerate with a
different seed/date range -- it always clears and reloads, never appends.

## Three-way comparison

[`compare_systems.py`](../compare_systems.py), one level up, runs this
agent, `chatbot_classifier.py`, and `chatbot_nl2cypher.py` against the
same local Neo4j/TimescaleDB and the same model (`claude-sonnet-5`) over
107 questions, with ground truth computed directly from the database. Full
write-up -- method, results, and where each architecture actually breaks:
**[icy-mint.github.io/building-graph-chatbot](https://icy-mint.github.io/building-graph-chatbot/)**
(source: `../docs/index.html`). Raw results: `../compare_systems_results_full.json`.

## Eval harness (Step 6)

`evals/golden.yaml` -- 28 cases covering pure graph/time-series lookups,
multi-hop (hot rooms -> their AC), forecasting, ambiguous/nonexistent
rooms, mechanical-room-has-no-sensors, no-data date ranges, out-of-scope
questions, and 3 prompt-injection attempts. Scoring is deterministic (tool
selection, argument accuracy, tool status, answer-text checks, and numeric
fact checks against ground truth computed live from the database, never
hand-typed in the YAML) -- no LLM needed to grade. `evals/run_eval.py`
runs it against the real agent and writes a timestamped JSON report plus
`latest.json`.

```bash
make eval         # deterministic scoring only
make eval-judge    # + an LLM-as-judge faithfulness pass (claude-haiku-5-5,
                    # a different/cheaper model than the agent) -- extra
                    # cost per run, off by default
```

Latest run: **26/28** deterministic (the 2 misses are a known precision
tradeoff -- see "Two real bugs" above -- and a paraphrase the keyword
checker didn't anticipate, not an agent fault; both pass the LLM-judge
pass). This is a different thing from the `compare_systems.py` comparison
above: that grades final-answer correctness across all three chatbot
architectures, this grades THIS agent's tool-selection/argument accuracy
and the injection/drift checks the spec specifically asked for.

**The LLM-judge pass earned its keep on the first real run**: it caught a
genuine, intermittent hallucination the deterministic checks missed
entirely -- asked a question with no reason to mention AC units at all,
the model once volunteered "room 105 is serviced by AC1" (it's actually
AC2) and cited `get_ac_for_room`/`get_rooms_serviced_by_ac` as the source,
despite never calling either (only `get_occupancy` was called). A
regression guard for this exact case is now in `golden.yaml`. The judge
also has a calibration quirk worth knowing before trusting its "NO"
verdicts at face value: it treats the model doing simple, correct
arithmetic over real tool-returned numbers (e.g. averaging several hourly
readings, which some questions have no dedicated tool for and inherently
require) as "unsupported," which isn't the same thing as fabrication --
its "YES/NO" faithfulness label needs a human reading the reason, not
blind trust, especially for anything the model had to compute itself.

`tests/test_prompt_schema_drift.py` -- the drift guard: fails if
`SYSTEM_PROMPT`'s room/AC counts and ids disagree with `model.py`, if
`model.py` disagrees with the live Neo4j schema, or if a tool's declared
JSON schema (what the model sees) disagrees with its real Python
signature. Introspects the actual schema rather than hand-maintaining a
duplicate description of it, per the spec.

## Roadmap (not yet built)

5. FastAPI + Streamlit UI
