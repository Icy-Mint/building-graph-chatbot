"""MCP server wrapper over the existing typed tool layer (app/tools/) --
per the spec, built only now that the core agent (Step 4) and the eval
harness (Step 6) both work. This registers the SAME 7 Python functions
app/agent/tool_specs.py hands to the Anthropic tool-use loop; nothing
about the tools' logic, validation, or read-only enforcement is
duplicated or reimplemented here. An MCP client gets exactly the same
typed, hand-written queries the Claude agent loop does -- still no tool
that accepts raw Cypher or SQL.

Named mcp_server/, not mcp/ as the spec's own wording suggests: a local
folder literally named mcp/ would shadow the installed `mcp` PyPI package
this module imports, which is a silent, confusing way to break every
import of the real package from anywhere in this project. Not a step
back from the spec's intent, just a naming collision it didn't
anticipate.

Run with:
    python -m mcp_server.server
and point an MCP client (e.g. Claude Desktop's config, or `mcp dev
mcp_server/server.py` for the inspector) at it over stdio.
"""
from mcp.server.mcpserver import MCPServer

from app.tools.graph_tools import get_ac_for_room, get_room_info, get_rooms_serviced_by_ac
from app.tools.timeseries_tools import forecast_occupancy, get_hot_periods, get_occupancy, get_temp_readings

mcp = MCPServer(
    name="dorm-building-tools",
    instructions=(
        "Read-only tools over a dormitory building's Neo4j graph (structure) "
        "and TimescaleDB (sensor history: temperature, occupancy). Every tool "
        "runs one hand-written, parameterized query -- none accepts raw Cypher "
        "or SQL. Room numbers are strings like '101'; AC unit labels are like "
        "'AC1'. Mechanical rooms (201, 202) have no sensors -- tools return "
        "status 'no_sensors' for those, distinct from 'error' (bad input) and "
        "'no_data' (valid input, nothing matched)."
    ),
)

for fn in (get_rooms_serviced_by_ac, get_ac_for_room, get_room_info, get_temp_readings, get_hot_periods, get_occupancy, forecast_occupancy):
    mcp.add_tool(fn)


if __name__ == "__main__":
    mcp.run()
