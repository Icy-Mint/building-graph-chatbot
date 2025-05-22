# chatbotForecast.py
import os, re, pandas as pd, streamlit as st
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_community.graphs import Neo4jGraph
from langchain_community.chains.graph_qa.cypher import GraphCypherQAChain

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

chain = GraphCypherQAChain.from_llm(
    llm=llm,
    graph=graph,
    top_k=50,
    execute_on_graph=False,          # generate only
    allow_dangerous_requests=True,
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
        st.error("Install `pyvis` (`pip install pyvis`) to enable the preview.")
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

            st.warning("⛅️ Forecasting isn't a pure Cypher lookup; fetching history…")

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

            cypher = resp.get("cypher", "").strip()
            answer = resp.get("result", "")

            with st.expander("🧠 Generated Cypher"):
                st.code(cypher or "—", language="cypher")

            if cypher.lower().startswith(("match", "with", "call")):
                rows = graph.query(cypher)
                if rows:
                    st.success("Answer:")
                    st.dataframe(pd.DataFrame(rows))
                else:
                    st.info(answer or "(no rows returned)")
            else:
                st.warning("⚠️Can't be answered with the current schema.")
                if answer:
                    st.info(answer)

    except Exception as err:
        st.error(f"❌ Something went wrong:\n\n{err}")
