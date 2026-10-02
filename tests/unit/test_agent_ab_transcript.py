"""Parsing headless Claude Code transcripts into A/B metrics (#543)."""

import agent_ab_stubs as stub

from cgis.bench.transcript import (
    ToolCall,
    Transcript,
    allocation,
    classify_next,
    parse_transcript,
    returned_files,
    run_metrics,
    sufficiency,
)


def test_parse_reads_session_header_calls_and_closing_totals() -> None:
    t = parse_transcript(stub.cgis_session())
    assert t.cwd == "/w"
    assert t.model == "claude-sonnet-5-5"
    assert t.mcp_servers == {"cgis": "connected"}
    assert [c.id for c in t.calls] == ["c1", "r1", "c2", "g1", "s1"]
    assert t.calls[0].result.startswith("callers:")
    assert t.calls[4].parent == "task-1"
    assert t.cost_usd == 0.25
    assert t.turns == 6
    assert t.duration_ms == 41000
    assert t.usage.cache_read_input_tokens == 400000
    assert t.subtype == "success"
    assert "```json" in t.answer


def test_residual_context_is_the_last_main_thread_request() -> None:
    """A sub-agent's request does not describe the main session's context."""
    t = parse_transcript(stub.cgis_session())
    assert t.residual_context == 10 + 7000


def test_parse_skips_noise_lines_and_non_init_system_events() -> None:
    t = parse_transcript(
        ["", "not json", "[1]", *stub.lines({"type": "system", "subtype": "hook", "cwd": "/x"})]
    )
    assert t == Transcript()


def test_tool_result_for_an_unknown_call_is_ignored() -> None:
    t = parse_transcript(stub.lines(stub.tool_result("nope", "x")))
    assert t.calls == []


def test_error_results_are_kept_as_errors() -> None:
    t = parse_transcript(
        stub.lines(
            stub.tool_use("b", "Bash", {"command": "cgis x"}),
            stub.tool_result("b", "no", is_error=True),
        )
    )
    assert t.calls[0].is_error is True


def test_returned_files_finds_source_paths() -> None:
    call = ToolCall(id="c", name="mcp__cgis__x", input={}, result="a/b.py, c.ts and d.tsx; e.md")
    assert returned_files(call) == {"a/b.py", "c.ts", "d.tsx"}


def _call(name: str, **tool_input: object) -> ToolCall:
    return ToolCall(id="n", name=name, input=dict(tool_input))


def test_classify_next_outcomes() -> None:
    returned = {"cgis/cli.py"}
    assert classify_next(returned, None, "/w") == "answered"
    assert classify_next(returned, _call("mcp__cgis__cgis_trace_flow"), "/w") == "called_again"
    assert (
        classify_next(returned, _call("Read", file_path="/w/src/cgis/cli.py"), "/w")
        == "read_returned"
    )
    assert (
        classify_next(returned, _call("Read", file_path="/w/src/cgis/pipeline.py"), "/w")
        == "read_other"
    )
    assert classify_next(returned, _call("Grep", pattern="x"), "/w") == "searched"
    assert classify_next(returned, _call("TodoWrite"), "/w") == "other"


def test_sufficiency_counts_only_main_thread_cgis_calls() -> None:
    t = parse_transcript(stub.cgis_session())
    assert sufficiency(t) == {"read_returned": 1, "searched": 1}


def test_allocation_is_the_share_of_named_files_the_answer_used() -> None:
    t = parse_transcript(stub.cgis_session())
    # Named: cgis/cli.py, cgis/guardian/collector.py, cgis/query/render/mermaid.py.
    assert allocation(t, ["src/cgis/cli.py"]) == 1 / 3
    assert allocation(Transcript(), ["a.py"]) is None


def test_run_metrics_on_a_clean_treatment_session() -> None:
    t = parse_transcript(stub.cgis_session())
    m = run_metrics(t, ["src/cgis/cli.py", "src/cgis/extractors/registry.py"])
    assert m.total_tool_calls == 5
    assert m.cgis_calls == 2
    assert m.tool_calls["Read"] == 2
    assert m.files_read == 2  # sub-agent reads count too: the session paid for them
    assert m.bytes_read == len("def structure(): ...\n")
    assert m.cli_attempts == 0
    assert m.contaminated is False


def test_a_blocked_attempt_is_counted_and_one_that_got_output_contaminates() -> None:
    blocked = parse_transcript(
        stub.lines(
            stub.tool_use("b", "Bash", {"command": "cgis overview"}),
            stub.tool_result("b", "x", is_error=True),
        )
    )
    leaked = parse_transcript(
        stub.lines(
            stub.tool_use("b", "Bash", {"command": "cgis overview"}),
            stub.tool_result("b", "graph!"),
        )
    )
    assert run_metrics(blocked, []).cli_attempts == 1
    assert run_metrics(blocked, []).contaminated is False
    assert run_metrics(leaked, []).contaminated is True


def test_files_read_counts_one_file_however_its_path_is_spelled() -> None:
    t = parse_transcript(
        stub.lines(
            stub.init(cwd="/w"),
            stub.tool_use("a", "Read", {"file_path": "/w/src/m.py"}),
            stub.tool_result("a", "x"),
            stub.tool_use("b", "Read", {"file_path": "./src/m.py"}),
            stub.tool_result("b", "x"),
            stub.tool_use("c", "Read", {"file_path": "src/m.py"}),
            stub.tool_result("c", "x"),
        )
    )
    assert run_metrics(t, []).files_read == 1
