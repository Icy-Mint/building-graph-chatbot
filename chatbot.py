import os, glob
import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from neo4j import GraphDatabase
from langchain_openai import ChatOpenAI
from langchain.chains import LLMChain
from langchain.prompts import PromptTemplate
from langchain.output_parsers import StructuredOutputParser, ResponseSchema

# ────────────────────────────────
# 1.  CONFIG
# ────────────────────────────────
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USER = os.getenv("NEO4J_USERNAME")
NEO4J_PASS = os.getenv("NEO4J_PASSWORD")

# ───── Connect to Neo4j ─────
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))

class GraphHelper:
    def __call__(self, cypher: str):
        try:
            with driver.session() as s:
                return [r.data() for r in s.run(cypher)]
        except Exception as e:
            return f"⚠️ Cypher error : {e}"

class SensorHelper:
    def __init__(self, folder="sensor_outputs"):
        self.tables = {
            f.split("_")[1]: pd.read_csv(os.path.join(folder, f), parse_dates=["timestamp"])
            for f in os.listdir(folder) if f.endswith(".csv")
        }

    def hottest(self, room):
        df = self.tables.get(room)
        if df is None:
            return f"No temperature data for room {room}"
        peak = df.loc[df.temperature.idxmax()]
        return (f"Room {room} peaked at {peak.temperature:.1f} °C "
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
        return (f"Room {room} reached lowest temperature {low.temperature:.1f} °C "
                f"on {low.timestamp.strftime('%Y-%m-%d %H:%M')}")


# ───── LLM Setup ─────
llm = ChatOpenAI(openai_api_key=OPENAI_API_KEY, temperature=0)

# Structured output parsing schema
response_schemas = [
    ResponseSchema(name="action", description="One of: hottest, coldest, occupancy, ac_mapping, fallback"),
    ResponseSchema(name="room", description="Room number if mentioned, else null")
]
parser = StructuredOutputParser.from_response_schemas(response_schemas)

# Classification prompt
prompt = PromptTemplate(
    template="""
You are a smart assistant for building management. 
Classify the user question into one of 4 actions: temperature, occupancy, ac_mapping, or fallback.
Also extract room number if present. If not, return null for room.

{format_instructions}

User question: {question}
""",
    input_variables=["question"],
    partial_variables={"format_instructions": parser.get_format_instructions()}
)

classification_chain = LLMChain(llm=llm, prompt=prompt)
fallback_chain = LLMChain(
    llm=llm,
    prompt=PromptTemplate(input_variables=["question"], template="""
You're a smart assistant for building management.
Answer the following user question based on building layout and sensor data:

{question}
""")
)

# ───── Chatbot Entry Point ─────
def ask(query):
    gh = GraphHelper()
    sh = SensorHelper()

    try:
        parsed = parser.parse(classification_chain.run(question=query))
        action = parsed['action']
        room = parsed.get('room')

        if action == "hottest":
            if room and room in sh.tables:
                return sh.hottest(room)
            else:
                return "\n".join([sh.hottest(r) for r in sh.tables.keys()])

        elif action == "coldest":
            if room and room in sh.tables:
                return sh.coldest(room)
            else:
                return "\n".join([sh.coldest(r) for r in sh.tables.keys()])

        elif action == "occupancy":
            if room and room in sh.tables:
                return sh.occupancy_pattern(room)
            else:
                return "\n".join([sh.occupancy_pattern(r) for r in sh.tables.keys()])

        elif action == "ac_mapping":
            result = gh("""
            MATCH (a:AC_Unit)-[:SERVICES]->(r:Room)
            RETURN a.ac_id AS ac_unit, collect(r.room_number) AS rooms
            ORDER BY a.ac_id
            """)
            if not isinstance(result, list):
                return result
            if len(result) == 0:
                return "I couldn’t find any AC-unit to room mapping."
            return "\n".join(
                f"AC unit {row['ac_unit']} serves rooms: {', '.join(map(str, row['rooms']))}."
                for row in result
            )

        else:
            return fallback_chain.run(question=query)

    except Exception as e:
        return f"❌ Failed to classify query: {e}"

# ───── STREAMLIT UI ─────
st.set_page_config(page_title="Dorm Building Chatbot", page_icon="🏢", layout="wide")
st.title("🏢 Dorm Building Chatbot")
st.markdown("Ask me anything about rooms, AC units, temperatures, or occupancy patterns.")

query = st.text_input("Enter your question:", key="input")

if st.button("Ask"):
    if query:
        with st.spinner("Thinking..."):
            try:
                answer = ask(query)
                st.success(answer)
            except Exception as e:
                st.error(f"Something went wrong: {e}")
