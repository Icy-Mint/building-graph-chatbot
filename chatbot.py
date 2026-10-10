# chatbot.py
"""Streamlit chatbot (v1) that classifies a question into a fixed action set
via an LLM, then answers either from local sensor CSVs (pandas) or a single
hardcoded Cypher query against Neo4j. Unlike chatbotForecast.py, no
LLM-generated Cypher ever executes here — the only Cypher is the fixed
ac_mapping query below."""
import json
import os
import time
from datetime import datetime, timezone

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from neo4j import GraphDatabase
from langchain_openai import ChatOpenAI
from langchain.chains import LLMChain
from langchain.prompts import PromptTemplate
from langchain.output_parsers import StructuredOutputParser, ResponseSchema

load_dotenv()

SENSOR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sensor_outputs")


def require_env_vars() -> None:
    required = ["NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "OPENAI_API_KEY"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(
            "Missing required environment variable(s): " + ", ".join(missing) +
            ". Copy .env.template to .env and fill them in."
        )


# ─────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────
class GraphHelper:
    def __init__(self, driver):
        self._driver = driver

    def __call__(self, cypher: str):
        try:
            with self._driver.session() as s:
                return [r.data() for r in s.run(cypher)]
        except Exception as e:
            return f"⚠️ Cypher error : {e}"


class SensorHelper:
    def __init__(self, folder=SENSOR_DIR):
        self.tables = {
            f.split("_")[1]: pd.read_csv(os.path.join(folder, f), parse_dates=["timestamp"])
            for f in os.listdir(folder) if f.endswith(".csv")
        }

    def hottest(self, room):
        df = self.tables.get(room)
        if df is None:
            return f"No temperature data for room {room}"
        peak = df.loc[df.temperature.idxmax()]
        return (f"Room {room} peaked at {peak.temperature:.1f} °C "
                f"on {peak.timestamp.strftime('%Y-%m-%d %H:%M')}")

    def occupancy_pattern(self, room):
        df = self.tables.get(room)
        if df is None:
            return f"No occupancy data for room {room}"
        occ_by_hour = df[df.occupancy == 1]["timestamp"].dt.hour.value_counts().sort_index()
        if occ_by_hour.empty:
            return f"No occupancy detected in room {room}"
        hours = ", ".join(str(h) + ":00" for h in occ_by_hour.index)
        return f"Room {room} is typically occupied during: {hours}"

    def coldest(self, room):
        df = self.tables.get(room)
        if df is None:
            return f"No temperature data for room {room}"
        low = df.loc[df.temperature.idxmin()]
        return (f"Room {room} reached lowest temperature {low.temperature:.1f} °C "
                f"on {low.timestamp.strftime('%Y-%m-%d %H:%M')}")


# ─────────────────────────────────────────
# PROMPTS (pure strings/objects, no side effects — safe to import)
# ─────────────────────────────────────────
response_schemas = [
    ResponseSchema(name="action", description="One of: hottest, coldest, occupancy, ac_mapping, fallback"),
    ResponseSchema(name="room", description="Room number if mentioned, else null")
]
parser = StructuredOutputParser.from_response_schemas(response_schemas)

classification_prompt = PromptTemplate(
    template="""
You are a smart assistant for building management.
Classify the user question into one of the following actions:
- hottest: user asks about highest, hottest, max temperature
- coldest: user asks about lowest, coldest, coolest, min temperature
- occupancy: user asks about room usage or occupancy patterns
- ac_mapping: user asks which AC unit services which rooms
- fallback: all other questions

{format_instructions}

User question: {question}
""",
    input_variables=["question"],
    partial_variables={"format_instructions": parser.get_format_instructions()}
)

fallback_prompt = PromptTemplate(
    input_variables=["question"],
    template="""
You're a smart assistant for building management.
Answer the following user question based on building layout and sensor data:

{question}
"""
)


# ─────────────────────────────────────────
# RESOURCES
# Cached so Streamlit builds each one once per process instead of
# reopening the Neo4j driver and re-reading every sensor CSV on every rerun.
# ─────────────────────────────────────────
@st.cache_resource
def get_driver():
    return GraphDatabase.driver(
        os.getenv("NEO4J_URI"),
        auth=(os.getenv("NEO4J_USERNAME"), os.getenv("NEO4J_PASSWORD")),
    )


@st.cache_resource
def get_sensor_helper() -> SensorHelper:
    return SensorHelper()


@st.cache_resource
def get_classification_chain() -> LLMChain:
    llm = ChatOpenAI(openai_api_key=os.getenv("OPENAI_API_KEY"), temperature=0)
    return LLMChain(llm=llm, prompt=classification_prompt)


@st.cache_resource
def get_fallback_chain() -> LLMChain:
    llm = ChatOpenAI(openai_api_key=os.getenv("OPENAI_API_KEY"), temperature=0)
    return LLMChain(llm=llm, prompt=fallback_prompt)


# ─────────────────────────────────────────
# OBSERVABILITY
# Same log shape/location as chatbotForecast.py (different filename) so the
# two bots' logs can be compared directly.
# ─────────────────────────────────────────
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "chatbot_v1_log.jsonl")


def log_interaction(entry: dict) -> None:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ─────────────────────────────────────────
# CORE ANSWERING LOGIC
# Dependency-injected (chains/helpers passed in, not read from globals) so it
# can be unit-tested with fakes — no Streamlit runtime, no live Neo4j/OpenAI.
# ─────────────────────────────────────────
def ask(query, classification_chain, fallback_chain, parser, graph_helper, sensor_helper):
    """Classify the question into a fixed action set, then dispatch to pandas
    (sensor CSVs) or a hardcoded Cypher query. Returns (answer, action, room);
    action/room are None if classification itself failed.
    """
    try:
        parsed = parser.parse(classification_chain.run(question=query))
    except Exception as e:
        return f"❌ Failed to classify query: {e}", None, None

    action = parsed.get("action")
    room = parsed.get("room")
    room = str(room) if room is not None else None  # the LLM sometimes returns
    # room as a bare JSON number instead of a string; sh.tables is keyed by
    # strings, so an un-normalized int room would silently fail the `in`
    # check below and fall through to "all rooms" instead of erroring.

    try:
        if action == "hottest":
            if room and room in sensor_helper.tables:
                answer = sensor_helper.hottest(room)
            else:
                answer = "\n".join(sensor_helper.hottest(r) for r in sensor_helper.tables)

        elif action == "coldest":
            if room and room in sensor_helper.tables:
                answer = sensor_helper.coldest(room)
            else:
                answer = "\n".join(sensor_helper.coldest(r) for r in sensor_helper.tables)

        elif action == "occupancy":
            if room and room in sensor_helper.tables:
                answer = sensor_helper.occupancy_pattern(room)
            else:
                answer = "\n".join(sensor_helper.occupancy_pattern(r) for r in sensor_helper.tables)

        elif action == "ac_mapping":
            result = graph_helper("""
            MATCH (a:AC_Unit)-[:SERVICES]->(r:Room)
            RETURN a.ac_id AS ac_unit, collect(r.room_number) AS rooms
            ORDER BY a.ac_id
            """)
            if not isinstance(result, list):
                answer = result
            elif len(result) == 0:
                answer = "I couldn’t find any AC-unit to room mapping."
            else:
                answer = "\n".join(
                    f"AC unit {row['ac_unit']} serves rooms: {', '.join(map(str, row['rooms']))}."
                    for row in result
                )

        else:
            answer = fallback_chain.run(question=query)

    except Exception as e:
        return f"❌ Failed to answer query: {e}", action, room

    return answer, action, room


def _is_error(answer: str) -> bool:
    return isinstance(answer, str) and answer.startswith(("❌", "⚠️"))


# ─────────────────────────────────────────
# STREAMLIT UI
# Only runs under `streamlit run chatbot.py` — importing this module (e.g.
# from a test) no longer builds a chain, opens a Neo4j driver, or reads CSVs.
# ─────────────────────────────────────────
def main():
    st.set_page_config(page_title="Dorm Building Chatbot", page_icon="🏢", layout="wide")

    try:
        require_env_vars()
    except RuntimeError as e:
        st.error(str(e))
        st.stop()

    driver = get_driver()
    graph_helper = GraphHelper(driver)
    sensor_helper = get_sensor_helper()
    classification_chain = get_classification_chain()
    fallback_chain = get_fallback_chain()

    st.title("🏢 Dorm Building Chatbot")
    st.markdown("Ask me anything about rooms, AC units, temperatures, or occupancy patterns.")

    query = st.text_input("Enter your question:", key="input")

    if st.button("Ask"):
        if query:
            with st.spinner("Thinking..."):
                start_time = time.time()
                answer, action, room = ask(
                    query, classification_chain, fallback_chain, parser, graph_helper, sensor_helper
                )
                latency_ms = round((time.time() - start_time) * 1000)

            log_interaction({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "question": query,
                "action": action,
                "room": room,
                "outcome": "failed" if _is_error(answer) else "success",
                "answer": answer,
                "latency_ms": latency_ms,
            })

            if _is_error(answer):
                st.error(answer)
            else:
                st.success(answer)


if __name__ == "__main__":
    main()
