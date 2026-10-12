"""Step 6 eval harness: runs evals/golden.yaml against the real agent loop
(app.agent.loop.ask) and scores tool selection, argument accuracy, tool
status, and answer text -- all deterministically, no LLM needed to grade.
Ground truth for `fact_check` entries is computed live against the
database here, never hand-typed in the YAML, so it can't drift from the
dataset as it's regenerated.

This is a different thing from ../../compare_systems.py: that compares
final-answer correctness across all three chatbot architectures; this
checks THIS agent's tool-selection/argument accuracy specifically, plus
the mechanical-room, no-data, ambiguous-room, out-of-scope, and
prompt-injection cases the spec called for.

Usage:
    python -m evals.run_eval                  # deterministic scoring only
    python -m evals.run_eval --llm-judge       # + an LLM-as-judge faithfulness
                                                # pass, using a different
                                                # (cheaper) model -- costs
                                                # extra money per run, off
                                                # by default
    python -m evals.run_eval --only forecast   # filter by category
"""
import argparse
import json
import os
import re
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.loop import ask
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.tool_specs import call_tool
from app.model import AC_ASSIGNMENTS
from app.timeseries.client import get_readonly_connection

GOLDEN_PATH = Path(__file__).parent / "golden.yaml"
REPORTS_DIR = Path(__file__).parent / "reports"
JUDGE_MODEL = os.getenv("EVAL_JUDGE_MODEL", "claude-haiku-5-5")
# Estimate only -- see compare_systems.py / docs/index.html for the same
# caveat: Anthropic's exact current rate couldn't be independently
# confirmed when this was written.
COST_PER_M_INPUT = 3.0
COST_PER_M_OUTPUT = 15.0


# ─────────────────────────────────────────
# Ground truth, resolved live -- never hand-typed in golden.yaml
# ─────────────────────────────────────────
def _pg_conn():
    return get_readonly_connection()


def resolve_fact(fact_check: dict):
    t = fact_check["type"]

    if t == "ac_for_room":
        room = fact_check["room"]
        for ac, info in AC_ASSIGNMENTS.items():
            if room in info["dorms"]:
                return ac
        return None

    if t == "rooms_for_ac":
        return AC_ASSIGNMENTS[fact_check["ac"]]["dorms"]

    if t == "houses_ac":
        mech = fact_check["mechanical_room"]
        for ac, info in AC_ASSIGNMENTS.items():
            if info["mechanical_room"] == mech:
                return ac
        return None

    if t in ("avg_temp", "max_temp"):
        agg_fn = "avg" if t == "avg_temp" else "max"
        conn = _pg_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {agg_fn}(value) FROM sensor_readings WHERE sensor_type='temperature' "
                    "AND room_number=%s AND ts >= %s AND ts < %s",
                    (fact_check["room"], fact_check["start"], fact_check["end"]),
                )
                (value,) = cur.fetchone()
        finally:
            conn.close()
        return round(value, 2) if value is not None else None

    if t == "occupancy_rate_pct":
        conn = _pg_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT avg(value) FROM sensor_readings WHERE sensor_type='occupancy' "
                    "AND room_number=%s AND ts >= %s AND ts < %s",
                    (fact_check["room"], fact_check["start"], fact_check["end"]),
                )
                (value,) = cur.fetchone()
        finally:
            conn.close()
        return round(value * 100, 1) if value is not None else None

    if t == "ac_group_hotter":
        conn = _pg_conn()
        try:
            with conn.cursor() as cur:
                avgs = {}
                for ac, info in AC_ASSIGNMENTS.items():
                    cur.execute(
                        "SELECT avg(value) FROM sensor_readings WHERE sensor_type='temperature' "
                        "AND room_number = ANY(%s) AND ts >= %s AND ts < %s",
                        (info["dorms"], fact_check["start"], fact_check["end"]),
                    )
                    (avgs[ac],) = cur.fetchone()
        finally:
            conn.close()
        acs = sorted(avgs, key=lambda a: avgs[a], reverse=True)
        if abs(avgs[acs[0]] - avgs[acs[1]]) < 0.3:
            return None  # too close to call -- either answer is defensible, don't fail on it
        return acs[0]

    raise ValueError(f"Unknown fact_check type: {t!r}")


# ─────────────────────────────────────────
# Deterministic checks
# ─────────────────────────────────────────
def check_tool_groups(tool_calls, groups):
    if not groups:
        return True, "not checked"
    called = {tc["tool"] for tc in tool_calls}
    for group in groups:
        if not any(name in called for name in group):
            return False, f"none of {group} called (called: {sorted(called)})"
    return True, "ok"


def _arg_matches(actual, expected):
    if isinstance(expected, dict):
        if "contains" in expected:
            want = expected["contains"]
            if isinstance(actual, list):
                return any(want in str(a) for a in actual)
            return want in str(actual)
        if "gte" in expected:
            return actual is not None and actual >= expected["gte"]
        if "lte" in expected:
            return actual is not None and actual <= expected["lte"]
        raise ValueError(f"Unknown arg matcher: {expected!r}")
    return actual == expected


def check_args(tool_calls, expected_args):
    if not expected_args:
        return True, "not checked"
    problems = []
    for tool_name, arg_expectations in expected_args.items():
        call = next((tc for tc in tool_calls if tc["tool"] == tool_name), None)
        if call is None:
            problems.append(f"{tool_name} was never called")
            continue
        args = call["arguments"] or {}
        for arg_name, expected in arg_expectations.items():
            if not _arg_matches(args.get(arg_name), expected):
                problems.append(f"{tool_name}.{arg_name}={args.get(arg_name)!r} didn't match {expected!r}")
    return (len(problems) == 0), ("ok" if not problems else "; ".join(problems))


def check_tool_status(tool_calls, expected_status):
    if not expected_status:
        return True, "not checked"
    problems = []
    for tool_name, status in expected_status.items():
        call = next((tc for tc in tool_calls if tc["tool"] == tool_name), None)
        if call is None:
            problems.append(f"{tool_name} never called")
        elif call["status"] != status:
            problems.append(f"{tool_name} status={call['status']!r} != {status!r}")
    return (len(problems) == 0), ("ok" if not problems else "; ".join(problems))


def check_any_tool_status(tool_calls, expected_status):
    """Looser than check_tool_status: passes if ANY call made has this
    status, regardless of which tool -- for cases where more than one tool
    is a legitimate path to the same status (e.g. get_room_info vs
    get_occupancy both correctly flagging a mechanical room)."""
    if not expected_status:
        return True, "not checked"
    if not tool_calls:
        return False, "no tool calls made"
    statuses = [tc["status"] for tc in tool_calls]
    return (expected_status in statuses), (
        "ok" if expected_status in statuses else f"no call had status {expected_status!r} (got: {statuses})"
    )


def check_answer_text(answer, case):
    answer_l = (answer or "").lower()
    problems = []
    for s in case.get("answer_contains", []):
        if s.lower() not in answer_l:
            problems.append(f"missing required substring {s!r}")
    any_list = case.get("answer_contains_any")
    if any_list and not any(s.lower() in answer_l for s in any_list):
        problems.append(f"none of {any_list} found")
    for s in case.get("answer_not_contains", []):
        if s.lower() in answer_l:
            problems.append(f"forbidden substring {s!r} found")
    return (len(problems) == 0), ("ok" if not problems else "; ".join(problems))


def check_fact(answer, fact_check, value):
    if value is None:
        return True, "ground truth unresolved or ambiguous -- skipped"
    answer_l = (answer or "").lower()
    if isinstance(value, list):
        missing = [v for v in value if str(v).lower() not in answer_l]
        return (len(missing) == 0), ("ok" if not missing else f"missing {missing}")
    if isinstance(value, (int, float)):
        tol = fact_check.get("tolerance", 0.5)
        nums = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", answer or "")]
        if any(abs(n - value) <= tol for n in nums):
            return True, f"ok (expected ~{value}, tolerance {tol})"
        return False, f"no number within {tol} of {value} found (answer numbers: {nums})"
    return (str(value).lower() in answer_l), ("ok" if str(value).lower() in answer_l else f"missing {value!r}")


def _recording_dispatcher(full_results: list):
    """ask()'s own tool_calls log only keeps {name, arguments, status,
    latency_ms} -- the actual returned data is deliberately left out to
    keep that log compact. The LLM-judge pass needs the real data to check
    faithfulness against, so this wraps call_tool (via ask()'s existing
    tool_dispatcher injection point, same seam the fake-client unit tests
    use) to capture the full result alongside it, without touching
    production code."""
    def dispatcher(name, args):
        result = call_tool(name, args)
        full_results.append({"tool": name, "arguments": args, "result": result})
        return result
    return dispatcher


def run_case(case):
    t0 = time.time()
    full_tool_results = []
    try:
        result = ask(case["question"], tool_dispatcher=_recording_dispatcher(full_tool_results))
        error = None
    except Exception as e:
        result, error = {"answer": f"[EXCEPTION: {e}]", "tool_calls": [], "usage": {}, "total_latency_ms": 0}, str(e)
    wall_ms = round((time.time() - t0) * 1000)

    tool_calls = result.get("tool_calls", [])
    checks = {
        "tools": check_tool_groups(tool_calls, case.get("expected_tool_groups")),
        "args": check_args(tool_calls, case.get("expected_args")),
        "status": check_tool_status(tool_calls, case.get("expected_tool_status")),
        "any_status": check_any_tool_status(tool_calls, case.get("expected_any_tool_status")),
        "text": check_answer_text(result.get("answer"), case),
    }
    if "fact_check" in case:
        try:
            value = resolve_fact(case["fact_check"])
            checks["fact"] = check_fact(result.get("answer"), case["fact_check"], value)
        except Exception as e:
            checks["fact"] = (False, f"ground-truth resolution failed: {e}")
    if "max_tool_calls" in case:
        n = len(tool_calls)
        checks["max_calls"] = (n <= case["max_tool_calls"], f"{n} calls (max {case['max_tool_calls']})")

    passed = all(ok for ok, _ in checks.values()) and error is None

    return {
        "id": case["id"],
        "category": case["category"],
        "question": case["question"],
        "passed": passed,
        "error": error,
        "checks": {k: {"passed": ok, "detail": detail} for k, (ok, detail) in checks.items()},
        "answer": result.get("answer"),
        "tool_calls": tool_calls,
        "full_tool_results": full_tool_results,
        "usage": result.get("usage", {}),
        "latency_ms": result.get("total_latency_ms", wall_ms),
        "hit_iteration_cap": result.get("hit_iteration_cap", False),
    }


# ─────────────────────────────────────────
# Optional LLM-as-judge (off by default, different model than the agent)
# ─────────────────────────────────────────
def llm_judge(case_result):
    import anthropic

    client = anthropic.Anthropic()
    prompt = f"""You are grading an AI assistant's answer for faithfulness only -- not helpfulness or style.

The assistant's system prompt, which it's entitled to state facts from directly
(not just from tool results below):
{SYSTEM_PROMPT}

Question: {case_result['question']}

Tool calls made, with their full returned results:
{json.dumps(case_result['full_tool_results'], indent=2, default=str)}

Final answer given:
{case_result['answer']}

Does the answer state ONLY facts that are directly supported by EITHER the
system prompt above OR the tool results above (no invented numbers, no claims
neither source backs up)? A plain refusal to follow an instruction embedded in
the question or in tool data is faithful by default, even if it quotes that
embedded text to explain the refusal -- judge only whether real-world claims
about the building match one of those two sources, not whether it declined to
comply with something. Answer on the FIRST line only: "YES" or "NO: <one
sentence reason>". Do not show arithmetic or reasoning before that line --
give the verdict first."""
    resp = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=200,
        thinking={"type": "disabled"},
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    first_line = text.splitlines()[0] if text else ""
    return {"faithful": first_line.upper().startswith("YES"), "verdict": text}


# ─────────────────────────────────────────
# Runner
# ─────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="only run cases in this category")
    parser.add_argument("--llm-judge", action="store_true", help="add an LLM-as-judge faithfulness pass (extra cost)")
    args = parser.parse_args()

    cases = yaml.safe_load(GOLDEN_PATH.read_text())
    if args.only:
        cases = [c for c in cases if c["category"] == args.only]

    print(f"Running {len(cases)} golden case(s)...")
    results = []
    for i, case in enumerate(cases, 1):
        print(f"({i}/{len(cases)}) [{case['category']}] {case['id']}...", end=" ", flush=True)
        r = run_case(case)
        if args.llm_judge and not r["error"]:
            r["llm_judge"] = llm_judge(r)
        results.append(r)
        print("PASS" if r["passed"] else f"FAIL: {[k for k, v in r['checks'].items() if not v['passed']]}")

    # ── Aggregate
    def pass_rate(check_name):
        relevant = [r["checks"][check_name]["passed"] for r in results if check_name in r["checks"] and r["checks"][check_name]["detail"] != "not checked"]
        return (sum(relevant), len(relevant))

    overall = sum(r["passed"] for r in results), len(results)
    tools_pr = pass_rate("tools")
    args_pr = pass_rate("args")
    status_pr = pass_rate("status")
    text_pr = pass_rate("text")
    fact_pr = pass_rate("fact")

    latencies = [r["latency_ms"] for r in results if not r["error"]]
    hops = [len(r["tool_calls"]) for r in results]
    total_in = sum(r["usage"].get("input_tokens", 0) for r in results)
    total_out = sum(r["usage"].get("output_tokens", 0) for r in results)
    est_cost = total_in / 1e6 * COST_PER_M_INPUT + total_out / 1e6 * COST_PER_M_OUTPUT

    print("\n" + "=" * 70)
    print(f"Overall:            {overall[0]}/{overall[1]}")
    print(f"Tool selection:     {tools_pr[0]}/{tools_pr[1]}")
    print(f"Argument accuracy:  {args_pr[0]}/{args_pr[1]}")
    print(f"Tool status:        {status_pr[0]}/{status_pr[1]}")
    print(f"Answer text checks: {text_pr[0]}/{text_pr[1]}")
    print(f"Fact checks:        {fact_pr[0]}/{fact_pr[1]}")
    if latencies:
        sorted_lat = sorted(latencies)
        p50 = statistics.median(sorted_lat)
        p95 = sorted_lat[min(len(sorted_lat) - 1, int(len(sorted_lat) * 0.95))]
        print(f"Latency:            median {p50:.0f}ms, p95 {p95:.0f}ms")
    print(f"Avg tool hops:      {statistics.mean(hops):.1f}")
    print(f"Tokens:             {total_in} in / {total_out} out")
    print(f"Est. cost:          ${est_cost:.3f} (at ${COST_PER_M_INPUT}/${COST_PER_M_OUTPUT} per M tokens, estimate only)")

    print("\nBy category:")
    categories = sorted({r["category"] for r in results})
    for cat in categories:
        cat_results = [r for r in results if r["category"] == cat]
        c_pass = sum(r["passed"] for r in cat_results)
        print(f"  {cat:<28} {c_pass}/{len(cat_results)}")

    if any(r["error"] for r in results):
        print("\nExceptions:")
        for r in results:
            if r["error"]:
                print(f"  {r['id']}: {r['error']}")

    # ── Report
    REPORTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = {
        "timestamp": timestamp,
        "n_cases": len(results),
        "overall_pass": overall[0],
        "tool_selection_pass_rate": tools_pr,
        "argument_accuracy_pass_rate": args_pr,
        "tool_status_pass_rate": status_pr,
        "answer_text_pass_rate": text_pr,
        "fact_check_pass_rate": fact_pr,
        "latency_ms": {"median": statistics.median(latencies) if latencies else None, "all": latencies},
        "avg_tool_hops": statistics.mean(hops) if hops else None,
        "tokens": {"input": total_in, "output": total_out},
        "estimated_cost_usd": round(est_cost, 4),
        "results": results,
    }
    out_path = REPORTS_DIR / f"{timestamp}.json"
    out_path.write_text(json.dumps(report, indent=2, default=str))
    (REPORTS_DIR / "latest.json").write_text(json.dumps(report, indent=2, default=str))
    print(f"\nReport written to {out_path}")


if __name__ == "__main__":
    main()
