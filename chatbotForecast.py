# chatbotForecast.py
"""Streamlit chatbot that answers building-sensor questions by generating and
executing Cypher against a Neo4j graph (GraphCypherQAChain), with a hardcoded
fallback path for forecast-style questions."""
import json
import os
import re
import time
from datetime import datetime, timezone

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_community.graphs import Neo4jGraph
from langchain_community.chains.graph_qa.cypher import GraphCypherQAChain
from langchain.prompts import PromptTemplate

try:
    from pyvis.network import Network
    HAS_PYVIS = True
except ModuleNotFoundError:
    HAS_PYVIS = False

load_dotenv()

# ─────────────────────────────────────────
# 1a.  ENV VALIDATION & PROVIDER SELECTION
# ─────────────────────────────────────────
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").lower()

_DEFAULT_MODELS = {
    "openai": "gpt-4",
    "gemini": "gemini-3.8-flash",
    "groq": "llama-3.3-70b-versatile",
}
_PROVIDER_API_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "gemini": "GOOGLE_API_KEY",
    "groq": "GROQ_API_KEY",
}

LLM_MODEL_NAME = os.getenv("LLM_MODEL") or _DEFAULT_MODELS.get(LLM_PROVIDER)


def require_env_vars() -> None:
    """Fail fast with one clear message instead of a library stack trace
    three layers deep inside Neo4jGraph/ChatOpenAI construction."""
    required = ["NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD"]
    key_env = _PROVIDER_API_KEY_ENV.get(LLM_PROVIDER)
    if key_env:
        required.append(key_env)
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(
            "Missing required environment variable(s): " + ", ".join(missing) +
            ". Copy .env.template to .env and fill them in."
        )

# ─────────────────────────────────────────
# 1.  SCHEMA & PROMPT  (pure strings, no side effects — safe to import)
# ─────────────────────────────────────────
schema = """
Node Labels:
- Room: properties room_number (string, e.g. '101'), type ('dorm' or 'mechanical'). Only dorm rooms (101-106) have sensors; mechanical rooms (201, 202) do not and only contain AC units.
- AC_Unit: properties ac_id (string, values like 'AC1', 'AC2')
- Sensor: properties sensor_id (string, formatted 'OCC_<room_number>' for occupancy sensors or 'TEMP_<room_number>' for temperature sensors, e.g. 'TEMP_101'), sensor_type ('occupancy' or 'temperature')
- Reading: properties timestamp (datetime), value (float — temperature reading, or 0/1 for occupancy), sensor_type ('occupancy' or 'temperature', matches the recording sensor), room_number (string, matches the recording sensor's room)

Relationships:
- (Room)-[:CONTAINS]->(AC_Unit): Mechanical rooms contain AC units.
- (AC_Unit)-[:SERVICES]->(Room): AC units service dorm rooms.
- (Room)-[:HAS_SENSOR]->(Sensor): Rooms have sensors.
- (Sensor)-[:REPORTS_TO]->(AC_Unit): Temperature sensors report to their AC unit.
- (Sensor)-[:RECORDED]->(Reading): Sensors recorded readings over time.
"""
# Few shots covering the common question patterns (synonym mapping, latest reading,
# per-room-latest aggregation, and point-in-time lookups) so the LLM has a template
# for each instead of inventing multi-hop Cypher from the schema alone.
few_shot = """Examples:

User: Which rooms have AC1?
MATCH (a:AC_Unit {{ac_id:'AC1'}})-[:SERVICES]->(r:Room)
RETURN r.room_number

User: What rooms are serviced by air conditioning unit 2?
MATCH (a:AC_Unit {{ac_id:'AC2'}})-[:SERVICES]->(r:Room)
RETURN r.room_number

User: What is the current temperature in room 101?
MATCH (r:Room {{room_number:'101'}})-[:HAS_SENSOR]->(s:Sensor {{sensor_type:'temperature'}})-[:RECORDED]->(rd:Reading)
RETURN rd.value AS temperature, rd.timestamp AS as_of
ORDER BY rd.timestamp DESC
LIMIT 1

User: Which room is currently the hottest?
MATCH (r:Room)-[:HAS_SENSOR]->(s:Sensor {{sensor_type:'temperature'}})-[:RECORDED]->(rd:Reading)
WITH r, rd ORDER BY rd.timestamp DESC
WITH r, collect(rd)[0] AS latest
RETURN r.room_number AS room, latest.value AS temperature
ORDER BY latest.value DESC
LIMIT 1

User: Is room 103 occupied right now?
MATCH (r:Room {{room_number:'103'}})-[:HAS_SENSOR]->(s:Sensor {{sensor_type:'occupancy'}})-[:RECORDED]->(rd:Reading)
RETURN rd.value AS occupied, rd.timestamp AS as_of
ORDER BY rd.timestamp DESC
LIMIT 1
"""

full_prompt_str = f"""
You are a Cypher query generator for Neo4j.

The database schema is:

{schema}

Guidelines:
•  When the user says "air conditioning unit N", "AC N", "acN", "ac N", etc., map it to ac_id = 'ACN'.
•  Use relationship directions exactly as shown.
•  Use correct property names (e.g. ac_id, room_number, sensor_id).
•  If the question can't be answered with the schema, reply ONLY: "Cannot answer with the current schema."
•  Output **only** the Cypher statement - no prefixes, no code fences.
{few_shot}

Now answer **this question** (generate only Cypher, no prose):
Question: {{query}}
"""

custom_prompt = PromptTemplate.from_template(full_prompt_str)

FORECAST_WORDS = re.compile(
    r"\b(forecasts?|forecasting|predict(s|ing|ion|ions)?|projections?|trends?|trending)\b",
    re.I,
)

MAX_CYPHER_RETRIES = 1

# Blocks write clauses in any Cypher before it reaches the database. Needed
# because GraphCypherQAChain executes LLM-generated Cypher *internally*
# (self.graph.query(...) inside its _call()) before ask_graph ever sees it,
# so a check placed only in our own fallback-execution code would be too
# late. Aura's free tier has no per-role access control (that's an Enterprise
# feature), so this is the only read-only enforcement available without
# upgrading or self-hosting — it's app-level, not a substitute for a real
# read-only DB role if one becomes available.
WRITE_CLAUSE_RE = re.compile(r"\b(CREATE|MERGE|DELETE|SET|REMOVE|DROP)\b", re.I)


def ensure_read_only_cypher(cypher_query: str) -> None:
    if WRITE_CLAUSE_RE.search(cypher_query):
        raise PermissionError(
            f"Refusing to execute a non-read-only Cypher statement: {cypher_query!r}"
        )


# ─────────────────────────────────────────
# 2.  RESOURCES
# Cached so Streamlit builds each one once per process instead of
# reconnecting to Neo4j and rebuilding the chain on every rerun.
# ─────────────────────────────────────────
@st.cache_resource
def get_graph() -> Neo4jGraph:
    graph = Neo4jGraph(
        url=os.getenv("NEO4J_URI"),
        username=os.getenv("NEO4J_USERNAME"),
        password=os.getenv("NEO4J_PASSWORD"),
    )

    unguarded_query = graph.query

    def guarded_query(cypher_query, *args, **kwargs):
        ensure_read_only_cypher(cypher_query)
        return unguarded_query(cypher_query, *args, **kwargs)

    graph.query = guarded_query  # instance-level override; every caller (the
    # chain's internal execution, ask_graph's fallback execution, and main()'s
    # own hardcoded queries) goes through this same guarded method.
    return graph


def _build_llm():
    """Pick the chat model from LLM_PROVIDER (openai/gemini/groq) so the same
    chain/prompt can be benchmarked across providers without touching the app
    logic. The non-default providers are imported lazily so you don't need
    their packages installed unless you actually use them."""
    model = LLM_MODEL_NAME

    if LLM_PROVIDER == "openai":
        return ChatOpenAI(model=model, temperature=0, openai_api_key=os.getenv("OPENAI_API_KEY"))

    if LLM_PROVIDER == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=model, temperature=0, google_api_key=os.getenv("GOOGLE_API_KEY"))

    if LLM_PROVIDER == "groq":
        from langchain_groq import ChatGroq
        return ChatGroq(model=model, temperature=0, groq_api_key=os.getenv("GROQ_API_KEY"))

    raise ValueError(f"Unknown LLM_PROVIDER '{LLM_PROVIDER}'. Use one of: {', '.join(_DEFAULT_MODELS)}")


@st.cache_resource
def get_chain() -> GraphCypherQAChain:
    llm = _build_llm()
    return GraphCypherQAChain.from_llm(
        llm=llm,
        graph=get_graph(),
        cypher_prompt=custom_prompt,  # add custom prompt to allow more flexible question
        top_k=50,
        execute_on_graph=True,   # execution
        allow_dangerous_requests=True,  # allow the direct access to the database
        validate_cypher=True,    # catch hallucinated relationship directions before executing
        return_intermediate_steps=True,  # the chain never populates a "cypher"
        # key in its result dict — the generated Cypher only appears in
        # intermediate_steps[0]["query"], and only if this is True. Without
        # it, resp.get("cypher") below is always None.
        verbose=True,
    )


# ─────────────────────────────────────────
# 3.  CORE ANSWERING LOGIC
# Dependency-injected (chain/graph passed in, not read from globals) so it can
# be unit-tested with fakes — no Streamlit runtime, no live Neo4j/OpenAI needed.
# ─────────────────────────────────────────
def _looks_like_apology(text) -> bool:
    return isinstance(text, str) and text.lower().startswith(("i'm sorry", "cannot answer"))


def _extract_generated_cypher(resp: dict) -> str:
    for step in resp.get("intermediate_steps") or []:
        if "query" in step:
            return step["query"]
    return ""


def ask_graph(
    question: str,
    chain: GraphCypherQAChain,
    graph: Neo4jGraph,
    max_retries: int = MAX_CYPHER_RETRIES,
    on_attempt=None,
):
    """Run the Cypher QA chain, retrying once by feeding the failure back to the LLM
    instead of silently forcing execution or swallowing the failure.

    on_attempt, if given, is called as on_attempt(attempt_index, cypher, error)
    after each attempt (error is None on success) — used to log per-attempt
    detail without changing this function's (cypher, result, error) return
    shape that callers and tests already rely on.
    """
    attempt_q = question
    cypher, error = "", None

    for attempt in range(max_retries + 1):
        try:
            resp = chain.invoke({"query": attempt_q})
        except Exception as e:
            resp, error = {}, str(e)

        cypher_raw = _extract_generated_cypher(resp)
        cypher = cypher_raw.strip() if cypher_raw else ""
        cypher = re.sub(r"'\s*([\w-]+)\s*'", r"'\1'", cypher)

        answer_raw = resp.get("result")
        answer = "" if _looks_like_apology(answer_raw) else (answer_raw or "")

        if answer:
            if on_attempt:
                on_attempt(attempt, cypher, None)
            return cypher, answer, None

        rows = None
        if cypher and cypher.lower().startswith(("match", "with", "call")):
            try:
                rows = graph.query(cypher)
            except Exception as e:
                error = str(e)
            if rows:
                if on_attempt:
                    on_attempt(attempt, cypher, None)
                return cypher, rows, None
            error = error or "Query executed but returned 0 rows."
        else:
            error = error or "The model did not produce a usable Cypher query."

        if on_attempt:
            on_attempt(attempt, cypher, error)

        if attempt < max_retries:
            attempt_q = (
                f"{question}\n\n"
                f"(A previous attempt generated this Cypher, which failed: {error}\n"
                f"Previous Cypher:\n{cypher or '(none)'}\n"
                f"Re-check the schema and fix the query.)"
            )

    return cypher, None, error


# ─────────────────────────────────────────
# 4.  OBSERVABILITY
# One JSONL record per question — the data source for building an accuracy
# eval set and comparing providers later, not just a debug print.
# ─────────────────────────────────────────
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "qa_log.jsonl")


def log_interaction(entry: dict) -> None:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ─────────────────────────────────────────
# 5.  STREAMLIT UI
# Only runs under `streamlit run chatbotForecast.py` — importing this module
# (e.g. from a test) no longer builds a chain or opens a Neo4j connection.
# ─────────────────────────────────────────
def main():
    st.set_page_config("Building Sensor Chatbot", "🏢")

    try:
        require_env_vars()
    except RuntimeError as e:
        st.error(str(e))
        st.stop()

    graph = get_graph()
    chain = get_chain()

    st.title("🏢 Sensor Graph")

    col_q, col_btn = st.columns([3, 1])
    user_q = col_q.text_input("Ask about rooms, AC units, or sensors:")

    # ─────────────────────────────────────────
    # 5a.  Quick graph preview button
    # ─────────────────────────────────────────
    if col_btn.button("🔎 Preview Graph"):

        if not HAS_PYVIS:
            st.error("Install`pyvis`(`pip install pyvis`) to enable the preview.")
        else:
            with st.spinner("Loading…"):
                data = graph.query(
                    """
                    MATCH (a)-[r]->(b)
                    RETURN elementId(a) AS a_id, labels(a)[0] AS a_lab, properties(a) AS a_p,
                           elementId(b) AS b_id, labels(b)[0] AS b_lab, properties(b) AS b_p,
                           type(r)      AS r_type
                    LIMIT 50
                    """
                )

                net = Network(height="600px", bgcolor="#222", directed=True)
                for rec in data:
                    net.add_node(rec["a_id"], label=rec["a_lab"], title=str(rec["a_p"]))
                    net.add_node(rec["b_id"], label=rec["b_lab"], title=str(rec["b_p"]))
                    net.add_edge(rec["a_id"], rec["b_id"], label=rec["r_type"])

                net.save_graph("mini_graph.html")
                st.components.v1.html(open("mini_graph.html").read(), height=620, scrolling=True)

    # ─────────────────────────────────────────
    # 5b.  Handle the user question
    # ─────────────────────────────────────────
    if user_q:
        try:
            # ────────────────────────────────
            # Forecast‑style queries
            # ────────────────────────────────
            if FORECAST_WORDS.search(user_q):

                st.warning("⛅️Forecasting isn't a pure Cypher lookup; fetching history…")

                with st.spinner("Querying Neo4j…"):
                    rows = graph.query(
                        """
                        MATCH (d:Room)-[:HAS_SENSOR]->(:Sensor {sensor_type:'occupancy'})
                              -[:RECORDED]->(m:Reading {sensor_type:'occupancy'})
                        RETURN d.room_number AS room,
                               toString(m.timestamp) AS ts,
                               m.value               AS occ   // 0=vacant, 1=occupied
                        """
                    )

                if not rows:
                    st.error("No occupancy data found.")
                    st.stop()  # safe: no spinner currently open

                # ── 1.  Put into DataFrame
                df = pd.DataFrame(rows)
                df["ts"] = pd.to_datetime(df["ts"])
                df.sort_values(["room", "ts"], inplace=True)

                # ── 2.  Current occupancy (latest reading per room)
                latest = (
                    df.groupby("room").tail(1)[["room", "occ", "ts"]]
                    .rename(columns={"occ": "current_occ"})
                )

                occupied_now = latest[latest["current_occ"] == 1]["room"].tolist()
                vacant_now   = latest[latest["current_occ"] == 0]["room"].tolist()

                st.subheader("📌 Current status (most recent reading)")
                col_occ, col_vac = st.columns(2)
                with col_occ:
                    st.success("Occupied now")
                    st.write(occupied_now or "—")
                with col_vac:
                    st.info("Vacant now")
                    st.write(vacant_now or "—")

                # ── 3.  Very‑naïve hourly forecast for the next hour
                df["hour_of_day"] = df["ts"].dt.hour
                prob = (
                    df.groupby(["room", "hour_of_day"])["occ"]
                    .mean()                      # probability of being occupied at that hour
                    .reset_index(name="p_occ")
                )

                next_hour = pd.Timestamp.utcnow().round("H") + pd.Timedelta(hours=1)
                h = next_hour.hour
                likely_occ = prob[
                    (prob["hour_of_day"] == h) & (prob["p_occ"] >= 0.5)
                ]["room"].tolist()

                st.subheader(f"🔮 Likely occupied at {next_hour:%Y-%m-%d %H:00 UTC}")
                st.write(likely_occ or "No room crosses the 50% probability threshold for the coming hour.")

                # ── 4.  Let the user download the full history
                st.subheader("📥 Raw occupancy history")
                st.dataframe(df.head())
                csv = df.to_csv(index=False).encode()
                st.download_button("⬇ Download CSV", csv, "occupancy_history.csv", "text/csv")

            # ────────────────────────────────
            # Plain‑Cypher questions
            # ────────────────────────────────
            else:
                attempts_log = []
                start_time = time.time()
                with st.spinner("Thinking…"):
                    cypher, result, error = ask_graph(
                        user_q, chain, graph,
                        on_attempt=lambda i, c, e: attempts_log.append(
                            {"attempt": i, "cypher": c, "error": e}
                        ),
                    )

                log_interaction({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "question": user_q,
                    "provider": LLM_PROVIDER,
                    "model": LLM_MODEL_NAME,
                    "outcome": "success" if result else "failed",
                    "final_cypher": cypher,
                    "final_error": error,
                    "retries": max(len(attempts_log) - 1, 0),
                    "latency_ms": round((time.time() - start_time) * 1000),
                    "attempts": attempts_log,
                })

                shown_cypher = cypher if cypher else "(no Cypher generated)"
                with st.expander(" Generated Cypher"):
                    st.code(shown_cypher, language="cypher")

                if result:
                    st.success("Answer:")
                    if isinstance(result, list):
                        try:
                            st.dataframe(pd.DataFrame(result))
                        except Exception:
                            st.write(result)
                    else:
                        st.write(result)
                elif error:
                    st.warning(f" Couldn't get a confident answer: {error}")
                else:
                    st.info("(no rows returned)")

        # closes the big `try:` that started earlier
        except Exception as err:
            st.error(f" Something went wrong:\n\n{err}")


if __name__ == "__main__":
    main()
