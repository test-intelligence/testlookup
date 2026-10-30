"""W23d AGT-14 / AGT-15: ONE vocabulary per story, mirrored and pinned (contract §1.1, §6.2 G-A).

* ``http_exchange_vocab.py`` == ``frontend/src/types/httpExchanges.ts`` and ``prometheus_vocab.py`` ==
  ``frontend/src/types/prometheus.ts`` — read as TEXT with the W22b regex readers
  (``test_retention_export_vocab_frontend_parity.py``): every constant the TS file mirrors equals the
  Python one, and the constants the UI needs are mirrored at all.
* ``REDACTED`` is ``redaction_service.REDACTED``; the vector file's ``vocabulary`` block is the modules'.
* Every default metric template carries ``{service}`` and passes the save-side model; every outcome
  sentence names a known outcome; the template 422s answer the vocabulary's sentences.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.models.schemas import HttpExchangeIn, IntegrationsConfigUpdate, PrometheusMetricTemplateIn
from app.services import http_exchange_redaction as hr
from app.services import http_exchange_vocab as hv
from app.services import prometheus_vocab as pv
from app.services.redaction_service import REDACTED as SERVICE_REDACTED
from app.services.run_context_vocab import TOKEN_SHAPES

ROOT = Path(__file__).resolve().parents[2]
FRONTEND_TYPES = ROOT / "frontend" / "src" / "types"
HTTP_TS = FRONTEND_TYPES / "httpExchanges.ts"
PROM_TS = FRONTEND_TYPES / "prometheus.ts"
VECTORS = ROOT / "sdk-vectors" / "http_exchange.json"

_STR = r"""(?:'([^'\\]*)'|"([^"\\]*)")"""

#: The constants the UI reads: each MUST be mirrored (others may be, and are then pinned too).
HTTP_REQUIRED = (
    "EXCHANGES_PER_TEST_MAX", "EXCHANGES_PER_RUN_MAX", "BODY_STORED_MAX_BYTES", "TRUNCATION_MARKER", "LIST_PAGE_MAX",
    "REDACTED", "CAPTURE_SOURCES", "SCHEMA_STATES", "EXCHANGE_PANEL_SENTENCES", "CONTRACT_SENTENCES",
)
PROM_REQUIRED = (
    "SETTINGS_KEYS", "SECRET_KEY", "SERVICE_PLACEHOLDER", "TEMPLATE_ID_RE", "TEMPLATES_MAX", "TEMPLATE_LABEL_MAX",
    "TEMPLATE_PROMQL_MAX", "TEMPLATE_UNITS", "DEFAULT_METRIC_TEMPLATES", "OUTCOMES", "OUTCOME_SENTENCES",
    "FINDING_SENTENCES", "TEMPLATE_ERRORS", "PROBE_SENTENCES",
)


def _source(path: Path) -> str:
    if not path.exists():
        pytest.fail(f"{path.relative_to(ROOT)} is missing (waiting for G-A2, contract §1.5)")
    return path.read_text(encoding="utf-8")


def _strings(text: str) -> tuple[str, ...]:
    return tuple(a or b for a, b in re.findall(_STR, text))


def _ts_value(src: str, name: str) -> Any:
    """The literal of ``export const NAME`` (number, one-piece string, regex, string array, string record,
    or an array of string-valued objects); ``None`` when the name is not exported as such a literal."""
    head = rf"^export const {name}\b[^=\n]*=\s*"
    if match := re.search(head + r"(-?\d+(?:\.\d+)?)\s*$", src, re.MULTILINE):
        text = match.group(1)
        return float(text) if "." in text else int(text)
    if match := re.search(head + _STR + r"\s*$", src, re.MULTILINE):
        return match.group(1) if match.group(1) is not None else match.group(2)
    if match := re.search(head + r"/(.+)/[a-z]*\s*$", src, re.MULTILINE):
        return match.group(1)
    if match := re.search(head + r"\[(.*?)\]\s*as const", src, re.MULTILINE | re.DOTALL):
        body = match.group(1)
        if "{" in body:
            rows = re.findall(r"\{([^}]*)\}", body)
            return [{key: a or b for key, a, b in re.findall(rf"(\w+):\s*{_STR}", row)} for row in rows]
        return _strings(body)
    if match := re.search(head + r"\{(.*?)\n\}\s*as const", src, re.MULTILINE | re.DOTALL):
        body = match.group(1)
        entries = {key: a or b for key, a, b in re.findall(rf"^\s*'?(\w+)'?:\s*{_STR},?\s*$", body, re.MULTILINE)}
        lines = [line for line in body.splitlines() if line.strip()]
        assert len(entries) == len(lines), f"every {name} entry is one `key: 'value',` line"
        return entries
    return None


def _exported(src: str) -> list[str]:
    return re.findall(r"^export const (\w+)\b", src, re.MULTILINE)


def _python(module: Any, name: str) -> Any:
    value = getattr(module, name)
    return tuple(value) if isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value) else value


@pytest.mark.parametrize(("ts_path", "module", "required"), [(HTTP_TS, hv, HTTP_REQUIRED), (PROM_TS, pv, PROM_REQUIRED)],
                         ids=["httpExchanges.ts", "prometheus.ts"])
def test_the_frontend_mirrors_the_vocabulary(ts_path: Path, module: Any, required: tuple[str, ...]):
    src = _source(ts_path)
    exported = _exported(src)
    missing = [name for name in required if name not in exported]
    assert not missing, f"{ts_path.name} does not mirror {missing}"
    compared = 0
    for name in exported:
        if not hasattr(module, name) or name == "DEFAULT_METRIC_TEMPLATES":
            continue
        ts_value = _ts_value(src, name)
        assert ts_value is not None, f"{ts_path.name}: {name} is not a plain literal the parity test can read"
        assert ts_value == _python(module, name), f"{ts_path.name}: {name} differs from {module.__name__}"
        compared += 1
    assert compared >= len(required) - 1


def test_the_default_templates_mirror_ids_labels_and_units():
    rows = _ts_value(_source(PROM_TS), "DEFAULT_METRIC_TEMPLATES")
    assert rows == [{key: template[key] for key in ("id", "label", "unit")} for template in pv.DEFAULT_METRIC_TEMPLATES]


# ── the backend's own copies ────────────────────────────────────────────────────


def test_redacted_is_the_redaction_service_marker():
    assert hv.REDACTED == SERVICE_REDACTED == "[REDACTED]"


def test_the_vector_file_vocabulary_is_the_modules():
    vocabulary = json.loads(VECTORS.read_text(encoding="utf-8"))["vocabulary"]
    names = ("EXCHANGES_PER_TEST_MAX", "EXCHANGES_PER_RUN_MAX", "BODY_WIRE_MAX_BYTES", "BODY_STORED_MAX_BYTES",
             "TRUNCATION_MARKER", "HEADERS_MAX", "HEADER_NAME_MAX", "HEADER_VALUE_MAX", "URL_MAX", "ENDPOINT_MAX",
             "METHOD_RE", "SCHEMA_KEYS_MAX", "FIELD_NAME_RE", "SCHEMA_STATES", "HEADER_DENYLIST",
             "HEADER_SECRET_SUFFIXES", "QUERY_DENYLIST", "ENDPOINT_ID_SEGMENT_RES", "REDACTED")
    expected: dict[str, Any] = {name: getattr(hv, name) for name in names}
    expected.update({"TOKEN_SHAPES": TOKEN_SHAPES, "JSON_DEPTH_MAX": hr.JSON_DEPTH_MAX,
                     "UNNAMED_KEYS": hr.UNNAMED_KEYS, "ERROR_MAX": hr.ERROR_MAX, "JSON_PAIR_RE": hr.JSON_PAIR_RE,
                     "FORM_PAIR_RE": hr.FORM_PAIR_RE, "URL_PARTS_RE": hr.URL_PARTS_RE})
    expected = {key: list(value) if isinstance(value, tuple) else value for key, value in expected.items()}
    assert vocabulary == expected


def test_the_model_bounds_are_the_vocabulary():
    fields = HttpExchangeIn.model_fields
    assert fields["response_schema_state"].default in hv.SCHEMA_STATES
    assert any(getattr(item, "max_length", None) == hr.ERROR_MAX for item in fields["error"].metadata)
    assert len({name.lower() for name in hv.HEADER_DENYLIST}) == len(hv.HEADER_DENYLIST)
    assert all(name == name.lower() for name in (*hv.HEADER_DENYLIST, *hv.QUERY_DENYLIST, *hv.HEADER_SECRET_SUFFIXES))
    assert hv.LIST_PAGE_MAX == hv.EXCHANGES_PER_TEST_MAX


def test_every_default_template_carries_the_placeholder_and_saves():
    assert 0 < len(pv.DEFAULT_METRIC_TEMPLATES) <= pv.TEMPLATES_PER_CALL_MAX <= pv.TEMPLATES_MAX
    for template in pv.DEFAULT_METRIC_TEMPLATES:
        assert pv.SERVICE_PLACEHOLDER in template["promql"], template["id"]
        assert template["unit"] in pv.TEMPLATE_UNITS
        assert re.fullmatch(pv.TEMPLATE_ID_RE, template["id"])
        assert PrometheusMetricTemplateIn.model_validate(template).model_dump() == template
    assert len({template["id"] for template in pv.DEFAULT_METRIC_TEMPLATES}) == len(pv.DEFAULT_METRIC_TEMPLATES)


def test_every_sentence_names_a_known_outcome():
    assert set(pv.OUTCOME_SENTENCES) <= set(pv.OUTCOMES)
    assert set(pv.OUTCOMES) - set(pv.OUTCOME_SENTENCES) == {"ok"}
    assert set(pv.PROBE_SENTENCES) == {"healthy", "skipped_off"}


def _template_errors(templates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    with pytest.raises(ValidationError) as caught:
        IntegrationsConfigUpdate.model_validate({"prometheus_metric_templates": templates})
    return caught.value.errors()


def test_the_template_422s_answer_the_vocabulary_sentences():
    good = dict(pv.DEFAULT_METRIC_TEMPLATES[0])
    errors = _template_errors([good, dict(good, id="no_placeholder", promql="sum(rate(errors_total[1m]))")])
    assert (errors[0]["loc"], errors[0]["msg"]) == (("prometheus_metric_templates", 1, "promql"),
                                                    pv.TEMPLATE_ERRORS["placeholder"])
    errors = _template_errors([good, dict(good)])
    assert (errors[0]["loc"], errors[0]["msg"]) == (("prometheus_metric_templates",), pv.TEMPLATE_ERRORS["duplicate_id"])
    many = [dict(good, id=f"tpl_{chr(97 + n)}") for n in range(pv.TEMPLATES_MAX + 1)]
    assert _template_errors(many)[0]["msg"] == pv.TEMPLATE_ERRORS["too_many"]
    assert _template_errors([dict(good, unit="furlongs")])[0]["loc"] == ("prometheus_metric_templates", 0, "unit")
    saved = IntegrationsConfigUpdate.model_validate({"prometheus_metric_templates": many[: pv.TEMPLATES_MAX]})
    assert len(saved.prometheus_metric_templates or []) == pv.TEMPLATES_MAX
    assert IntegrationsConfigUpdate.model_validate({"prometheus_metric_templates": []}).prometheus_metric_templates == []
