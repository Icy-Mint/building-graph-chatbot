// Visualize.cypher
// Read-only queries for viewing the graph after running Graph.cypher.
// Paste one at a time into Neo4j Browser / Aura's Query tab, switch the
// results panel to the "Graph" view.

// ─────────── Whole graph (capped at 100 nodes) ───────────
MATCH (n) RETURN n LIMIT 100;

// ─────────── Structure only — skips Reading nodes so the ───────────
// ─────────── Room/AC_Unit/Sensor relationships are easy to see ───────────
MATCH (a)-[r]->(b) WHERE NOT b:Reading RETURN a, r, b;

// ─────────── Node counts per label ───────────
MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n ORDER BY label;
