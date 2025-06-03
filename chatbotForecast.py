# chatbotForecast.py
import os, re, pandas as pd, streamlit as st
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_community.graphs import Neo4jGraph
from langchain_community.chains.graph_qa.cypher import GraphCypherQAChain
from langchain.prompts import PromptTemplate

# Add a better system prompt that includes the schema
from langchain.prompts import PromptTemplate


try:
    from pyvis.network import Network
    HAS_PYVIS = True
except ModuleNotFoundError:
    HAS_PYVIS = False

# ─────────────────────────────────────────
# 1.  ENV & OBJECTS
# ─────────────────────────────────────────
load_dotenv()
graph = Neo4jGraph(
    url=os.getenv("NEO4J_URI"),
    username=os.getenv("NEO4J_USERNAME"),
    password=os.getenv("NEO4J_PASSWORD"),
)

llm = ChatOpenAI(
    model="gpt-4",
    temperature=0,
    openai_api_key=os.getenv("OPENAI_API_KEY"),
)
# add database schema to guide the cipher generation 
schema = """
Node Labels:
- Room: properties room_number (string), type ('dorm' or 'mechanical')
- AC_Unit: properties ac_id (string, values like 'AC1', 'AC2')
- Room (property: room_number, example values: '101', '102', '103')
- Sensor: properties sensor_id (string), sensor_type ('occupancy' or 'temperature')
- Reading: properties timestamp, value

Relationships:
- (Room)-[:CONTAINS]->(AC_Unit): Mechanical rooms contain AC units.
- (AC_Unit)-[:SERVICES]->(Room): AC units service dorm rooms.
- (Room)-[:HAS_SENSOR]->(Sensor): Rooms have sensors.
- (Sensor)-[:REPORTS_TO]->(AC_Unit): Temperature sensors report to their AC unit.
- (Sensor)-[:RECORDED]->(Reading) : Reading reported by sensors 
"""
# Put few shots for LLM synonym mapping 
few_shot = """Examples (AC-unit synonyms):
User: Which rooms have AC1?
MATCH (a:AC_Unit {{ac_id:'AC1'}})-[:SERVICES]->(r:Room)
RETURN r.room_number

User: What rooms are serviced by air conditioning unit 2?
MATCH (a:AC_Unit {{ac_id:'AC2'}})-[:SERVICES]->(r:Room)
RETURN r.room_number
"""

# ---- build one resolved string, but keep {{question}} placeholder ----------
full_prompt_str = f"""
You are a Cypher query generator for Neo4j.

The database schema is:

{schema} 

Guidelines:
•  When the user says "air conditioning unit N", "AC N", "acN", "ac N", etc., map it to ac_id = 'ACN'.
•  Use relationship directions exactly as shown.
•  Use correct property names (e.g. ac_id, room_number, sensor_id).
•  If the question can't be answered with the schema, reply ONLY: "Cannot answer with the current schema."
•  Output **only** the Cypher statement – no prefixes, no code fences.
{few_shot}

Now answer **this question** (generate only Cypher, no prose):
Question: {{query}}
"""

custom_prompt = PromptTemplate.from_template(full_prompt_str)

# Build chain with strong schema injection
chain = GraphCypherQAChain.from_llm(
    llm=llm,
    graph=graph,
    cypher_prompt=custom_prompt,  # add custom prompt to allow more flexible question 
    top_k=50,
    execute_on_graph=True,   # execution 
    allow_dangerous_requests=True,  # allow the direct access to the database
    verbose=True,
)


FORECAST_WORDS = re.compile(r"\b(forecast|predict|projection|trend)\b", re.I)

# ─────────────────────────────────────────
# 2.  STREAMLIT UI
# ─────────────────────────────────────────
st.set_page_config("Building Sensor Chatbot", "🏢")
st.title("🏢 Sensor Graph")

col_q, col_btn = st.columns([3, 1])
user_q = col_q.text_input("Ask about rooms, AC units, or sensors:")

# ─────────────────────────────────────────
# 2a.  Quick graph preview button
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
# 3.  Handle the user question
# ─────────────────────────────────────────
if user_q:
    try:
        # ────────────────────────────────
        # 3a.  Forecast‑style queries
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

            st.subheader(f"🔮 Likely occupied at {next_hour:%Y-%m-%d %H:00 UTC}")
            st.write(likely_occ or "No room crosses the 50% probability threshold for the coming hour.")

            # ── 4.  Let the user download the full history
            st.subheader("📥 Raw occupancy history")
            st.dataframe(df.head())
            csv = df.to_csv(index=False).encode()
            st.download_button("⬇ Download CSV", csv, "occupancy_history.csv", "text/csv")

        # ────────────────────────────────
        # 3b.  Plain‑Cypher questions
        # ────────────────────────────────
        else:
            with st.spinner("Thinking…"):
                resp = chain.invoke({"query": user_q})

            cypher  = resp.get("cypher")
            answer  = resp.get("result", "")

            shown_cypher = cypher.strip() if cypher else "(executed directly — no raw Cypher returned)"
            with st.expander(" Generated Cypher"):
                st.code(shown_cypher, language="cypher")

            if answer:
                st.success("Answer:")
                if isinstance(answer, list):
                    try:
                        st.dataframe(pd.DataFrame(answer))
                    except Exception:
                        st.write(answer)
                else:
                    st.write(answer)
            else:
                if cypher and cypher.lower().startswith(("match", "with", "call")):
                    rows = graph.query(cypher)
                    if rows:
                        st.success("Answer:")
                        st.dataframe(pd.DataFrame(rows))
                    else:
                        st.info("(no rows returned)")
                else:
                    st.warning(" The LLM says the question can’t be answered with the current schema.")

    # closes the big `try:` that started earlier
    except Exception as err:
        st.error(f" Something went wrong:\n\n{err}")
