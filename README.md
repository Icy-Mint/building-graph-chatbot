# KG‑FOR‑MECH  
_A knowledge‑graph–driven demo chatbot for mechanical‑system monitoring_


This repo shows how to combine **Neo4j**, **LangChain + OpenAI**, and **Streamlit** to build a chatbot that answers questions about rooms, AC units, temperatures, and occupancy—all backed by a graph of sensors and readings.

---

## 📁 Project Layout

```text
├── archived/                # Old or experimental code
├── lib/                     # (reserved for reusable modules)
├── sensor_outputs/          # Synthetic CSVs with room‑level sensor data
│   └── room_101_timeseries.csv
├── chatbot.py               # v1 – intent‑classifier chatbot
├── chatbotForecast.py       # v2 – GraphCypherQAChain Streamlit app (main demo)
├── Graph.cypher             # Schema + seed data for Neo4j
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


> **If it reports success but a later `MATCH` query shows no data:** some
> lightweight query editors (e.g. Aura's embedded "Query" tab) only reliably
> execute one statement per submission, and will silently run just the first
> one (the harmless `MATCH (n) DETACH DELETE n;` wipe) when you paste the
> whole multi-statement file at once. If that happens, run `Graph.cypher`'s
> sections one block at a time instead, checking `MATCH (n) RETURN count(n);`
> after each. The full standalone Neo4j Browser (`browser.neo4j.io`, linked
> from Aura's "Connect" button) handles multi-statement scripts more
> reliably than the embedded editor.

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

- `Visualize the whole graph` (switch the results panel to the **Graph** view)
MATCH (n) RETURN n LIMIT 100

- `Visualize structure only` (skips the high-volume `Reading` nodes so Room/AC_Unit/Sensor relationships are easy to see)
MATCH (a)-[r]->(b) WHERE NOT b:Reading RETURN a, r, b

> **Note**  
> The code assumes a Neo4j graph with nodes & relationships exactly as defined in the embedded schema string.

---

##  Tested With

| Tool        | Version |
|-------------|---------|
| **Python**  | 3.9 +   |
| **Neo4j**   | 5.x     |
| **Streamlit** | 1.29 + |
| **LangChain** | 0.1 + |
| **OpenAI API** | GPT‑4 |

---

## Quick One‑Liner (zero‑to‑chatbot)

```bash
pip install -r requirements.txt && \
cp .env.template .env && \
streamlit run chatbotForecast.py
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

### 📂 Short Description for Chatbot.py

This Streamlit-based chatbot answers building management questions using an LLM classifier (via LangChain) to extract intent (hottest, coldest, occupancy, ac_mapping, fallback) and room number. SensorHelper and GraphHelper handle CSV sensor data and Neo4j queries. The system routes questions to helper functions based on LLM-classified intent, with fallback to LLM for open-ended queries.

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

### 📂 Short Description for chatbotForecast.py

A single‑page **Streamlit** app that converts plain‑English questions about rooms, AC‑units, temperatures, and occupancy into live **Cypher** queries on Neo4j.  
For “forecast‑style” prompts it adds a minimal occupancy‑prediction workflow.

---

##  Features at a glance

| Category                | What it does |
|-------------------------|--------------|
| **Conversational Q&A**  | GPT‑4 (via LangChain **`GraphCypherQAChain`**) auto‑writes Cypher from user text. |
| **Live graph execution**| Runs the generated Cypher on Neo4j and shows results as a dataframe. |
| **Graph preview**       | “🔎 Preview Graph” button renders a 50‑node PyVis mini‑graph. |
| **Occupancy forecast**  | If the question contains *forecast / predict / trend / projection*:<br>1️⃣ pull historical occupancy readings → 2️⃣ display current occupied / vacant rooms → 3️⃣ compute a naïve next‑hour probability → 4️⃣ offer a CSV download. |
| **Robust error handling** | Null‑safe `resp.get()` usage, human‑readable error flashes—no more `NoneType.strip()` crashes. |

---

###  How it works (under the hood)

1. **Natural‑language → Cypher**  
   * LangChain’s `GraphCypherQAChain` + GPT‑4 are primed with a full schema & few‑shot examples.  
   * The model returns a Cypher query string (or “Cannot answer…”).

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
   * `resp.get("cypher") / resp.get("result")` are both null‑safe.  
   * LLM apology messages are detected and trigger a direct Cypher fallback.

---

### Pros

* Conversational—no Cypher knowledge required.  
* Auto‑fallback to raw Cypher execution if GPT apologises.  
* Demonstrates blending graph **and** tabular analytics in Streamlit.

### Cons

* Forecast model is deliberately simple (hourly mean).  
* Single‑turn chat; no conversation memory.  
* Requires the Neo4j schema to stay in sync with the hard‑coded prompt.
