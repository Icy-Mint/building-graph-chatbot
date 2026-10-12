"""Offline tests for the agent loop's control flow -- a fake Anthropic
client stands in for the real API (same dependency-injection pattern used
for the Streamlit bots' LLM chains elsewhere in this project), so these
run with no ANTHROPIC_API_KEY and no network call at all."""
import json

import pytest

from app.agent.loop import MAX_ITERATIONS, ask


class FakeUsage:
    def __init__(self, input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = cache_read_input_tokens
        self.cache_creation_input_tokens = cache_creation_input_tokens


class FakeTextBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class FakeToolUseBlock:
    type = "tool_use"

    def __init__(self, id, name, input):
        self.id = id
        self.name = name
        self.input = input


class FakeMessage:
    def __init__(self, content, stop_reason, usage=None):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = usage or FakeUsage()


class FakeMessagesAPI:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        # ask() keeps mutating (appending to) the same `messages` list across
        # iterations, so a reference here would reflect the list's FINAL
        # state by the time a test inspects it, not its state at this call.
        # Snapshot a shallow copy instead -- the inner dicts aren't mutated
        # after being appended, so a shallow copy is a faithful record.
        snapshot = dict(kwargs)
        snapshot["messages"] = list(kwargs["messages"])
        self.calls.append(snapshot)
        return self.responses.pop(0)


class FakeAnthropicClient:
    def __init__(self, responses):
        self.messages = FakeMessagesAPI(responses)


def fake_tool_dispatcher(results_by_call):
    """Returns a tool_dispatcher that yields the next scripted result each
    time it's called, regardless of which tool/args were passed -- tests
    assert on the args separately via the client's recorded calls."""
    results = iter(results_by_call)
    calls = []

    def dispatcher(name, args):
        calls.append((name, args))
        return next(results)

    dispatcher.calls = calls
    return dispatcher


def test_ask_returns_text_with_no_tool_use():
    client = FakeAnthropicClient([
        FakeMessage([FakeTextBlock("The building has 8 rooms.")], stop_reason="end_turn"),
    ])

    result = ask("How many rooms are there?", client=client)

    assert result["answer"] == "The building has 8 rooms."
    assert result["tool_calls"] == []
    assert result["hit_iteration_cap"] is False
    assert len(client.messages.calls) == 1


def test_ask_calls_a_tool_and_feeds_the_result_back():
    client = FakeAnthropicClient([
        FakeMessage(
            [FakeToolUseBlock("call_1", "get_ac_for_room", {"room_number": "101"})],
            stop_reason="tool_use",
        ),
        FakeMessage([FakeTextBlock("Room 101 is serviced by AC1.")], stop_reason="end_turn"),
    ])
    dispatcher = fake_tool_dispatcher([{"status": "ok", "room_number": "101", "ac_label": "AC1"}])

    result = ask("Which AC services room 101?", client=client, tool_dispatcher=dispatcher)

    assert result["answer"] == "Room 101 is serviced by AC1."
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["tool"] == "get_ac_for_room"
    assert result["tool_calls"][0]["status"] == "ok"
    assert dispatcher.calls == [("get_ac_for_room", {"room_number": "101"})]

    # The second API call must include the tool_result fed back as a user message.
    second_call_messages = client.messages.calls[1]["messages"]
    tool_result_content = second_call_messages[-1]["content"][0]
    assert tool_result_content["type"] == "tool_result"
    assert tool_result_content["tool_use_id"] == "call_1"
    assert json.loads(tool_result_content["content"])["ac_label"] == "AC1"
    assert tool_result_content["is_error"] is False


def test_ask_marks_tool_errors_as_is_error_in_the_fed_back_result():
    client = FakeAnthropicClient([
        FakeMessage(
            [FakeToolUseBlock("call_1", "get_ac_for_room", {"room_number": "999"})],
            stop_reason="tool_use",
        ),
        FakeMessage([FakeTextBlock("Room 999 doesn't exist.")], stop_reason="end_turn"),
    ])
    dispatcher = fake_tool_dispatcher([
        {"status": "error", "reason": "Unknown room '999'.", "valid_rooms": ["101"]},
    ])

    result = ask("What about room 999?", client=client, tool_dispatcher=dispatcher)

    assert result["tool_calls"][0]["status"] == "error"
    second_call_messages = client.messages.calls[1]["messages"]
    tool_result_content = second_call_messages[-1]["content"][0]
    assert tool_result_content["is_error"] is True


def test_ask_lets_the_model_retry_a_tool_with_corrected_arguments():
    """The spec's "retry once on tool error" isn't a special code path --
    it's the natural consequence of feeding the error back and letting the
    model call the tool again within the iteration budget. This confirms
    that actually happens: two tool_use turns, second with corrected args."""
    client = FakeAnthropicClient([
        FakeMessage([FakeToolUseBlock("call_1", "get_ac_for_room", {"room_number": "101"})], stop_reason="tool_use"),
        FakeMessage([FakeToolUseBlock("call_2", "get_ac_for_room", {"room_number": "101"})], stop_reason="tool_use"),
        FakeMessage([FakeTextBlock("Room 101 is serviced by AC1.")], stop_reason="end_turn"),
    ])
    dispatcher = fake_tool_dispatcher([
        {"status": "error", "reason": "Unknown room '101'.", "valid_rooms": ["101"]},
        {"status": "ok", "room_number": "101", "ac_label": "AC1"},
    ])

    result = ask("Which AC services room 101?", client=client, tool_dispatcher=dispatcher)

    assert result["answer"] == "Room 101 is serviced by AC1."
    assert [c["status"] for c in result["tool_calls"]] == ["error", "ok"]
    assert len(client.messages.calls) == 3


def test_ask_hits_iteration_cap_and_makes_one_final_no_tools_call():
    """Loops MAX_ITERATIONS times always getting tool_use back (an agent
    that never converges) -- the loop must stop, ask once more without
    tools for a best-effort answer, and report hit_iteration_cap=True."""
    looping_responses = [
        FakeMessage([FakeToolUseBlock(f"call_{i}", "get_room_info", {"room_number": "101"})], stop_reason="tool_use")
        for i in range(MAX_ITERATIONS)
    ]
    final_response = FakeMessage(
        [FakeTextBlock("I wasn't able to fully investigate, but here's what I found.")],
        stop_reason="end_turn",
    )
    client = FakeAnthropicClient(looping_responses + [final_response])
    dispatcher = fake_tool_dispatcher([{"status": "ok"} for _ in range(MAX_ITERATIONS)])

    result = ask("A question the model can't resolve", client=client, tool_dispatcher=dispatcher)

    assert result["hit_iteration_cap"] is True
    assert "wasn't able to fully investigate" in result["answer"]
    assert len(result["tool_calls"]) == MAX_ITERATIONS
    assert len(client.messages.calls) == MAX_ITERATIONS + 1  # +1 for the final no-tools call

    # The final call must NOT include tools (it's asking for a wrap-up, not
    # inviting another tool call that would never terminate the loop).
    assert "tools" not in client.messages.calls[-1]


def test_ask_never_overrides_the_model_saying_it_cannot_answer():
    """Spec: never force an answer after the model says it cannot answer.
    If the model's first response is plain text declining to answer, the
    loop must return that text as-is, not retry or synthesize something."""
    client = FakeAnthropicClient([
        FakeMessage([FakeTextBlock("I don't have enough information to answer that.")], stop_reason="end_turn"),
    ])

    result = ask("An out-of-scope question", client=client)

    assert result["answer"] == "I don't have enough information to answer that."
    assert len(client.messages.calls) == 1


def test_ask_accumulates_usage_across_turns_including_cache_fields():
    client = FakeAnthropicClient([
        FakeMessage(
            [FakeToolUseBlock("call_1", "get_room_info", {"room_number": "101"})],
            stop_reason="tool_use",
            usage=FakeUsage(input_tokens=100, output_tokens=20, cache_read_input_tokens=80),
        ),
        FakeMessage(
            [FakeTextBlock("Done.")],
            stop_reason="end_turn",
            usage=FakeUsage(input_tokens=50, output_tokens=10, cache_read_input_tokens=40),
        ),
    ])
    dispatcher = fake_tool_dispatcher([{"status": "ok"}])

    result = ask("question", client=client, tool_dispatcher=dispatcher)

    assert result["usage"]["input_tokens"] == 150
    assert result["usage"]["output_tokens"] == 30
    assert result["usage"]["cache_read_input_tokens"] == 120
