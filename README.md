# KG‑FOR‑MECH  
_A knowledge‑graph–driven demo chatbot for mechanical‑system monitoring_


This repo shows how to combine **Neo4j**, **LangChain**, and **Streamlit** to build a chatbot that answers questions about rooms, AC units, temperatures, and occupancy—all backed by a graph of sensors and readings. The LLM is swappable (`LLM_PROVIDER=openai|gemini|groq|anthropic`), and the sensor backend can be either the bundled CSVs or a local TimescaleDB instance (`SENSOR_BACKEND=csv|timescaledb`) — see `.env.template`.

Two Streamlit apps here take genuinely different approaches to the same problem — see [`tool-calling-agent/`](./tool-calling-agent) for a third (a typed tool-calling agent, no LangChain) and [the comparison write-up](https://icy-mint.github.io/building-graph-chatbot/) for a 107-question benchmark of all three on the same model and database.

---

## 📁 Project Layout

```text
├── archived/                # Old or experimental code
├── lib/                     # (reserved for reusable modules)
├── sensor_outputs/          # Synthetic CSVs with room‑level sensor data
│   └── room_101_timeseries.csv
├── chatbot_classifier.py    # v1 – fixed-intent-classifier chatbot
├── chatbot_nl2cypher.py     # v2 – GraphCypherQAChain Streamlit app (main demo)
├── test_chatbot_classifier.py / test_chatbot_nl2cypher.py  # pytest, no live services needed
├── compare_systems.py       # 3-way benchmark across this repo + tool-calling-agent/
├── compare_systems_results_full.json  # its 107-question results
├── docs/index.html          # the comparison write-up, published via GitHub Pages
├── tool-calling-agent/      # v3 – a third architecture: Claude tool-use, no LangChain (own README, own venv)
├── Graph.cypher             # Schema + seed data for Neo4j
├── Visualize.cypher         # Read-only queries to view the loaded graph
├── SensorDataGeneration.py  # Script to create synthetic sensor CSVs
├── requirements.txt         # Python deps
├── .env.template            # Copy → `.env` and fill in secrets
├── LICENSE
└── README.md                # You’re reading it 🙂
```
###  1 · Load the Knowledge Graph

> **Prerequisite**  Neo4j 5.x (Aura, Desktop, or self‑hosted)

1. **Create a database** and note the Bolt URI, user, and password.  
2. **Run `Graph.cypher`** in Neo4j Browser or `cypher-shell`.

> Success but `MATCH (n) RETURN count(n);` shows 0? Some lightweight editors
> (e.g. Aura's "Query" tab) only run the first statement of a pasted script —
> run `Graph.cypher` block by block instead.

#### Graph schema at‑a‑glance

| Node       | Key properties                                  | Purpose              |
|------------|-------------------------------------------------|----------------------|
| `Room`     | `room_number`, `type` (`dorm` / `mechanical`)   | Physical spaces      |
| `AC_Unit`  | `ac_id`                                         | HVAC equipment       |
| `Sensor`   | `sensor_id`, `sensor_type` (`temperature` / `occupancy`) | Devices              |
| `Reading`  | `timestamp`, `value`, `sensor_type`, `room_number`       | Time‑series values   |

| Relationship                                  | Comment                                  |
|-----------------------------------------------|------------------------------------------|
| `(Room)‑[:HAS_SENSOR]→(Sensor)`               |                                          |
| `(AC_Unit)‑[:SERVICES]→(Room)`                |                                          |
| `(Room)‑[:CONTAINS]→(AC_Unit)`                | *mechanical rooms only*                  |
| `(Sensor)‑[:REPORTS_TO]→(AC_Unit)`            | *temperature, occupancy sensors*         |
| `(Sensor)‑[:RECORDED]→(Reading)`              |                                          |

---

### 📂 Sensor Data Files

This project includes synthetic sensor data used for the graph demo:

- `room_101_timeseries.csv`: Room-level data for timestamp,room_number,sensor_id_occ,sensor_id_temp,occupancy,temperature
- Those data can be generated using SensorDataGeneration.py

### 📂 Environment variable template 
Copy the template using the code below to start build your own knowledge graph:
cp .env.template .env

###  2 · Sanity‑check the Graph

Paste the Cypher snippets below into **Neo4j Browser** (or `cypher-shell`) to
verify that the demo graph is loaded correctly.

---
- `Verify timestamps exist`
MATCH (s:Sensor {sensor_id: "OCC_101"})-[:RECORDED]->(r:Reading)
RETURN r.timestamp, r.value
ORDER BY r.timestamp
LIMIT 10;

- `Verify nodes and relationships exist`
MATCH path = (ac:AC_Unit)-[:SERVICES]->(r:Room)-[:HAS_SENSOR]->(s:Sensor)
RETURN path

- `Verify nodes of data from sensors exist`
MATCH path = (s:Sensor)-[:RECORDED]->(r:Reading)
RETURN path
LIMIT 50

To actually *see* the graph (switch the results panel to the **Graph** view),
run the queries in [`Visualize.cypher`](./Visualize.cypher).

> **Note**  
> The code assumes a Neo4j graph with nodes & relationships exactly as defined in the embedded schema string.

---

##  Tested With

| Tool        | Version |
|-------------|---------|
| **Python**  | 3.9 +   |
| **Neo4j**   | 5.x     |
| **Streamlit** | 1.29+ |
| **LangChain** | <1.0 (pinned -- see `requirements.txt`) |
| **LLM** | OpenAI (default), Gemini, Groq, or Anthropic -- `LLM_PROVIDER` |

---

## Quick One‑Liner (zero‑to‑chatbot)

```bash
pip install -r requirements.txt && \
cp .env.template .env && \
streamlit run chatbot_nl2cypher.py
```

Open the browser tab ➜ ask things like:

    “Which room has the coolest temperature?”

    “What rooms are serviced by AC2?”

    “Forecast occupancy for the next hour.”

You should see the cypher block in your terminal 
```cypher  
MATCH (a:AC_Unit)-[:SERVICES]->(r:Room)
RETURN a.ac_id AS ac_unit,
       collect(r.room_number) AS rooms
ORDER  BY ac_unit;
```

### 📂 Short Description for chatbot_classifier.py

This Streamlit-based chatbot answers building management questions using an LLM classifier (via LangChain) to extract one of ~10 fixed intents (current/all-time hottest/coldest, occupancy, occupancy forecast, AC mapping, fallback) plus a list of room numbers -- it supports multi-room questions like "compare 101 and 104". SensorHelper and GraphHelper handle sensor data (CSV by default, or a local TimescaleDB instance -- `SENSOR_BACKEND`) and Neo4j queries. The system routes questions to helper functions based on LLM-classified intent, with fallback to the LLM for open-ended queries.

## Pros

* Simple, maintainable routing logic.
* LLM handles synonyms and paraphrasing.
* Structured parsing reduces hallucination.
* Efficient hybrid of LLM + Python functions.
* Easily extensible for new actions.

## Cons

* Rigid: pre-defined action labels required.
* Limited for multi-step or complex reasoning.
* LLM only classifies, no dynamic function calling.
* More complex as functions scale.
* No conversation memory or context.

### 📂 Short Description for chatbot_nl2cypher.py

A single‑page **Streamlit** app that converts plain‑English questions about rooms, AC‑units, temperatures, and occupancy into live **Cypher** queries on Neo4j.  
For “forecast‑style” prompts it adds a minimal occupancy‑prediction workflow.

---

##  Features at a glance

| Category                | What it does |
|-------------------------|--------------|
| **Conversational Q&A**  | The configured LLM (`LLM_PROVIDER`; via LangChain **`GraphCypherQAChain`**) auto‑writes Cypher from user text, with `validate_cypher=True` catching hallucinated relationship directions before it runs. |
| **Live graph execution**| Runs the generated Cypher on Neo4j and shows results as a dataframe. |
| **Graph preview**       | “🔎 Preview Graph” button renders a 50‑node PyVis mini‑graph. |
| **Occupancy forecast**  | If the question contains *forecast / predict / trend / projection*:<br>1️⃣ pull historical occupancy readings → 2️⃣ display current occupied / vacant rooms → 3️⃣ compute a naïve next‑hour probability → 4️⃣ offer a CSV download. |
| **Robust error handling** | Retries once on failure with the error fed back to the model; human‑readable error flashes—no more `NoneType.strip()` crashes. |

---

###  How it works (under the hood)

1. **Natural‑language → Cypher**  
   * LangChain’s `GraphCypherQAChain` + the configured LLM (`LLM_PROVIDER=openai|gemini|groq|anthropic`) are primed with a full schema & few‑shot examples.  
   * The model returns a Cypher query string (or “Cannot answer…”), checked against the schema (`validate_cypher=True`) before it runs.

2. **Execute & visualise**  
   * Cypher runs on Neo4j; results render as a Streamlit dataframe.  
   * Optional PyVis preview gives a quick visual of part of the graph.

3. **Occupancy shortcut**  
   * For *forecast / predict / trend / projection* keywords, the app bypasses the LLM and:  
     * pulls historical occupancy readings;  
     * shows current room status;  
     * computes a simple next‑hour probability;  
     * lets users download the raw CSV.

4. **Crash‑safe guards**  
   * `resp.get("cypher")` is always `None` -- `GraphCypherQAChain` never populates it. The generated query is read from `intermediate_steps` instead (`_extract_generated_cypher()`, requires `return_intermediate_steps=True`).  
   * If the chain fails or the answer looks like an apology, `ask_graph()` retries once with the error fed back to the LLM (`MAX_CYPHER_RETRIES`), and falls back to executing the generated Cypher directly if a usable query exists but no answer came back.

---

### Pros

* Conversational—no Cypher knowledge required.  
* Retries once with the error fed back to the model, and falls back to direct Cypher execution if the model apologises instead of answering.  
* Demonstrates blending graph **and** tabular analytics in Streamlit.

### Cons

* Forecast model is deliberately simple (hourly mean).  
* Single‑turn chat; no conversation memory.  
* Requires the Neo4j schema to stay in sync with the hard‑coded prompt.

---

## Testing

```bash
pip install -r requirements.txt
pytest   # 46 offline unit tests across both files (fakes for the LLM/DB clients -- no live services needed)
```

`pytest.ini` excludes `tool-calling-agent/` (its own separate venv/package) so
a bare `pytest` at the repo root only runs `test_chatbot_classifier.py` and
`test_chatbot_nl2cypher.py`.

## Three-way comparison

[`compare_systems.py`](./compare_systems.py) runs both bots above plus
[`tool-calling-agent/`](./tool-calling-agent) -- a third architecture
(Claude tool-use, typed tools, no LangChain) -- against the same local
Neo4j/TimescaleDB and the same model (`claude-sonnet-5`) over 107
questions, with ground truth computed directly from the database rather
than trusted from any of the three systems. Full write-up (method,
results, where each architecture actually breaks):
**[icy-mint.github.io/building-graph-chatbot](https://icy-mint.github.io/building-graph-chatbot/)**.
Raw results: `compare_systems_results_full.json`.
