# KG-FOR-MECH
The repo is a demo for creating AI chatbot based on knowledge graph for mechanical system

### 📂 Creating Knowledge Graph Using Graph.cypher 

    Run Graph.cypher to create a knowledge graph in neo4j database 
## Graph Schema Summary
`Nodes`

    Room — room_number, type (dorm or mechanical)

    AC_Unit — ac_id

    Sensor — sensor_id, sensor_type (temperature or occupancy)

    Reading — timestamp, value, sensor_type, room_number

`Relationships`

    (:Room)-[:HAS_SENSOR]->(:Sensor)

    (:AC_Unit)-[:SERVICES]->(:Room)

    (:Room)-[:CONTAINS]->(:AC_Unit) — for mechanical rooms

    (:Sensor)-[:REPORTS_TO]->(:AC_Unit) — only for temperature sensors

    (:Sensor)-[:RECORDED]->(:Reading)

### 📂 Sensor Data Files

This project includes synthetic sensor data used for the graph demo:

- `room_101_timeseries.csv`: Room-level data for timestamp,room_number,sensor_id_occ,sensor_id_temp,occupancy,temperature
- Those data can be generated using SensorDataGeneration.py

### 📂 Environment variable template 
Copy the template using the code below to start build your own knowledge graph:
cp .env.template .env


## 🧪 Manual test queries

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

### 📂 Short Description for Chatbot.py

This Streamlit-based chatbot answers building management questions using an LLM classifier (via LangChain) to extract intent (hottest, coldest, occupancy, ac_mapping, fallback) and room number. SensorHelper and GraphHelper handle CSV sensor data and Neo4j queries. The system routes questions to helper functions based on LLM-classified intent, with fallback to LLM for open-ended queries.

## Pros

    Simple, maintainable routing logic.

    LLM handles synonyms and paraphrasing.

    Structured parsing reduces hallucination.

    Efficient hybrid of LLM + Python functions.

    Easily extensible for new actions.

## Cons

    Rigid: pre-defined action labels required.

    Limited for multi-step or complex reasoning.

    LLM only classifies, no dynamic function calling.

    More complex as functions scale.

    No conversation memory or context.

### 1 . Test this questiona in the chatbot“Which rooms are serviced by which AC units?”

You should see the cypher block in your terminal 
```cypher  
MATCH (a:AC_Unit)-[:SERVICES]->(r:Room)
RETURN a.ac_id AS ac_unit,
       collect(r.room_number) AS rooms
ORDER  BY ac_unit;