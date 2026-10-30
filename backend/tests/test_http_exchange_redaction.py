"""W23d AGT-14: the ONE server rule for a captured HTTP exchange (contract §1.2 / §1.4 / §2.1 P-2).

* Every case of ``sdk-vectors/http_exchange.json`` (the cross-language specification the Python SDK
  runs too) through the REAL model: ``HttpExchangeIn`` behind the list pre-filter, exactly as
  ``IngestTestResult`` / ``LiveHttpExchangesIn`` receive it. Redacting ``expect`` again yields ``expect``.
* The raw secret samples are absent from ``IngestPayload(...).model_dump()`` — the object-storage copy.
* The per-test and per-run caps, and a malformed exchange dropped without a 422 on the batch.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.models import schemas
from app.models.schemas import HttpExchangeIn, IngestPayload, IngestTestResult, LiveHttpExchangesIn
from app.services import http_exchange_redaction as hr
from app.services import http_exchange_vocab as vocab

VECTORS_PATH = Path(__file__).resolve().parents[2] / "sdk-vectors" / "http_exchange.json"
VECTORS: dict[str, Any] = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))
CASES: list[dict[str, Any]] = VECTORS["cases"]

#: Fragments of every secret sample in the vector inputs (non-hex: each carries a w / s / k … letter).
RAW_FRAGMENTS = (
    "eyJzdWIiOiJ3MjNkLXNhbXBsZSJ9", "c2lnbmF0dXJlLXNhbXBsZS12YWx1ZS0x", "dzIzZDpzYW1wbGUtcGFzcw",
    "w23dSampleSessionValue", "w23dcsrf", "sid=w23dsid", "tlk_w23d_sample_key_", "cHJveHk6dzIzZFByb3h5UGFzcw",
    "w23dSampleSessionToken", "QxZkLmNoPqRsTuVwXy", "W23DSAMPLEKEYQZX", "w23dCustomSecretValue",
    "w23dServicePass", "w23dSamplePass", "w23dClientSecretValue", "w23dApiKeyValue", "w23dGithubValue",
    "w23d.sample@example.test", "w23dSampleAccessToken123", "w23dsig", "samplepass", "w23dBareUserinfo",
    "w23dListPass", "w23dBlobPass", "w23dtoken", "w23dFormValue",
)


def expand(value: Any) -> Any:
    """The vector file's generator specs (its ``generators`` block)."""
    if isinstance(value, dict):
        keys = set(value)
        if keys == {"$repeat", "times"}:
            return value["$repeat"] * value["times"]
        if keys == {"$join"}:
            return "".join(expand(part) for part in value["$join"])
        if keys == {"$list", "count"}:
            return [expand(value["$list"]) for _ in range(value["count"])]
        if keys == {"$json"}:
            return json.dumps(expand(value["$json"]), ensure_ascii=False, separators=(",", ":"))
        return {key: expand(item) for key, item in value.items()}
    if isinstance(value, list):
        return [expand(item) for item in value]
    return value


def _server(wire: dict[str, Any]) -> dict[str, Any] | None:
    """What the server stores for ONE wire exchange (``None`` = dropped), through the model's own pre-filter."""
    result = IngestTestResult.model_validate(
        {"test_name": "test_vectors", "status": "PASSED", "http_exchanges": [wire]})
    assert result.http_exchanges is not None
    if not result.http_exchanges:
        assert result.http_exchanges_dropped == 1
        return None
    assert result.http_exchanges_dropped == 0
    return result.http_exchanges[0].model_dump(mode="json")


def test_the_vector_file_names_every_case_once_and_grows_only():
    names = [case["name"] for case in CASES]
    assert len(names) == len(set(names)) >= 30
    assert VECTORS["version"] == 1
    assert all(set(case) == {"name", "note", "input", "expect"} for case in CASES)


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_every_vector_case(case: dict[str, Any]):
    assert _server(expand(case["input"])) == expand(case["expect"])


@pytest.mark.parametrize("case", [c for c in CASES if c["expect"] is not None],
                         ids=[c["name"] for c in CASES if c["expect"] is not None])
def test_redacting_the_expected_exchange_again_changes_nothing(case: dict[str, Any]):
    expect = expand(case["expect"])
    assert HttpExchangeIn.model_validate(expect).model_dump(mode="json") == expect
    assert _server(expect) == expect


def test_no_raw_sample_survives_any_vector_case():
    stored = json.dumps([_server(expand(case["input"])) for case in CASES], ensure_ascii=False)
    sent = json.dumps([expand(case["input"]) for case in CASES], ensure_ascii=False)
    for fragment in RAW_FRAGMENTS:
        assert fragment in sent, f"the sample {fragment!r} is not in the vector inputs any more"
        assert fragment not in stored, fragment


# ── the object-storage copy: IngestPayload.model_dump() ─────────────────────────


def _payload(results: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {"project_id": "00000000-0000-4000-8000-0000000000aa", "build_number": "w23d-41",
            "results": results, **extra}


def _result(name: str, exchanges: Any = None, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"test_name": name, "status": "FAILED", **extra}
    if exchanges is not None:
        out["http_exchanges"] = exchanges
    return out


def test_the_queued_batch_copy_carries_no_raw_sample():
    inputs = [expand(case["input"]) for case in CASES]
    results = [_result(f"test_chunk_{i}", inputs[i:i + 20]) for i in range(0, len(inputs), 20)]
    payload = IngestPayload.model_validate(_payload(results))
    dumped = json.dumps(payload.model_dump(mode="json"), ensure_ascii=False)
    for fragment in RAW_FRAGMENTS:
        assert fragment not in dumped, fragment
    assert vocab.REDACTED in dumped
    kept = sum(len(result.http_exchanges or []) for result in payload.results)
    assert kept == sum(1 for case in CASES if case["expect"] is not None)


def test_a_batch_without_exchanges_dumps_exactly_as_before():
    payload = IngestPayload.model_validate(_payload([_result("test_plain")]))
    dumped = payload.model_dump()
    assert {"run_ai"} & set(dumped) == set()
    assert {"http_exchanges", "http_exchanges_dropped", "service_name"} & set(dumped["results"][0]) == set()
    assert IngestPayload.model_validate(_payload([_result("test_plain")], run_ai=False)).model_dump()["run_ai"] is False
    assert payload.run_ai is True


def test_service_name_is_carried_and_bounded():
    payload = IngestPayload.model_validate(_payload([_result("test_svc", service_name="checkout")]))
    assert payload.model_dump()["results"][0]["service_name"] == "checkout"
    with pytest.raises(ValueError):
        IngestTestResult.model_validate(_result("test_svc", service_name="s" * 256))


def test_a_malformed_exchange_is_dropped_without_a_422_on_the_batch():
    good = {"method": "get", "url": "https://api.example.test/api/products/7", "status": 200}
    bad = [{"method": "get me", "url": "https://api.example.test/x"}, {"method": "GET"}, {"method": "GET", "url": " "},
           "not an object", {"method": "GET", "url": "https://api.example.test/x", "status": 700},
           {"method": "GET", "url": "https://api.example.test/x", "started_at": "yesterday-ish"}]
    payload = IngestPayload.model_validate(_payload([_result("test_mixed", [good, *bad]), _result("test_other")]))
    first, second = payload.results
    assert [ex.method for ex in first.http_exchanges or []] == ["GET"]
    assert first.http_exchanges_dropped == len(bad)
    assert second.test_name == "test_other" and second.http_exchanges is None


@pytest.mark.parametrize("raw", [None, "not a list", {"method": "GET"}, 7])
def test_a_non_list_is_no_exchange(raw: Any):
    result = IngestTestResult.model_validate({"test_name": "test_shape", "status": "PASSED", "http_exchanges": raw})
    if raw is None:
        assert result.http_exchanges is None and result.http_exchanges_dropped is None
    else:
        assert result.http_exchanges == [] and result.http_exchanges_dropped == 0


def test_a_client_sent_dropped_count_is_never_trusted():
    alone = IngestTestResult.model_validate(_result("test_lie", http_exchanges_dropped=99))
    assert alone.http_exchanges_dropped is None
    mixed = IngestTestResult.model_validate(
        _result("test_lie", [{"method": "GET", "url": "/a"}], http_exchanges_dropped=99))
    assert mixed.http_exchanges_dropped == 0


def test_twenty_one_exchanges_keep_the_first_twenty():
    wires = [{"method": "GET", "url": f"https://api.example.test/api/items/{n}"} for n in range(21)]
    result = IngestTestResult.model_validate(_result("test_many", wires))
    assert len(result.http_exchanges or []) == vocab.EXCHANGES_PER_TEST_MAX == 20
    assert result.http_exchanges_dropped == 1
    assert (result.http_exchanges or [])[-1].url.endswith("/api/items/19")
    live = LiveHttpExchangesIn.model_validate(
        {"session_id": "s-w23d", "run_id": "r-w23d", "test_name": "test_many", "exchanges": wires,
         "exchanges_dropped": 0})
    assert (len(live.exchanges), live.exchanges_dropped) == (20, 1)


def test_the_per_run_cap_keeps_exchanges_in_result_order():
    wire = {"method": "GET", "url": "https://api.example.test/api/products/7", "status": 200}
    results = [_result(f"test_r{n}", [wire] * 20) for n in range(51)]
    payload = IngestPayload.model_validate(_payload(results))
    kept = [len(result.http_exchanges or []) for result in payload.results]
    assert sum(kept) == vocab.EXCHANGES_PER_RUN_MAX == 1_000
    assert kept[:50] == [20] * 50 and kept[50] == 0
    assert payload.results[50].http_exchanges_dropped == 20
    assert all(result.http_exchanges_dropped == 0 for result in payload.results[:50])


def test_the_per_run_cap_cuts_inside_a_result(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(schemas, "EXCHANGES_PER_RUN_MAX", 25)
    wire = {"method": "GET", "url": "https://api.example.test/api/products/7"}
    payload = IngestPayload.model_validate(_payload([
        _result("test_a", [wire] * 21), _result("test_b"), _result("test_c", [wire] * 20), _result("test_d", [wire])]))
    a, b, c, d = payload.results
    assert (len(a.http_exchanges or []), a.http_exchanges_dropped) == (20, 1)
    assert b.http_exchanges is None
    assert (len(c.http_exchanges or []), c.http_exchanges_dropped) == (5, 15)
    assert (d.http_exchanges, d.http_exchanges_dropped) == ([], 1)


# ── module rules the vectors do not spell out ──────────────────────────────────


def test_headers_are_capped_after_sorting_by_name():
    headers = {f"X-H-{n:02d}": f"value-{n}" for n in range(55, 0, -1)}
    stored, count = hr.redact_headers(headers)
    assert list(stored) == sorted(headers)[: vocab.HEADERS_MAX]
    assert count == 0
    long_value, _ = hr.redact_headers({"X-Note": "w23d note " * 300})
    assert len(long_value["X-Note"]) == vocab.HEADER_VALUE_MAX


def test_the_url_and_the_endpoint_are_capped():
    url, _ = hr.redact_url("https://api.example.test/" + "seg/" * 700)
    assert len(url) == vocab.URL_MAX
    assert len(hr.derive_endpoint("https://api.example.test/" + "seg/" * 700)) == vocab.ENDPOINT_MAX


def test_url_host_never_raises_on_a_redacted_userinfo():
    assert hr.url_host("https://w23d:[REDACTED]@API.example.test:8443/x?y=1") == "api.example.test"
    assert hr.url_host("http://[::1]:9090/metrics") == "[::1]"
    assert hr.url_host("/relative/only") is None


def test_the_transport_error_gets_the_text_pass_and_its_cap():
    wire = {"method": "GET", "url": "https://api.example.test/x", "error": "ConnectError https://w23d:samplepass@h/x"}
    stored = HttpExchangeIn.model_validate(wire)
    assert stored.error is not None and "samplepass" not in stored.error
    assert HttpExchangeIn.model_validate(stored.model_dump()).error == stored.error
