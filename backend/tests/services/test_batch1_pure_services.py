import random
import sys
import types
import xml.etree.ElementTree as std_et
from unittest.mock import patch

import pytest

from app.services.allure_parser import _calc_duration, parse_allure_result
from app.services.llm_factory import get_llm
from app.services.mock_generator import generate_mock_allure_results, generate_mock_testng_results

if "defusedxml.ElementTree" not in sys.modules:
    pkg = types.ModuleType("defusedxml")
    mod = types.ModuleType("defusedxml.ElementTree")
    mod.fromstring = std_et.fromstring
    sys.modules["defusedxml"] = pkg
    sys.modules["defusedxml.ElementTree"] = mod


def test_allure_parser_uses_parent_suite_and_tags():
    parsed = parse_allure_result(
        {
            "uuid": "u1",
            "name": "test_x",
            "labels": [
                {"name": "parentSuite", "value": "API"},
                {"name": "tag", "value": "regression"},
            ],
            "start": 10,
            "stop": 20,
            "statusDetails": {"trace": "oops"},
        },
        "run-id",
        "prefix/allure/u1-result.json",
    )
    assert parsed is not None
    assert parsed["suite_name"] == "API"
    assert parsed["tags"] == ["regression"]
    assert parsed["error_message"] == "oops"
    assert parsed["minio_s3_prefix"].endswith("/allure/")


def test_calc_duration_never_negative():
    assert _calc_duration(10, 5) == 0
    assert _calc_duration(None, 5) is None


def test_testng_parser_handles_testsuites_root_and_error_node():
    from app.services.testng_parser import parse_testng_xml
    xml = """<testsuites>
      <testsuite name="S1">
        <testcase classname="pkg.C1" name="a" time="0.1"><error message="boom"/></testcase>
      </testsuite>
    </testsuites>"""
    rows = parse_testng_xml(xml, "run-1")
    assert len(rows) == 1
    assert rows[0]["status"] == "broken"
    assert rows[0]["package_name"] == "pkg"


def test_mock_generators_return_expected_shapes():
    random.seed(1)
    allure = generate_mock_allure_results(3, 0.5, "p1", "b1")
    testng = generate_mock_testng_results(3, 0.5, "p1", "b1")
    assert len(allure) == 3
    assert allure[0][0].startswith("allure/")
    assert testng[0][0] == "testng/testng-results-b1.xml"
    assert b"<testsuite" in testng[0][1]


@patch("langchain_ollama.ChatOllama")
def test_llm_factory_ollama_uses_registry_model(mock_ollama):
    with patch("app.services.llm_factory._get_active_model_sync", return_value="ft-model"):
        get_llm(provider="ollama", track="reasoning")
    kwargs = mock_ollama.call_args.kwargs
    assert kwargs["model"] == "ft-model"


def test_llm_factory_openai_blocked_when_offline():
    with patch("app.services.llm_factory.settings") as s:
        s.AI_OFFLINE_MODE = True
        s.LLM_PROVIDER = "openai"
        with pytest.raises(ValueError):
            get_llm(provider="openai")
