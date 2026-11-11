"""Internal-only release-test fixture service. Contains synthetic data exclusively."""

import asyncio
import json
import re
import threading
import time

import uvicorn
from fake_integrations import app as integrations
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route


probe_timeout_until = 0.0


async def delay_model_probe(request: Request):
    global probe_timeout_until
    # Automatically restore the test provider if a test runner exits mid-control.
    probe_timeout_until = time.monotonic() + 20 if request.method == "POST" else 0.0
    return JSONResponse({"delay_enabled": request.method == "POST"})


async def ollama(request: Request):
    if request.url.path.startswith(("/api/v2/", "/api/v1/")):
        return JSONResponse(
            {"error": "Semantic index disabled for this release fixture"},
            status_code=503,
        )
    if request.url.path == "/api/tags":
        if time.monotonic() < probe_timeout_until:
            await asyncio.sleep(5)
        return JSONResponse(
            {"models": [{"name": "release-fixture", "model": "release-fixture"}]}
        )
    if request.url.path == "/api/show":
        return JSONResponse({"model_info": {}, "capabilities": ["completion", "tools"]})
    if request.url.path == "/api/version":
        return JSONResponse({"version": "0.13.0"})
    payload = await request.json()
    messages = payload.get("messages", [])
    prompt = "\n".join(str(msg.get("content", "")) for msg in messages)
    if request.url.path == "/api/embed":
        return JSONResponse(
            {"embeddings": [[0.125] * 384 for _ in payload.get("input", [])]}
        )
    if request.url.path not in ("/api/chat", "/api/generate"):
        return JSONResponse({"error": "unknown fixture route"}, status_code=404)
    # Only repeat fixture case names explicitly present in the evidence sent by the app.
    leaves = sorted(set(re.findall(r"journey_[a-z0-9]+_failure_[ab]\b", prompt)))
    response = (
        "Recorded failing cases: " + ", ".join(leaves)
        if leaves
        else "Synthetic analysis of the supplied test evidence."
    )
    if payload.get("format") or "json" in prompt.lower() and not leaves:
        response = json.dumps(
            {
                "summary": response,
                "root_cause": "Fixture assertion mismatch",
                "failure_category": "TEST_BUG",
                "confidence_score": 85,
                "recommended_action": "Check the recorded assertion",
                "recommendations": ["Review the failing test"],
                "verdict": "accept",
                "findings": [],
                "anomalies": [],
                "risks": [],
            }
        )
    event = {
        "model": payload.get("model", "release-fixture"),
        "created_at": "2026-01-01T00:00:00Z",
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 12,
        "eval_count": 8,
    }
    if request.url.path == "/api/chat":
        event["message"] = {"role": "assistant", "content": response}
    else:
        event["response"] = response
    if payload.get("stream", True):
        return Response(json.dumps(event) + "\n", media_type="application/x-ndjson")
    return JSONResponse(event)


application = Starlette(
    routes=[
        Route(
            "/_release/model-probe-delay", delay_model_probe, methods=["POST", "DELETE"]
        ),
        Route("/api/{path:path}", ollama, methods=["GET", "POST"]),
    ]
)
application.mount("/", integrations)


if __name__ == "__main__":
    # Both listeners use the same fake state. The application trusts only this
    # test-run certificate for Jira. The test runner reads the ledger over HTTP.
    tls = threading.Thread(
        target=uvicorn.run,
        args=(application,),
        kwargs={
            "host": "0.0.0.0",
            "port": 8443,
            "ssl_certfile": "/fixture-tls/ca.pem",
            "ssl_keyfile": "/fixture-tls/key.pem",
            "access_log": False,
        },
        daemon=True,
    )
    tls.start()
    uvicorn.run(application, host="0.0.0.0", port=8080, access_log=False)
