import os, glob
import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from neo4j import GraphDatabase
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain.chains import GraphCypherQAChain, RetrievalQA
from langchain_community.graphs import Neo4jGraph
from langchain_community.vectorstores import Neo4jVector

# ─────────── 1. CONFIG ───────────
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
NEO4J_URI      = os.getenv("NEO4J_URI")
NEO4J_USER     = os.getenv("NEO4J_USERNAME")
NEO4J_PASS     = os.getenv("NEO4J_PASSWORD")

# ─────────── 2. LLM and Graph Setup ───────────
llm = ChatOpenAI(openai_api_key=OPENAI_API_KEY, temperature=0, request_timeout=30)

graph = Neo4jGraph(url=NEO4J_URI, username=NEO4J_USER, password=NEO4J_PASS)
graph.refresh_schema()                 # still a method
schema_str = graph.schema          # now just an attribute

# ---- few‑shot examples to guide the LLM ----
examples = [
    {
        "query":   # ← change!
        "Which rooms are serviced by which Air Conditioning Units?",
        "cypher":
        "MATCH (a:AC_Unit)-[:SERVICES]->(r:Room)\n"
        "RETURN a.ac_id AS ac_unit, collect(r.room_number) AS rooms",
    },
    {
        "query":   # ← change!
        "Which AC unit services room 105?",
        "cypher":
        "MATCH (a:AC_Unit)-[:SERVICES]->(r:Room {room_number:'105'})\n"
        "RETURN a.ac_id AS ac_unit",
    },
]



cypher_chain = GraphCypherQAChain.from_llm(
    llm=llm,
    graph=graph,
    allow_dangerous_requests=True,
    top_k=15,
    verbose=True,
    cypher_examples=examples,
    schema=schema_str              # now points to the right text
)


# ─────────── 3. Optional Vector Index for Sensor QA ───────────
embeddings = OpenAIEmbeddings()

vector_store = Neo4jVector.from_existing_graph(
    url=NEO4J_URI,
    username=NEO4J_USER,
    password=NEO4J_PASS,
    embedding=embeddings,
    index_name="reading_vec",
    node_label="Reading",
    text_node_properties=["value"],
    embedding_node_property="embedding"
)

vector_qa = RetrievalQA.from_chain_type(
    llm=llm,
    retriever=vector_store.as_retriever()
)
# ─────────── 4. Fallback CSV helper ───────────
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
        return (f"Room {room} peaked at {peak.temperature:.1f} °C on {peak.timestamp.strftime('%Y-%m-%d %H:%M')}")

    def occupancy_pattern(self, room):
        df = self.tables.get(room)
        if df is None:
            return f"No occupancy data for room {room}"
        occ_by_hour = df[df.occupancy == 1]["timestamp"].dt.hour.value_counts().sort_index()
        if occ_by_hour.empty:
            return f"No occupancy detected in room {room}"
        hours = ", ".join(str(h) + ":00" for h in occ_by_hour.index)
        return f"Room {room} is typically occupied during: {hours}"

# ─────────── 5. Chatbot Entry Point ───────────
def ask(query: str):
    sh = SensorHelper()

    # 1️⃣  Graph Cypher QA
    try:
        raw = cypher_chain.invoke({"query": query})   # dict
        answer = raw["result"] if isinstance(raw, dict) else raw
        if answer and "i don't know" not in answer.lower():
            return answer
    except Exception as e:
        st.write(f"Graph QA error → {e}")

    # 2️⃣  Vector QA
    try:
        raw = vector_qa.invoke(query)                 # dict
        vec_answer = raw["result"] if isinstance(raw, dict) else raw
        if vec_answer and "i don't know" not in vec_answer.lower():
            return vec_answer
    except Exception as e:
        st.write(f"Vector QA error → {e}")

    # 3️⃣  CSV fall‑backs  ───────────────────────────────────
    ql = query.lower()
    if "hot" in ql or "temperature" in ql:
        return "\n".join(sh.hottest(r) for r in sh.tables)
    if "occupy" in ql or "occupied" in ql:
        return "\n".join(sh.occupancy_pattern(r) for r in sh.tables)

    return "❓ I couldn’t understand that question. Try asking about rooms, AC units, or temperature."

# ─────────── 6. STREAMLIT UI ───────────
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
