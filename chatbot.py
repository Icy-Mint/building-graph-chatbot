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
from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_openai import ChatOpenAI
from langchain.chains import LLMChain
from langchain.prompts import PromptTemplate
from langchain.output_parsers import StructuredOutputParser, ResponseSchema

load_dotenv()

SENSOR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sensor_outputs")

# Same provider-swap as chatbotForecast.py, so the two bots can be benchmarked
# on the same LLM without code changes.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").lower()

_DEFAULT_MODELS = {
    "openai": "gpt-4",
    "gemini": "gemini-3.8-flash",
    "groq": "llama-3.3-70b-versatile",
    "anthropic": "claude-sonnet-5",
}
_PROVIDER_API_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "gemini": "GOOGLE_API_KEY",
    "groq": "GROQ_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}

LLM_MODEL_NAME = os.getenv("LLM_MODEL") or _DEFAULT_MODELS.get(LLM_PROVIDER)


def require_env_vars() -> None:
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


def _build_llm():
    """Pick the chat model from LLM_PROVIDER (openai/gemini/groq/anthropic).
    Non-default providers are imported lazily so their packages are only
    needed if used."""
    model = LLM_MODEL_NAME

    if LLM_PROVIDER == "openai":
        return ChatOpenAI(model=model, temperature=0, openai_api_key=os.getenv("OPENAI_API_KEY"))

    if LLM_PROVIDER == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=model, temperature=0, google_api_key=os.getenv("GOOGLE_API_KEY"))

    if LLM_PROVIDER == "groq":
        from langchain_groq import ChatGroq
        return ChatGroq(model=model, temperature=0, groq_api_key=os.getenv("GROQ_API_KEY"))

    if LLM_PROVIDER == "anthropic":
        from langchain_anthropic import ChatAnthropic
        # claude-sonnet-5 rejects `temperature` entirely ("deprecated for this
        # model" -- confirmed live), unlike the other three providers.
        # ChatAnthropic defaults to max_tokens=1024 and omits `thinking`
        # entirely, which lets the model default to extended thinking --
        # confirmed live (in dorm-agent, same model) that thinking alone can
        # consume the whole 1024-token budget before any real output, making
        # responses silently empty. Disabled explicitly for the same reason
        # dorm-agent disables it: this task doesn't need deep reasoning.
        return ChatAnthropic(
            model=model,
            max_tokens=2048,
            thinking={"type": "disabled"},
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
        )

    raise ValueError(f"Unknown LLM_PROVIDER '{LLM_PROVIDER}'. Use one of: {', '.join(_DEFAULT_MODELS)}")


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

    def _latest_row(self, room):
        df = self.tables.get(room)
        if df is None:
            return None
        return df.loc[df.timestamp.idxmax()]

    def current_temp(self, room):
        latest = self._latest_row(room)
        if latest is None:
            return f"No temperature data for room {room}"
        return (f"The current temperature in room {room} is {latest.temperature:.1f} °C "
                f"as of {latest.timestamp.strftime('%Y-%m-%d %H:%M')}.")

    def current_occupancy(self, room):
        latest = self._latest_row(room)
        if latest is None:
            return f"No occupancy data for room {room}"
        status = "occupied" if latest.occupancy == 1 else "vacant"
        return f"Room {room} is currently {status} (as of {latest.timestamp.strftime('%Y-%m-%d %H:%M')})."

    def current_hottest_room(self):
        if not self.tables:
            return "No temperature data available."
        best_room, best = None, None
        for room in self.tables:
            latest = self._latest_row(room)
            if best is None or latest.temperature > best.temperature:
                best_room, best = room, latest
        return (f"Room {best_room} is currently the hottest at {best.temperature:.1f} °C "
                f"(as of {best.timestamp.strftime('%Y-%m-%d %H:%M')}).")

    def current_coldest_room(self):
        if not self.tables:
            return "No temperature data available."
        best_room, best = None, None
        for room in self.tables:
            latest = self._latest_row(room)
            if best is None or latest.temperature < best.temperature:
                best_room, best = room, latest
        return (f"Room {best_room} is currently the coldest at {best.temperature:.1f} °C "
                f"(as of {best.timestamp.strftime('%Y-%m-%d %H:%M')}).")

    def occupancy_forecast(self, room=None):
        """Naive next-hour occupancy forecast: probability of being occupied
        at the upcoming hour, averaged from history at that hour-of-day.
        Hour-of-day only (no day-of-week split) -- mirrors chatbotForecast.py's
        forecast approach so the two architectures can be compared on equal
        footing rather than one having a richer forecasting method by
        accident of implementation."""
        next_hour = (pd.Timestamp.now("UTC") + pd.Timedelta(hours=1)).hour

        if room is not None:
            df = self.tables.get(room)
            if df is None:
                return f"No occupancy data for room {room}"
            at_hour = df[df.timestamp.dt.hour == next_hour]
            if at_hour.empty:
                return f"No historical data for room {room} at hour {next_hour}:00 to forecast from."
            prob = at_hour.occupancy.mean()
            return (f"Room {room} has a {prob:.0%} historical probability of being occupied "
                    f"around {next_hour}:00 (the next hour), based on {len(at_hour)} past readings at that hour.")

        likely = []
        for r, df in self.tables.items():
            at_hour = df[df.timestamp.dt.hour == next_hour]
            if not at_hour.empty and at_hour.occupancy.mean() >= 0.5:
                likely.append(r)
        if not likely:
            return f"No room crosses the 50% occupancy probability threshold for the next hour ({next_hour}:00)."
        return f"Rooms likely occupied around {next_hour}:00 (the next hour): {', '.join(sorted(likely))}."


# ─────────────────────────────────────────
# PROMPTS (pure strings/objects, no side effects — safe to import)
# ─────────────────────────────────────────
response_schemas = [
    ResponseSchema(name="action", description=(
        "One of: current_hottest, current_coldest, current_temp, alltime_hottest, "
        "alltime_coldest, current_occupancy, occupancy_pattern, occupancy_forecast, "
        "ac_mapping, fallback"
    )),
    ResponseSchema(name="room", description="Room number if mentioned, else null")
]
parser = StructuredOutputParser.from_response_schemas(response_schemas)

classification_prompt = PromptTemplate(
    template="""
You are a smart assistant for building management.
Classify the user question into one of the following actions. Default to the
current_* actions unless the user explicitly says "ever", "historically",
"all-time", or "record" — "hottest"/"coldest" with no such qualifier means
"right now", not an all-time extreme.

- current_hottest: which room is hottest right now (no specific room, present tense)
- current_coldest: which room is coldest right now
- current_temp: the current/right-now temperature in a SPECIFIC room
- alltime_hottest: the highest temperature EVER recorded (explicitly historical)
- alltime_coldest: the lowest temperature ever recorded (explicitly historical)
- current_occupancy: whether a SPECIFIC room is occupied right now (yes/no status)
- occupancy_pattern: when a room is typically/usually occupied (a pattern across hours, not current status)
- occupancy_forecast: a FUTURE/predicted occupancy question (e.g. "will", "forecast", "predict", "next hour") -- not current or historical status
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
    return LLMChain(llm=_build_llm(), prompt=classification_prompt)


@st.cache_resource
def get_fallback_chain() -> LLMChain:
    return LLMChain(llm=_build_llm(), prompt=fallback_prompt)


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
def ask(query, classification_chain, fallback_chain, parser, graph_helper, sensor_helper, usage_callback=None):
    """Classify the question into a fixed action set, then dispatch to pandas
    (sensor CSVs) or a hardcoded Cypher query. Returns (answer, action, room);
    action/room are None if classification itself failed.

    usage_callback, if given, is called once with a {model: {input_tokens,
    output_tokens, ...}} dict (aggregated across every LLM call this turn
    made -- classification, and the fallback chain if it ran) before
    returning, mirroring the on_attempt pattern in chatbotForecast.ask_graph:
    extra observability without changing this function's existing
    (answer, action, room) return shape that callers and tests rely on.
    """
    cb = UsageMetadataCallbackHandler()

    try:
        parsed = parser.parse(classification_chain.run(question=query, callbacks=[cb]))
    except Exception as e:
        if usage_callback:
            usage_callback(cb.usage_metadata)
        return f"❌ Failed to classify query: {e}", None, None

    action = parsed.get("action")
    room = parsed.get("room")
    room = str(room) if room is not None else None  # the LLM sometimes returns
    # room as a bare JSON number instead of a string; sh.tables is keyed by
    # strings, so an un-normalized int room would silently fail the `in`
    # check below and fall through to "all rooms" instead of erroring.

    try:
        if action == "current_hottest":
            answer = sensor_helper.current_hottest_room()

        elif action == "current_coldest":
            answer = sensor_helper.current_coldest_room()

        elif action == "current_temp":
            if room and room in sensor_helper.tables:
                answer = sensor_helper.current_temp(room)
            else:
                answer = "\n".join(sensor_helper.current_temp(r) for r in sensor_helper.tables)

        elif action == "alltime_hottest":
            if room and room in sensor_helper.tables:
                answer = sensor_helper.hottest(room)
            else:
                answer = "\n".join(sensor_helper.hottest(r) for r in sensor_helper.tables)

        elif action == "alltime_coldest":
            if room and room in sensor_helper.tables:
                answer = sensor_helper.coldest(room)
            else:
                answer = "\n".join(sensor_helper.coldest(r) for r in sensor_helper.tables)

        elif action == "current_occupancy":
            if room and room in sensor_helper.tables:
                answer = sensor_helper.current_occupancy(room)
            else:
                answer = "\n".join(sensor_helper.current_occupancy(r) for r in sensor_helper.tables)

        elif action == "occupancy_pattern":
            if room and room in sensor_helper.tables:
                answer = sensor_helper.occupancy_pattern(room)
            else:
                answer = "\n".join(sensor_helper.occupancy_pattern(r) for r in sensor_helper.tables)

        elif action == "occupancy_forecast":
            if room and room in sensor_helper.tables:
                answer = sensor_helper.occupancy_forecast(room)
            else:
                answer = sensor_helper.occupancy_forecast()

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
            answer = fallback_chain.run(question=query, callbacks=[cb])

    except Exception as e:
        if usage_callback:
            usage_callback(cb.usage_metadata)
        return f"❌ Failed to answer query: {e}", action, room

    if usage_callback:
        usage_callback(cb.usage_metadata)
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
            usage = {}
            with st.spinner("Thinking..."):
                start_time = time.time()
                answer, action, room = ask(
                    query, classification_chain, fallback_chain, parser, graph_helper, sensor_helper,
                    usage_callback=usage.update,
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
                "usage": usage,
            })

            if _is_error(answer):
                st.error(answer)
            else:
                st.success(answer)


if __name__ == "__main__":
    main()
