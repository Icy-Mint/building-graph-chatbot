"""Unit tests for ask_graph's retry/error-handling logic.

These run entirely offline: chain/graph are fakes with scripted return
values, so no live Neo4j instance and no OpenAI key are needed.
"""
from chatbotForecast import ask_graph


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
