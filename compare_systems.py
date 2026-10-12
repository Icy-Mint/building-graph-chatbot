"""Three-way comparison of chatbot_classifier.py, chatbot_nl2cypher.py, and
tool-calling-agent's agent loop -- same model (claude-sonnet-5), same
underlying local Neo4j+TimescaleDB data, same golden question set.

Note: compare_systems_results.json / compare_systems_results_full.json were
recorded before this project's three systems got clearer names, so their
"chatbot.py" / "chatbotForecast.py" / "dorm-agent" keys are a frozen,
accurate record of what ran at the time -- not left stale by accident. This
script keeps using those same dict/JSON keys (only the display text below
and the real imports were updated) so --resume still matches that file.

Ground truth for every question is computed here directly against the
database (raw SQL / the AC_ASSIGNMENTS constant), independently of all
three systems under test -- never by trusting one system's answer to
check another's.

Run from the repo root with the root .venv (it has every dependency all
three systems need -- see the "single venv" discovery in this session):
    ./.venv/Scripts/python.exe compare_systems.py
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

# Windows' default console/file-redirect encoding (cp1252) can't represent
# every character model output can contain (emoji, some punctuation) and
# raises instead of substituting, which previously crashed a 100+ question
# run partway through on an otherwise-harmless print. Force UTF-8 with
# replacement so a stray character degrades the log, not the whole run.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── Force every system onto the same local database + same model,
# regardless of what the root .env / tool-calling-agent/.env currently point at.
# Set before importing anything that reads these at import time.
os.environ["NEO4J_URI"] = "bolt://localhost:7687"
os.environ["NEO4J_USERNAME"] = "neo4j"
os.environ["NEO4J_PASSWORD"] = "dormagent-local-dev-pw1"
os.environ["SENSOR_BACKEND"] = "timescaledb"
os.environ["POSTGRES_HOST"] = "localhost"
os.environ["POSTGRES_PORT"] = "5432"
os.environ["POSTGRES_DB"] = "dorm_sensors"
os.environ["POSTGRES_READONLY_USER"] = "dorm_readonly"
os.environ["POSTGRES_READONLY_PASSWORD"] = "dormagent-local-dev-pw2"
os.environ["LLM_PROVIDER"] = "anthropic"
os.environ["ANTHROPIC_MODEL"] = "claude-sonnet-5"
os.environ["MAX_AGENT_ITERATIONS"] = "5"

from dotenv import load_dotenv

load_dotenv(override=False)  # only to pick up ANTHROPIC_API_KEY, OPENAI_API_KEY etc.

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(REPO_ROOT, "tool-calling-agent"))

import psycopg

import chatbot_classifier as cb
import chatbot_nl2cypher as cf
from app.agent.loop import ask as dormagent_ask
from app.model import AC_ASSIGNMENTS


# ─────────────────────────────────────────
# Ground truth, computed directly from the database
# ─────────────────────────────────────────
def _pg_conn():
    return psycopg.connect(
        host=os.environ["POSTGRES_HOST"],
        port=os.environ["POSTGRES_PORT"],
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_READONLY_USER"],
        password=os.environ["POSTGRES_READONLY_PASSWORD"],
    )


def gt_current_extreme(sensor_type: str, direction: str):
    """direction: 'max' or 'min'. Latest reading per room, then the extreme."""
    order = "DESC" if direction == "max" else "ASC"
    with _pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (room_number) room_number, value, ts
            FROM sensor_readings
            WHERE sensor_type = %s
            ORDER BY room_number, ts DESC
            """,
            (sensor_type,),
        )
        latest = cur.fetchall()
    room, value, ts = sorted(latest, key=lambda r: r[1], reverse=(direction == "max"))[0]
    return {"room": room, "value": round(value, 2), "ts": ts.isoformat()}


def gt_current_value(sensor_type: str, room: str):
    with _pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT value, ts FROM sensor_readings WHERE sensor_type=%s AND room_number=%s ORDER BY ts DESC LIMIT 1",
            (sensor_type, room),
        )
        value, ts = cur.fetchone()
    return {"room": room, "value": round(value, 2), "ts": ts.isoformat()}


def gt_alltime_extreme(sensor_type: str, direction: str):
    order = "DESC" if direction == "max" else "ASC"
    with _pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT room_number, value, ts FROM sensor_readings WHERE sensor_type=%s ORDER BY value {order} LIMIT 1",
            (sensor_type,),
        )
        room, value, ts = cur.fetchone()
    return {"room": room, "value": round(value, 2), "ts": ts.isoformat()}


def gt_next_hour_occupancy_prob(room: str):
    """Mirrors chatbot_classifier.py's own definition (hour-of-day only,
    averaged over all history) so there's one fair, shared definition of
    'forecast' to grade all three systems against -- tool-calling-agent's
    forecast_occupancy adds a day-of-week split on top, which is a
    legitimate richer method, not graded as wrong if it reasonably
    diverges from this simpler baseline."""
    next_hour = (datetime.now(timezone.utc).hour + 1) % 24
    with _pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT value FROM sensor_readings WHERE sensor_type='occupancy' AND room_number=%s AND EXTRACT(HOUR FROM ts)=%s",
            (room, next_hour),
        )
        values = [r[0] for r in cur.fetchall()]
    return {"room": room, "hour": next_hour, "prob": round(sum(values) / len(values), 2), "n": len(values)}


def gt_ac_for_room(room: str):
    for ac, info in AC_ASSIGNMENTS.items():
        if room in info["dorms"]:
            return ac
    return None


def gt_rooms_for_ac(ac: str):
    return AC_ASSIGNMENTS[ac]["dorms"]


def gt_same_ac(room1: str, room2: str) -> bool:
    return gt_ac_for_room(room1) == gt_ac_for_room(room2)


def gt_alltime_extreme_room(sensor_type: str, direction: str, room: str):
    order = "DESC" if direction == "max" else "ASC"
    with _pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT value, ts FROM sensor_readings WHERE sensor_type=%s AND room_number=%s ORDER BY value {order} LIMIT 1",
            (sensor_type, room),
        )
        value, ts = cur.fetchone()
    return {"room": room, "value": round(value, 2), "ts": ts.isoformat()}


def gt_alltime_occupancy_rate(room: str):
    with _pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT avg(value) FROM sensor_readings WHERE sensor_type='occupancy' AND room_number=%s", (room,)
        )
        (rate,) = cur.fetchone()
    return {"room": room, "rate": round(rate, 2)}


DORM_ROOMS = ["101", "102", "103", "104", "105", "106"]
MECHANICAL_ROOMS = ["201", "202"]


# ─────────────────────────────────────────
# Golden question set
# Each entry: id, category, question, gt (ground truth dict/value),
# check(answer_text, gt) -> bool
# Built programmatically (not hand-typed one by one) so it scales to ~100
# items while every ground-truth value is still computed directly from the
# database or the AC_ASSIGNMENTS constant, same as the original 11-question
# set this grew out of.
# ─────────────────────────────────────────
def _mentions_room(text, room):
    return bool(re.search(rf"\b{re.escape(room)}\b", text))


def _mentions_number_near(text, value, tol=0.5):
    for m in re.finditer(r"-?\d+(?:\.\d+)?", text):
        if abs(float(m.group()) - value) <= tol:
            return True
    return False


_DECLINE_WORDS = ["don't have", "do not have", "no data", "cannot", "can't", "not able", "outside my", "beyond the scope",
                  "not something i", "i'm not able", "no access", "not have access", "not part of", "outside the scope"]


def _declines(text):
    return any(w in text.lower() for w in _DECLINE_WORDS)


def build_questions():
    questions = []

    def add(id, category, question, gt, check):
        questions.append({"id": id, "category": category, "question": question, "gt": gt, "check": check})

    # current_hottest / current_coldest -- paraphrases, same ground truth each time
    gt_hot = gt_current_extreme("temperature", "max")
    for i, phrasing in enumerate([
        "Which room is currently the hottest?",
        "What's the hottest room right now?",
        "Right now, which dorm room has the highest temperature?",
    ]):
        add(f"current_hottest_{i}", "current_hottest", phrasing, gt_hot,
            lambda a, gt=gt_hot: _mentions_room(a, gt["room"]) and _mentions_number_near(a, gt["value"]))

    gt_cold = gt_current_extreme("temperature", "min")
    for i, phrasing in enumerate([
        "Which room is currently the coldest?",
        "What's the coldest room right now?",
        "Right now, which dorm room has the lowest temperature?",
    ]):
        add(f"current_coldest_{i}", "current_coldest", phrasing, gt_cold,
            lambda a, gt=gt_cold: _mentions_room(a, gt["room"]) and _mentions_number_near(a, gt["value"]))

    # current_temp per room, plus 2 alternate phrasings
    for room in DORM_ROOMS:
        gt = gt_current_value("temperature", room)
        add(f"current_temp_{room}", "current_temp", f"What is the temperature in room {room} right now?", gt,
            lambda a, gt=gt: _mentions_number_near(a, gt["value"]))
    for room in ["101", "106"]:
        gt = gt_current_value("temperature", room)
        add(f"current_temp_alt_{room}", "current_temp", f"How warm is room {room} at the moment?", gt,
            lambda a, gt=gt: _mentions_number_near(a, gt["value"]))

    # alltime_hottest / alltime_coldest -- global, paraphrases
    gt = gt_alltime_extreme("temperature", "max")
    for i, phrasing in enumerate([
        "What is the highest temperature ever recorded, and in which room?",
        "What's the all-time hottest temperature across the building, and where?",
    ]):
        add(f"alltime_hottest_{i}", "alltime_hottest", phrasing, gt,
            lambda a, gt=gt: _mentions_room(a, gt["room"]) and _mentions_number_near(a, gt["value"]))

    gt = gt_alltime_extreme("temperature", "min")
    for i, phrasing in enumerate([
        "What is the lowest temperature ever recorded, and in which room?",
        "What's the all-time coldest temperature across the building, and where?",
    ]):
        add(f"alltime_coldest_{i}", "alltime_coldest", phrasing, gt,
            lambda a, gt=gt: _mentions_room(a, gt["room"]) and _mentions_number_near(a, gt["value"]))

    # alltime extremes per specific room
    for room in DORM_ROOMS:
        gt = gt_alltime_extreme_room("temperature", "max", room)
        add(f"alltime_hottest_room_{room}", "alltime_hottest_room",
            f"What's the highest temperature ever recorded in room {room}?", gt,
            lambda a, gt=gt: _mentions_number_near(a, gt["value"]))
    for room in DORM_ROOMS:
        gt = gt_alltime_extreme_room("temperature", "min", room)
        add(f"alltime_coldest_room_{room}", "alltime_coldest_room",
            f"What's the lowest temperature ever recorded in room {room}?", gt,
            lambda a, gt=gt: _mentions_number_near(a, gt["value"]))

    # current_occupancy per room, plus 2 alt phrasings
    def occ_check(gt):
        return lambda a, gt=gt: (("yes" in a.lower() or "occupied" in a.lower()) if gt["value"] >= 0.5
                                  else ("no" in a.lower() or "unoccupied" in a.lower() or "not occupied" in a.lower()))

    for room in DORM_ROOMS:
        gt = gt_current_value("occupancy", room)
        add(f"current_occupancy_{room}", "current_occupancy", f"Is room {room} currently occupied?", gt, occ_check(gt))
    for room in ["102", "105"]:
        gt = gt_current_value("occupancy", room)
        add(f"current_occupancy_alt_{room}", "current_occupancy", f"Is anyone in room {room} right now?", gt, occ_check(gt))

    # occupancy_forecast per room, plus 2 alt phrasings
    def forecast_check(gt):
        return lambda a, gt=gt: _mentions_number_near(a, gt["prob"] * 100, tol=20) or _mentions_number_near(a, gt["prob"], tol=0.2)

    for room in DORM_ROOMS:
        gt = gt_next_hour_occupancy_prob(room)
        add(f"occupancy_forecast_{room}", "occupancy_forecast",
            f"Based on historical patterns, what's the probability room {room} will be occupied in the next hour?",
            gt, forecast_check(gt))
    for room in ["101", "104"]:
        gt = gt_next_hour_occupancy_prob(room)
        add(f"occupancy_forecast_alt_{room}", "occupancy_forecast",
            f"How likely is room {room} to be occupied an hour from now, based on history?", gt, forecast_check(gt))

    # ac_mapping: room->AC, AC->rooms, same-AC pair checks
    for room in DORM_ROOMS:
        gt = gt_ac_for_room(room)
        add(f"ac_for_{room}", "ac_mapping", f"Which AC unit services room {room}?", gt,
            lambda a, gt=gt: gt.lower() in a.lower().replace("-", "").replace(" ", ""))
    for ac in ["AC1", "AC2"]:
        gt = gt_rooms_for_ac(ac)
        add(f"rooms_for_{ac}", "ac_mapping", f"Which rooms does {ac} service?", gt,
            lambda a, gt=gt: all(_mentions_room(a, r) for r in gt))
    for r1, r2 in [("101", "102"), ("101", "104"), ("102", "103"), ("104", "105"), ("105", "106"), ("103", "104")]:
        gt = gt_same_ac(r1, r2)
        add(f"same_ac_{r1}_{r2}", "ac_mapping", f"Are room {r1} and room {r2} serviced by the same AC unit?", gt,
            lambda a, gt=gt: (("yes" in a.lower() or "same" in a.lower()) if gt
                              else ("no" in a.lower() or "different" in a.lower())))

    # multiroom current temperature comparisons
    for r1, r2 in [("101", "104"), ("102", "105"), ("103", "106"), ("101", "102"), ("104", "106"), ("102", "106"), ("101", "106"), ("103", "105")]:
        g1, g2 = gt_current_value("temperature", r1), gt_current_value("temperature", r2)
        add(f"compare_temp_{r1}_{r2}", "current_temp_multiroom", f"Compare the current temperatures of room {r1} and room {r2}.",
            {r1: g1, r2: g2},
            lambda a, g1=g1, g2=g2: _mentions_number_near(a, g1["value"]) and _mentions_number_near(a, g2["value"]))

    # multiroom current occupancy comparisons
    for r1, r2 in [("101", "104"), ("102", "105"), ("103", "106"), ("101", "106"), ("102", "104"), ("105", "106")]:
        g1, g2 = gt_current_value("occupancy", r1), gt_current_value("occupancy", r2)
        add(f"compare_occ_{r1}_{r2}", "current_occupancy_multiroom",
            f"Are both room {r1} and room {r2} currently occupied?", {r1: g1, r2: g2},
            lambda a, g1=g1, g2=g2: (("yes" in a.lower() or "both" in a.lower()) if (g1["value"] >= 0.5 and g2["value"] >= 0.5)
                                      else ("no" in a.lower() or "not both" in a.lower())))

    # room metadata / mechanical rooms
    add("mech_201_sensors", "room_info", "Does room 201 have any sensors?", False,
        lambda a: _declines(a) is False and ("no" in a.lower() or "doesn't have" in a.lower() or "does not have" in a.lower() or "none" in a.lower()))
    add("mech_202_type", "room_info", "Is room 202 a mechanical room or a dorm room?", "mechanical",
        lambda a: "mechanical" in a.lower())
    add("dorm_103_type", "room_info", "Is room 103 a mechanical room or a dorm room?", "dorm",
        lambda a: "dorm" in a.lower())
    add("count_dorm_rooms", "room_info", "How many dorm rooms are in the building?", len(DORM_ROOMS),
        lambda a: _mentions_number_near(a, len(DORM_ROOMS), tol=0))
    add("count_ac_units", "room_info", "How many AC units are there in total?", len(AC_ASSIGNMENTS),
        lambda a: _mentions_number_near(a, len(AC_ASSIGNMENTS), tol=0))
    add("list_mech_rooms", "room_info", "Which rooms are mechanical rooms?", MECHANICAL_ROOMS,
        lambda a: all(_mentions_room(a, r) for r in MECHANICAL_ROOMS))

    # all-time occupancy rate
    for room in ["101", "104", "106", "103", "105"]:
        gt = gt_alltime_occupancy_rate(room)
        add(f"alltime_occ_rate_{room}", "alltime_occupancy_rate",
            f"Over all recorded history, what fraction of the time has room {room} been occupied?", gt,
            lambda a, gt=gt: _mentions_number_near(a, gt["rate"] * 100, tol=15) or _mentions_number_near(a, gt["rate"], tol=0.15))

    # all-time comparison between two specific rooms
    for r1, r2 in [("101", "104"), ("102", "105"), ("103", "106"), ("101", "103"), ("104", "106")]:
        e1 = gt_alltime_extreme_room("temperature", "max", r1)
        e2 = gt_alltime_extreme_room("temperature", "max", r2)
        hotter = r1 if e1["value"] >= e2["value"] else r2
        add(f"alltime_compare_{r1}_{r2}", "alltime_compare",
            f"Historically, which room has reached a higher temperature: room {r1} or room {r2}?", hotter,
            lambda a, hotter=hotter: _mentions_room(a, hotter))

    # out-of-scope / fallback
    for i, q in enumerate([
        "What's the weather like outside today?",
        "What's the current stock price of Apple?",
        "Tell me a joke.",
        "What is your name?",
        "Who is the building manager?",
        "What's on the cafeteria menu today?",
        "What's the wifi password for the building?",
        "When is the next fire alarm test scheduled?",
        "Is the laundry machine on the 3rd floor available right now?",
        "What's the capital of France?",
        "Can you book a parking spot for me?",
        "Who built this chatbot?",
    ]):
        add(f"fallback_{i}", "fallback", q, None, lambda a: _declines(a))

    # compound / cross-category questions
    gt = gt_current_extreme("temperature", "max")
    add("compound_hottest_ac", "compound", "Which room is currently the hottest, and which AC unit services it?",
        {"room": gt["room"], "ac": gt_ac_for_room(gt["room"])},
        lambda a, gt=gt: _mentions_room(a, gt["room"]) and gt_ac_for_room(gt["room"]).lower() in a.lower().replace("-", "").replace(" ", ""))

    gt = gt_current_extreme("temperature", "min")
    add("compound_coldest_ac", "compound", "Which room is currently the coldest, and which AC unit services it?",
        {"room": gt["room"], "ac": gt_ac_for_room(gt["room"])},
        lambda a, gt=gt: _mentions_room(a, gt["room"]) and gt_ac_for_room(gt["room"]).lower() in a.lower().replace("-", "").replace(" ", ""))

    for room in ["102", "105", "106"]:
        gt_t = gt_current_value("temperature", room)
        gt_ac = gt_ac_for_room(room)
        add(f"compound_temp_ac_{room}", "compound",
            f"What is the current temperature in room {room}, and which AC unit services it?",
            {"temp": gt_t, "ac": gt_ac},
            lambda a, gt_t=gt_t, gt_ac=gt_ac: _mentions_number_near(a, gt_t["value"]) and gt_ac.lower() in a.lower().replace("-", "").replace(" ", ""))

    return questions


QUESTIONS = build_questions()


# ─────────────────────────────────────────
# System runners -- each returns {answer, input_tokens, output_tokens, latency_ms, error}
# ─────────────────────────────────────────
def run_chatbot_py(question, driver, graph_helper, sensor_helper, classification_chain, fallback_chain, parser):
    usage = {}
    t0 = time.time()
    try:
        answer, action, rooms = cb.ask(
            question, classification_chain, fallback_chain, parser, graph_helper, sensor_helper,
            usage_callback=usage.update,
        )
        error = None
    except Exception as e:
        answer, error = f"[EXCEPTION: {e}]", str(e)
    latency_ms = int((time.time() - t0) * 1000)
    totals = {"input_tokens": 0, "output_tokens": 0}
    for model_usage in usage.values():
        totals["input_tokens"] += model_usage.get("input_tokens", 0)
        totals["output_tokens"] += model_usage.get("output_tokens", 0)
    return {"answer": answer, **totals, "latency_ms": latency_ms, "error": error}


def run_chatbotforecast(question, chain, graph):
    usage = {}
    t0 = time.time()
    try:
        cypher, result, error = cf.ask_graph(question, chain, graph, usage_callback=usage.update)
        if error:
            answer = f"[ERROR: {error}]"
        else:
            answer = str(result)
    except Exception as e:
        answer, error = f"[EXCEPTION: {e}]", str(e)
    latency_ms = int((time.time() - t0) * 1000)
    totals = {"input_tokens": 0, "output_tokens": 0}
    for model_usage in usage.values():
        totals["input_tokens"] += model_usage.get("input_tokens", 0)
        totals["output_tokens"] += model_usage.get("output_tokens", 0)
    return {"answer": answer, **totals, "latency_ms": latency_ms, "error": error}


def run_dormagent(question):
    t0 = time.time()
    try:
        result = dormagent_ask(question)
        answer = result["answer"] or "[no answer -- hit iteration cap or truncation]"
        usage = result["usage"]
        error = None
    except Exception as e:
        answer, usage, error = f"[EXCEPTION: {e}]", {"input_tokens": 0, "output_tokens": 0}, str(e)
    latency_ms = int((time.time() - t0) * 1000)
    return {
        "answer": answer,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "latency_ms": latency_ms,
        "error": error,
    }


def main():
    print("Setting up chatbot_classifier.py resources (local Neo4j + TimescaleDB)...")
    cb.require_env_vars()
    driver = cb.get_driver()
    graph_helper = cb.GraphHelper(driver)
    sensor_helper = cb.get_sensor_helper()
    classification_chain = cb.get_classification_chain()
    fallback_chain = cb.get_fallback_chain()
    parser = cb.parser

    print("Setting up chatbot_nl2cypher.py resources (local Neo4j)...")
    cf.require_env_vars()
    graph = cf.get_graph()
    chain = cf.get_chain()

    out_path = os.path.join(REPO_ROOT, "compare_systems_results_full.json")
    results = {"chatbot.py": [], "chatbotForecast.py": [], "dorm-agent": []}
    done_ids = set()
    if "--resume" in sys.argv and os.path.exists(out_path):
        with open(out_path) as f:
            prior = json.load(f)
        prior_ids = {r["id"] for r in prior["results"]["chatbot.py"]}
        # Only treat a question as done if ALL three systems have a
        # non-exception row for it -- a mid-run credit/billing failure
        # (like the one that triggered this flag) leaves partial rows that
        # should be retried, not kept.
        for qid in prior_ids:
            rows = {sys_name: next((r for r in rows_ if r["id"] == qid), None) for sys_name, rows_ in prior["results"].items()}
            if all(r is not None and not (r.get("error") or "").startswith("Error code: 400") for r in rows.values()):
                done_ids.add(qid)
        for sys_name, rows_ in prior["results"].items():
            results[sys_name] = [r for r in rows_ if r["id"] in done_ids]
        print(f"Resuming: {len(done_ids)}/{len(QUESTIONS)} questions already complete, skipping those.")

    for qi, q in enumerate(QUESTIONS, 1):
        if q["id"] in done_ids:
            continue
        print(f"\n=== ({qi}/{len(QUESTIONS)}) [{q['category']}] {q['question']}")

        r1 = run_chatbot_py(q["question"], driver, graph_helper, sensor_helper, classification_chain, fallback_chain, parser)
        r1["correct"] = bool(q["check"](r1["answer"])) if not r1["error"] else False
        results["chatbot.py"].append({"id": q["id"], **r1})
        print(f"  chatbot_classifier.py: {'OK ' if r1['correct'] else 'FAIL'} | {r1['answer'][:120]!r}")

        r2 = run_chatbotforecast(q["question"], chain, graph)
        r2["correct"] = bool(q["check"](r2["answer"])) if not r2["error"] else False
        results["chatbotForecast.py"].append({"id": q["id"], **r2})
        print(f"  chatbot_nl2cypher.py : {'OK ' if r2['correct'] else 'FAIL'} | {r2['answer'][:120]!r}")

        r3 = run_dormagent(q["question"])
        r3["correct"] = bool(q["check"](r3["answer"])) if not r3["error"] else False
        results["dorm-agent"].append({"id": q["id"], **r3})
        print(f"  tool-calling-agent   : {'OK ' if r3['correct'] else 'FAIL'} | {r3['answer'][:120]!r}")

        # Flush after every question -- a 100-question x 3-system run takes a
        # while; don't lose completed work if it's interrupted partway.
        with open(out_path, "w") as f:
            json.dump({"questions": QUESTIONS_SERIALIZABLE, "results": results}, f, indent=2, default=str)

    # ── Summary
    # Dict/JSON keys stay as recorded ("chatbot.py" etc., see module
    # docstring); only the printed label uses the current file names.
    display_name = {"chatbot.py": "chatbot_classifier.py", "chatbotForecast.py": "chatbot_nl2cypher.py",
                     "dorm-agent": "tool-calling-agent"}
    print("\n" + "=" * 70)
    print(f"{'system':<22}{'accuracy':<12}{'avg latency ms':<18}{'total in tok':<14}{'total out tok':<14}")
    for system, rows in results.items():
        n = len(rows)
        correct = sum(r["correct"] for r in rows)
        avg_latency = sum(r["latency_ms"] for r in rows) / n
        total_in = sum(r["input_tokens"] for r in rows)
        total_out = sum(r["output_tokens"] for r in rows)
        label = display_name.get(system, system)
        print(f"{label:<22}{correct}/{n:<10}{avg_latency:<18.0f}{total_in:<14}{total_out:<14}")

    with open(out_path, "w") as f:
        json.dump({"questions": QUESTIONS_SERIALIZABLE, "results": results}, f, indent=2, default=str)
    print(f"\nFull results written to {out_path}")


QUESTIONS_SERIALIZABLE = [{"id": q["id"], "category": q["category"], "question": q["question"], "gt": q["gt"]} for q in QUESTIONS]


if __name__ == "__main__":
    main()
