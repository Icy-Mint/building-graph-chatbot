"""Live tests for mcp_server/server.py -- confirms the MCP wrapper exposes
the same 7 tools with correctly auto-derived schemas, and that calling one
through the MCP protocol layer reaches the same real tool function (and
real database) the Anthropic agent loop uses. Wrapped in asyncio.run()
rather than pytest-asyncio/anyio to avoid a new test dependency for two
tests.
"""
import asyncio
import json

from mcp_server.server import mcp


def test_lists_all_seven_tools_with_expected_argument_names():
    async def run():
        tools = await mcp.list_tools()
        return {t.name: set((t.input_schema or {}).get("properties", {})) for t in tools}

    schemas = asyncio.run(run())
    assert set(schemas) == {
        "get_rooms_serviced_by_ac", "get_ac_for_room", "get_room_info",
        "get_temp_readings", "get_hot_periods", "get_occupancy", "forecast_occupancy",
    }
    assert schemas["get_ac_for_room"] == {"room_number"}
    assert schemas["get_temp_readings"] == {"room_numbers", "start", "end", "agg"}


def test_call_tool_reaches_the_real_database():
    async def run():
        return await mcp.call_tool("get_ac_for_room", {"room_number": "104"})

    result = asyncio.run(run())
    assert result.is_error is False
    payload = json.loads(result.content[0].text)
    assert payload == {"status": "ok", "room_number": "104", "ac_label": "AC2"}


def test_call_tool_coerces_iso_datetime_strings():
    """MCP clients send argument values as JSON (strings for dates); this
    confirms the SDK's own coercion turns them into the datetime objects
    get_temp_readings expects, independently of tool_specs.py's
    parse_datetime_arg (the Anthropic-tool-use path's own parser) -- two
    separate callers, same underlying tool function, both must work."""
    async def run():
        return await mcp.call_tool(
            "get_temp_readings",
            {"room_numbers": ["103"], "start": "2024-01-08T00:00:00Z", "end": "2024-01-09T00:00:00Z", "agg": "daily"},
        )

    result = asyncio.run(run())
    assert result.is_error is False
    payload = json.loads(result.content[0].text)
    assert payload["status"] == "ok"
    assert payload["readings"][0]["room_number"] == "103"
