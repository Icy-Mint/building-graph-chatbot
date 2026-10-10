"""Unit tests for ask_graph's retry/error-handling logic, plus the env-var
validation helper.

These run entirely offline: chain/graph are fakes with scripted return
values, so no live Neo4j instance and no OpenAI key are needed.
"""
import os

import pytest

from chatbotForecast import ask_graph, require_env_vars, ensure_read_only_cypher, _PROVIDER_API_KEY_ENV


class FakeChain:
    """Stands in for GraphCypherQAChain. `responses` is a list of dicts
    returned in order, one per call to .invoke()."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def invoke(self, inputs):
        self.calls.append(inputs["query"])
        return self.responses.pop(0)


class FakeGraph:
    """Stands in for Neo4jGraph. `rows_by_cypher` maps a Cypher string to
    the rows .query() should return (or raises if mapped to an Exception)."""

    def __init__(self, rows_by_cypher=None):
        self.rows_by_cypher = rows_by_cypher or {}
        self.queried = []

    def query(self, cypher):
        self.queried.append(cypher)
        result = self.rows_by_cypher.get(cypher, [])
        if isinstance(result, Exception):
            raise result
        return result


def test_success_on_first_attempt_with_direct_result():
    chain = FakeChain([{"cypher": "MATCH (r:Room) RETURN r", "result": "room 101"}])
    graph = FakeGraph()

    cypher, result, error = ask_graph("any question", chain, graph)

    assert result == "room 101"
    assert error is None
    assert len(chain.calls) == 1


def test_success_via_direct_execution_when_chain_gives_no_result_text():
    cypher_text = "MATCH (r:Room) RETURN r.room_number"
    chain = FakeChain([{"cypher": cypher_text, "result": None}])
    graph = FakeGraph(rows_by_cypher={cypher_text: [{"room_number": "101"}]})

    cypher, result, error = ask_graph("any question", chain, graph)

    assert result == [{"room_number": "101"}]
    assert error is None
    assert len(chain.calls) == 1


def test_apology_triggers_retry_and_second_attempt_succeeds():
    good_cypher = "MATCH (r:Room) RETURN r.room_number"
    chain = FakeChain([
        {"cypher": "", "result": "I'm sorry, I cannot answer that."},
        {"cypher": good_cypher, "result": None},
    ])
    graph = FakeGraph(rows_by_cypher={good_cypher: [{"room_number": "102"}]})

    cypher, result, error = ask_graph("ambiguous question", chain, graph)

    assert result == [{"room_number": "102"}]
    assert error is None
    assert len(chain.calls) == 2
    # the retry must include the original question plus failure context
    assert "ambiguous question" in chain.calls[1]


def test_execution_error_triggers_retry_with_error_fed_back():
    bad_cypher = "MATCH (r:Room)-[:WRONG]->(s) RETURN r"
    good_cypher = "MATCH (r:Room) RETURN r.room_number"
    chain = FakeChain([
        {"cypher": bad_cypher, "result": None},
        {"cypher": good_cypher, "result": None},
    ])
    graph = FakeGraph(rows_by_cypher={
        bad_cypher: Exception("Unknown relationship type: WRONG"),
        good_cypher: [{"room_number": "103"}],
    })

    cypher, result, error = ask_graph("question", chain, graph)

    assert result == [{"room_number": "103"}]
    assert error is None
    assert len(chain.calls) == 2
    assert "Unknown relationship type" in chain.calls[1]


def test_zero_rows_on_both_attempts_returns_real_error_not_silent_failure():
    empty_cypher = "MATCH (r:Room {room_number:'999'}) RETURN r"
    chain = FakeChain([
        {"cypher": empty_cypher, "result": None},
        {"cypher": empty_cypher, "result": None},
    ])
    graph = FakeGraph(rows_by_cypher={empty_cypher: []})

    cypher, result, error = ask_graph("question about room 999", chain, graph)

    assert result is None
    assert error == "Query executed but returned 0 rows."
    assert len(chain.calls) == 2  # one retry was attempted, not silently given up on


def test_no_cypher_and_no_result_returns_error_without_crashing():
    chain = FakeChain([
        {"cypher": None, "result": None},
        {"cypher": None, "result": None},
    ])
    graph = FakeGraph()

    cypher, result, error = ask_graph("nonsense question", chain, graph)

    assert result is None
    assert error == "The model did not produce a usable Cypher query."
    assert graph.queried == []  # never tried to execute an empty/garbage query


def test_max_retries_zero_disables_retry():
    chain = FakeChain([{"cypher": "", "result": "I'm sorry, I cannot answer that."}])
    graph = FakeGraph()

    cypher, result, error = ask_graph("question", chain, graph, max_retries=0)

    assert result is None
    assert len(chain.calls) == 1  # no retry attempted


def test_on_attempt_fires_once_per_attempt_with_error_or_none():
    good_cypher = "MATCH (r:Room) RETURN r.room_number"
    chain = FakeChain([
        {"cypher": "", "result": "I'm sorry, I cannot answer that."},
        {"cypher": good_cypher, "result": None},
    ])
    graph = FakeGraph(rows_by_cypher={good_cypher: [{"room_number": "104"}]})
    seen = []

    ask_graph("q", chain, graph, on_attempt=lambda i, c, e: seen.append((i, c, e)))

    assert len(seen) == 2
    assert seen[0][0] == 0 and seen[0][2] is not None  # first attempt failed, error recorded
    assert seen[1][0] == 1 and seen[1][2] is None       # second attempt succeeded, no error


# ───────────────────────────────────────────────────────────────────────
# require_env_vars
# ───────────────────────────────────────────────────────────────────────

def test_require_env_vars_passes_when_all_set(monkeypatch):
    monkeypatch.setenv("NEO4J_URI", "bolt://x")
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "pw")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    import chatbotForecast
    monkeypatch.setattr(chatbotForecast, "LLM_PROVIDER", "openai")

    require_env_vars()  # should not raise


def test_require_env_vars_lists_every_missing_var(monkeypatch):
    for name in ["NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "OPENAI_API_KEY"]:
        monkeypatch.delenv(name, raising=False)
    import chatbotForecast
    monkeypatch.setattr(chatbotForecast, "LLM_PROVIDER", "openai")

    with pytest.raises(RuntimeError) as exc_info:
        require_env_vars()

    message = str(exc_info.value)
    for name in ["NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "OPENAI_API_KEY"]:
        assert name in message


def test_require_env_vars_checks_the_active_providers_key(monkeypatch):
    monkeypatch.setenv("NEO4J_URI", "bolt://x")
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "pw")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    import chatbotForecast
    monkeypatch.setattr(chatbotForecast, "LLM_PROVIDER", "gemini")

    with pytest.raises(RuntimeError) as exc_info:
        require_env_vars()

    assert "GOOGLE_API_KEY" in str(exc_info.value)


# ───────────────────────────────────────────────────────────────────────
# ensure_read_only_cypher
# ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cypher", [
    "MATCH (r:Room) RETURN r.room_number",
    "MATCH (r:Room {room_number:'101'})-[:HAS_SENSOR]->(s:Sensor) RETURN s",
    "MATCH (a:AC_Unit)-[:SERVICES]->(r:Room) RETURN r.room_number ORDER BY r.room_number LIMIT 5",
])
def test_ensure_read_only_cypher_allows_real_read_queries(cypher):
    ensure_read_only_cypher(cypher)  # should not raise


@pytest.mark.parametrize("cypher", [
    "MATCH (r:Room) DETACH DELETE r",
    "MATCH (r:Room) DELETE r",
    "CREATE (r:Room {room_number:'999'})",
    "MATCH (r:Room {room_number:'101'}) SET r.type = 'mechanical'",
    "MATCH (r:Room {room_number:'101'}) REMOVE r.type",
    "MERGE (r:Room {room_number:'999'})",
    "DROP INDEX room_number_index",
])
def test_ensure_read_only_cypher_blocks_write_clauses(cypher):
    with pytest.raises(PermissionError):
        ensure_read_only_cypher(cypher)
