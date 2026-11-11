from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

import pytest

import fakes_config
from fakes import probe


def test_compose_mounts_standalone_probe(tmp_path, monkeypatch):
    monkeypatch.setattr(fakes_config.subprocess, "run", lambda *args, **kwargs: None)
    config = {
        "services": {
            name: {"environment": []}
            for name in ("backend", "worker", "worker-children")
        }
    }
    fakes_config.configure(config, tmp_path, "podman", "test-image")
    service = config["services"][fakes_config.FAKE_HOST]
    assert service["healthcheck"]["test"] == ["CMD", "python", "/fixture/probe.py"]
    assert "./release-fixture:/fixture:ro" in service["volumes"]
    assert (tmp_path / "release-fixture/probe.py").read_bytes() == (
        fakes_config.ROOT / "e2e-runner/fakes/probe.py"
    ).read_bytes()


@pytest.mark.parametrize(
    ("status", "body", "expected_exit", "diagnostic"),
    [
        (200, b'{"version":"0.13.0"}', 0, ""),
        (503, b"private response", 1, "HTTP 503"),
        (200, b"private response", 1, "JSONDecodeError"),
        (200, b'{"message":"private response"}', 1, "unexpected payload"),
    ],
)
def test_probe_checks_fixture_response(status, body, expected_exit, diagnostic, capsys):
    paths = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            self.send_response(status)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        assert probe.check(server.server_port) == expected_exit
        thread.join(timeout=3)
    assert paths == ["/api/version"]
    stderr = capsys.readouterr().err
    assert diagnostic in stderr
    assert "private response" not in stderr
