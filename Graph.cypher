
//1. Wipe existing data (if any):
MATCH (n) DETACH DELETE n;

//2. Create Rooms and AC Units:
UNWIND range(101, 106) AS room_number
CREATE (:Room {room_number: toString(room_number), type: "dorm"});

UNWIND [201, 202] AS mech_number
CREATE (:Room {room_number: toString(mech_number), type: "mechanical"});

CREATE (ac1:AC_Unit {ac_id: "AC1"}),
       (ac2:AC_Unit {ac_id: "AC2"});

MATCH (r:Room {room_number: "201"}), (ac:AC_Unit {ac_id: "AC1"})
CREATE (r)-[:CONTAINS]->(ac);

MATCH (r:Room {room_number: "202"}), (ac:AC_Unit {ac_id: "AC2"})
CREATE (r)-[:CONTAINS]->(ac);

//3. Assign Dorm Rooms to AC Units:

MATCH (r:Room), (ac:AC_Unit {ac_id: "AC1"})
WHERE r.room_number IN ["101", "102", "103"]
CREATE (r)-[:SERVICED_BY]->(ac);

MATCH (r:Room), (ac:AC_Unit {ac_id: "AC2"})
WHERE r.room_number IN ["104", "105", "106"]
CREATE (r)-[:SERVICED_BY]->(ac);

//4. Add Sensors:
MATCH (r:Room)
WHERE r.type = "dorm"
WITH r
CREATE (occ:Sensor {sensor_id: "OCC_" + r.room_number, sensor_type: "occupancy"}),
       (temp:Sensor {sensor_id: "TEMP_" + r.room_number, sensor_type: "temperature"}),
       (r)-[:HAS_SENSOR]->(occ),
       (r)-[:HAS_SENSOR]->(temp);

//5. Connect Temp Sensors to AC Units:
MATCH (r:Room)-[:HAS_SENSOR]->(s:Sensor {sensor_type: "temperature"}),
      (r)-[:SERVICED_BY]->(ac:AC_Unit)
CREATE (s)-[:REPORTS_TO]->(ac);

//Quick Query to Visualize the Graph
MATCH (n)-[r]->(m) RETURN n, r, m LIMIT 50;

//Link the csv file to the graph 

LOAD CSV WITH HEADERS FROM "https://raw.githubusercontent.com/Icy-Mint/building-graph-chatbot/refs/heads/main/sensor_outputs/room_101_timeseries.csv" AS row
WITH row,
     toFloat(row.temperature) AS tempVal,
     toInteger(row.occupancy) AS occVal,
     datetime(replace(row.timestamp, " ", "T")) AS ts

// Match the temperature and occupancy sensors
MATCH (tempSensor:Sensor {sensor_id: row.sensor_id_temp})
MATCH (occSensor:Sensor {sensor_id: row.sensor_id_occ})

// Create temperature reading and link to sensor
CREATE (tempReading:Reading {
    timestamp: ts,
    value: tempVal,
    sensor_type: "temperature",
    room_number: row.room_number
})
CREATE (tempSensor)-[:RECORDED]->(tempReading)

// Create occupancy reading and link to sensor
CREATE (occReading:Reading {
    timestamp: ts,
    value: occVal,
    sensor_type: "occupancy",
    room_number: row.room_number
})
CREATE (occSensor)-[:RECORDED]->(occReading);

// for other rooms 

LOAD CSV WITH HEADERS FROM "https://raw.githubusercontent.com/Icy-Mint/building-graph-chatbot/refs/heads/main/sensor_outputs/room_102_timeseries.csv" AS row
WITH row,
     toFloat(row.temperature) AS tempVal,
     toInteger(row.occupancy) AS occVal,
     datetime(replace(row.timestamp, " ", "T")) AS ts

// Match the temperature and occupancy sensors
MATCH (tempSensor:Sensor {sensor_id: row.sensor_id_temp})
MATCH (occSensor:Sensor {sensor_id: row.sensor_id_occ})

// Create temperature reading and link to sensor
CREATE (tempReading:Reading {
    timestamp: ts,
    value: tempVal,
    sensor_type: "temperature",
    room_number: row.room_number
})
CREATE (tempSensor)-[:RECORDED]->(tempReading)

// Create occupancy reading and link to sensor
CREATE (occReading:Reading {
    timestamp: ts,
    value: occVal,
    sensor_type: "occupancy",
    room_number: row.room_number
})
CREATE (occSensor)-[:RECORDED]->(occReading);



LOAD CSV WITH HEADERS FROM "https://raw.githubusercontent.com/Icy-Mint/building-graph-chatbot/refs/heads/main/sensor_outputs/room_103_timeseries.csv" AS row
WITH row,
     toFloat(row.temperature) AS tempVal,
     toInteger(row.occupancy) AS occVal,
     datetime(replace(row.timestamp, " ", "T")) AS ts

// Match the temperature and occupancy sensors
MATCH (tempSensor:Sensor {sensor_id: row.sensor_id_temp})
MATCH (occSensor:Sensor {sensor_id: row.sensor_id_occ})

// Create temperature reading and link to sensor
CREATE (tempReading:Reading {
    timestamp: ts,
    value: tempVal,
    sensor_type: "temperature",
    room_number: row.room_number
})
CREATE (tempSensor)-[:RECORDED]->(tempReading)

// Create occupancy reading and link to sensor
CREATE (occReading:Reading {
    timestamp: ts,
    value: occVal,
    sensor_type: "occupancy",
    room_number: row.room_number
})
CREATE (occSensor)-[:RECORDED]->(occReading);


LOAD CSV WITH HEADERS FROM "https://raw.githubusercontent.com/Icy-Mint/building-graph-chatbot/refs/heads/main/sensor_outputs/room_104_timeseries.csv" AS row
WITH row,
     toFloat(row.temperature) AS tempVal,
     toInteger(row.occupancy) AS occVal,
     datetime(replace(row.timestamp, " ", "T")) AS ts

// Match the temperature and occupancy sensors
MATCH (tempSensor:Sensor {sensor_id: row.sensor_id_temp})
MATCH (occSensor:Sensor {sensor_id: row.sensor_id_occ})

// Create temperature reading and link to sensor
CREATE (tempReading:Reading {
    timestamp: ts,
    value: tempVal,
    sensor_type: "temperature",
    room_number: row.room_number
})
CREATE (tempSensor)-[:RECORDED]->(tempReading)

// Create occupancy reading and link to sensor
CREATE (occReading:Reading {
    timestamp: ts,
    value: occVal,
    sensor_type: "occupancy",
    room_number: row.room_number
})
CREATE (occSensor)-[:RECORDED]->(occReading);


LOAD CSV WITH HEADERS FROM "https://raw.githubusercontent.com/Icy-Mint/building-graph-chatbot/refs/heads/main/sensor_outputs/room_105_timeseries.csv" AS row
WITH row,
     toFloat(row.temperature) AS tempVal,
     toInteger(row.occupancy) AS occVal,
     datetime(replace(row.timestamp, " ", "T")) AS ts

// Match the temperature and occupancy sensors
MATCH (tempSensor:Sensor {sensor_id: row.sensor_id_temp})
MATCH (occSensor:Sensor {sensor_id: row.sensor_id_occ})

// Create temperature reading and link to sensor
CREATE (tempReading:Reading {
    timestamp: ts,
    value: tempVal,
    sensor_type: "temperature",
    room_number: row.room_number
})
CREATE (tempSensor)-[:RECORDED]->(tempReading)

// Create occupancy reading and link to sensor
CREATE (occReading:Reading {
    timestamp: ts,
    value: occVal,
    sensor_type: "occupancy",
    room_number: row.room_number
})
CREATE (occSensor)-[:RECORDED]->(occReading);


LOAD CSV WITH HEADERS FROM "https://raw.githubusercontent.com/Icy-Mint/building-graph-chatbot/refs/heads/main/sensor_outputs/room_106_timeseries.csv" AS row
WITH row,
     toFloat(row.temperature) AS tempVal,
     toInteger(row.occupancy) AS occVal,
     datetime(replace(row.timestamp, " ", "T")) AS ts

// Match the temperature and occupancy sensors
MATCH (tempSensor:Sensor {sensor_id: row.sensor_id_temp})
MATCH (occSensor:Sensor {sensor_id: row.sensor_id_occ})

// Create temperature reading and link to sensor
CREATE (tempReading:Reading {
    timestamp: ts,
    value: tempVal,
    sensor_type: "temperature",
    room_number: row.room_number
})
CREATE (tempSensor)-[:RECORDED]->(tempReading)

// Create occupancy reading and link to sensor
CREATE (occReading:Reading {
    timestamp: ts,
    value: occVal,
    sensor_type: "occupancy",
    room_number: row.room_number
})
CREATE (occSensor)-[:RECORDED]->(occReading);
