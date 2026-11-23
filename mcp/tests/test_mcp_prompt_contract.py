"""Render each advertised prompt and check tool arguments in critical recipes."""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import pytest

MCP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MCP_DIR))

import server  # noqa: E402

PROJECT = "11111111-1111-4111-8111-111111111111"
TEST_ID = "22222222-2222-4222-8222-222222222222"
VERSION = "2026.11.3-qa"

PARAMETERS = {
    "investigate_failure": {"test_case_id": TEST_ID},
    "release_readiness_report": {"release_version": VERSION},
    "weekly_quality_digest": {},
    "flakiness_investigation": {"threshold_pct": "47"},
    "defect_triage_session": {},
    "suite_health_check": {"suite_name": "checkout-qa"},
    "fix_this_flaky_test": {"fingerprint": "b94c0127f321daea"},
}


def render(name):
    args = {"project_id": PROJECT, **PARAMETERS[name]}
    result = asyncio.run(server.mcp.get_prompt(name, args))
    return "\n".join(message.content.text for message in result.messages if message.content.type == "text")


def test_every_published_prompt_has_a_render_case():
    prompts = asyncio.run(server.mcp.list_prompts())
    assert {prompt.name for prompt in prompts} == set(PARAMETERS)


@pytest.mark.parametrize("name", PARAMETERS)
def test_prompt_includes_supplied_arguments(name):
    text = render(name)

    assert PROJECT in text
    for key, value in PARAMETERS[name].items():
        assert value in text
        assert "{" + key + "}" not in text


def test_investigation_recipe_uses_the_published_test_case_argument():
    tools = asyncio.run(server.mcp.list_tools())
    get_test_case = next(tool for tool in tools if tool.name == "get_test_case")
    step = next(line for line in render("investigate_failure").splitlines() if "`get_test_case`" in line)
    supplied = re.search(rf'\b(\w+)="{TEST_ID}"', step)

    assert supplied is not None, step
    assert supplied.group(1) in get_test_case.input_schema["required"], step


def test_named_release_recipe_requests_the_releases_verdict():
    text = render("release_readiness_report")

    gate_steps = [line for line in text.splitlines() if "`get_release_gate`" in line]
    assert any(VERSION in line for line in gate_steps), text


# ── Acceptance cases added with the TL-QA fixes ──────────────────────────────

RUN_ID = "33333333-3333-4333-8333-333333333333"


def _tool(name):
    return next(tool for tool in asyncio.run(server.mcp.list_tools()) if tool.name == name)


def _investigation(**arguments):
    result = asyncio.run(server.mcp.get_prompt(
        "investigate_failure", {"project_id": PROJECT, "test_case_id": TEST_ID, **arguments},
    ))
    return "\n".join(message.content.text for message in result.messages if message.content.type == "text")


def test_investigation_recipe_names_only_published_arguments_of_each_tool_it_calls():
    """TL-QA-09: every `name="value"` a calling step gives a tool is an argument that tool publishes."""
    checked = 0
    for line in _investigation(run_id=RUN_ID).splitlines():
        step = re.match(r"\d+\. Call `(\w+)`", line)
        if not step:
            continue
        properties = _tool(step.group(1)).input_schema["properties"]
        for argument in re.findall(r'\b(\w+)="', line):
            assert argument in properties, (step.group(1), argument, line)
            checked += 1
    assert checked == 3  # project_id (step 1), test_id (step 3), test_case_id (step 4)


def test_investigation_recipe_carries_a_supplied_run_into_the_lookup():
    """TL-QA-09: get_test_case needs run_id and test_id; a supplied run is quoted for the later steps."""
    assert set(_tool("get_test_case").input_schema["required"]) == {"run_id", "test_id"}
    text = _investigation(run_id=f"  {RUN_ID} ")

    assert f'run_id="{RUN_ID}"' in text
    assert f'test_id="{TEST_ID}"' in text and "the run_id from step 2" in text


@pytest.mark.parametrize("arguments", [{}, {"run_id": ""}, {"run_id": "   "}], ids=["omitted", "empty", "blank"])
def test_investigation_recipe_stops_for_a_missing_run_instead_of_searching_by_id(arguments):
    """TL-QA-09: no tool maps a test case id to its run, so the recipe asks; it never guesses."""
    text = _investigation(**arguments)
    step = next(line for line in text.splitlines() if line.startswith("2."))

    assert "run_id=(none given)" in step and 'run_id=""' not in text
    assert "stop here and ask the user" in step and "Never guess a run" in step
    assert "Call `search_tests`" not in text


def test_named_release_recipe_reads_the_stored_gate_first_and_records_nothing():
    """TL-QA-10: the decision is the named release's stored gate, read with the tool's own arguments."""
    text = render("release_readiness_report")
    steps = [line for line in text.splitlines() if re.match(r"\d+\. ", line)]
    properties = _tool("get_release_gate").input_schema["properties"]

    assert "`get_release_gate`" in steps[0] and "`check_release_readiness`" in steps[1]
    assert f'release="{VERSION}"' in steps[0] and f'project="{PROJECT}"' in steps[0]
    assert "evaluate=False" in steps[0] and "evaluate=True" not in text
    assert {"release", "project", "evaluate"} <= set(properties)
    assert "context only" in steps[1]


def test_named_release_recipe_keeps_unevaluated_and_unresolved_apart_from_a_verdict():
    """TL-QA-10: no stored verdict stays NOT EVALUATED, and an unresolved name is a question."""
    text = render("release_readiness_report")

    assert "`evaluated: false`: the decision is NOT EVALUATED" in text
    assert "Do not put another release's verdict, or the project signal, in its place." in text
    assert "ask the user which release they mean. Do not pick one." in text


def test_default_release_placeholder_is_called_out_as_naming_no_release():
    """TL-QA-10: the prompt's default "next" maps to no release, so the recipe says to ask."""
    result = asyncio.run(server.mcp.get_prompt("release_readiness_report", {"project_id": PROJECT}))
    text = "\n".join(message.content.text for message in result.messages if message.content.type == "text")

    assert 'release="next"' in text
    assert 'is the placeholder "next", which names no release' in text
