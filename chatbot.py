import os, glob
import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from neo4j import GraphDatabase
from langchain.agents import Tool, initialize_agent
from langchain_openai import ChatOpenAI
from langchain.chains import LLMChain
from langchain.prompts import PromptTemplate


# ────────────────────────────────
# 1.  CONFIG
# ────────────────────────────────
load_dotenv()                                    # pull any .env values into env vars
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
            return f"⚠️ Cypher error → {e}"

# ───── Load Time-Series CSVs ─────
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

# ───── LLM Setup (optional fallback) ─────
llm = ChatOpenAI(openai_api_key=OPENAI_API_KEY, temperature=0)
prompt = PromptTemplate(input_variables=["question"], template="""
You're a smart assistant for building management.
Answer the following user question based on building layout and sensor data:

{question}
""")
chain = LLMChain(llm=llm, prompt=prompt)

# ───── Chatbot Entry Point ─────
def ask(query):
    gh = GraphHelper()
    sh = SensorHelper()

    query_lower = query.lower()

    if "hot" in query_lower or "temperature" in query_lower:
        return "\n".join([sh.hottest(room) for room in sh.tables.keys()])

    elif "occupy" in query_lower or "occupied" in query_lower:
        return "\n".join([sh.occupancy_pattern(room) for room in sh.tables.keys()])

    elif "air conditioning" in query_lower or "ac" in query_lower:
        result = gh("""
        MATCH (a:AC_Unit)-[:SERVICES]->(r:Room)
        RETURN a.ac_id AS ac_unit, collect(r.room_number) AS rooms
        ORDER BY a.ac_id
        """)
        if not result:
            return "I couldn’t find any AC-unit → room mapping in the database."
        return "\n".join(
            f"{row['ac_unit']} → Rooms {', '.join(map(str, row['rooms']))}"
            for row in result
        )

    else:
        return chain.run(question=query)



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
