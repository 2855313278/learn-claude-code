import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.client import HTTPConnection

import pytest

from provider_lab.api import create_server
from provider_lab.providers import DemoProvider, ProviderError, Turn


@contextmanager
def serving(tmp_path, provider=None):
    server = create_server(provider or DemoProvider(), tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def api(tmp_path):
    (tmp_path / "note.md").write_text("你好", encoding="utf-8")
    with serving(tmp_path) as port:
        yield port


def request(port, method="POST", path="/v1/runs", body=None, headers=None):
    connection = HTTPConnection("127.0.0.1", port, timeout=10)
    data = json.dumps({"prompt": "list"} if body is None else body).encode("utf-8")
    try:
        connection.request(
            method, path, data, {"Content-Type": "application/json", **(headers or {})}
        )
        response = connection.getresponse()
        return (
            response.status,
            json.loads(response.read()),
            response.getheader("Content-Type"),
        )
    finally:
        connection.close()


def test_health_is_local_and_does_not_expose_secrets(api):
    status, body, content_type = request(api, "GET", "/health")
    assert status == 200 and body == {
        "status": "ok",
        "provider": "demo",
        "model": "offline-demo",
    }
    assert content_type == "application/json; charset=utf-8"


def test_json_run_has_real_tool_roundtrip_and_isolated_history(api):
    status, first, _ = request(api, body={"prompt": "列出文件", "max_steps": 4})
    _, second, _ = request(api)
    assert status == 200 and first["status"] == "completed"
    assert first["steps"] == second["steps"] == 2
    assert first["run_id"] != second["run_id"]
    assert first["trace"][0]["tools"][0]["output"] == "note.md"


@pytest.mark.parametrize("method,path", [("GET", "/missing"), ("POST", "/missing")])
def test_unknown_routes_are_json(api, method, path):
    assert request(api, method, path)[0] == 404


@pytest.mark.parametrize(
    "body",
    [
        {},
        [],
        {"prompt": ""},
        {"prompt": "x", "max_steps": True},
        {"prompt": "x", "max_steps": 21},
        {"prompt": "x", "provider": "anthropic"},
        {"prompt": "x", "root": ".."},
        {"prompt": "x", "base_url": "http://remote"},
    ],
)
def test_invalid_input_or_config_overrides_rejected(api, body):
    assert request(api, body=body)[0] == 400


@pytest.mark.parametrize(
    "headers", [{"Origin": "https://evil.example"}, {"Host": "evil.example:8000"}]
)
def test_browser_origins_and_host_rebinding_are_rejected(api, headers):
    assert request(api, headers=headers)[0] == 403


def test_media_type_and_body_size_limits(api):
    assert request(api, headers={"Content-Type": "text/plain"})[0] == 415
    assert request(api, body={"prompt": "x" * 20_000})[0] == 413
    assert request(api, headers={"Transfer-Encoding": "chunked"})[0] == 400


def test_malformed_json_response_does_not_echo_input(api):
    connection = HTTPConnection("127.0.0.1", api, timeout=5)
    try:
        connection.request(
            "POST",
            "/v1/runs",
            b"SECRET is not json",
            {"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 400
        body = response.read()
        assert b"Invalid JSON body" in body and b"SECRET" not in body
    finally:
        connection.close()


def test_content_length_must_be_valid(api):
    for length, expected in [(None, 411), ("no", 400), ("-1", 413), ("0", 413)]:
        connection = HTTPConnection("127.0.0.1", api, timeout=5)
        try:
            connection.putrequest("POST", "/v1/runs")
            connection.putheader("Content-Type", "application/json")
            if length is not None:
                connection.putheader("Content-Length", length)
            connection.endheaders()
            response = connection.getresponse()
            assert response.status == expected
            response.read()
        finally:
            connection.close()


def test_budget_exhaustion_is_distinct_from_success(api):
    status, body, _ = request(api, body={"prompt": "list", "max_steps": 1})
    assert status == 200 and body["status"] == "step_limit"


def test_provider_errors_return_bad_gateway(tmp_path):
    class FailedProvider(DemoProvider):
        def generate(self, *args):
            raise ProviderError("Provider unavailable")

    with serving(tmp_path, FailedProvider()) as port:
        status, body, _ = request(port)
        assert status == 502 and body == {"error": "Provider unavailable"}


def test_concurrency_limit_returns_429_and_releases_slots(tmp_path):
    ready = threading.Event()
    release = threading.Event()
    lock = threading.Lock()

    class BlockingProvider(DemoProvider):
        active = 0

        def generate(self, *args):
            with lock:
                self.active += 1
                if self.active == 2:
                    ready.set()
            assert release.wait(5)
            return Turn("done")

    with serving(tmp_path, BlockingProvider()) as port, ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(request, port) for _ in range(2)]
        try:
            assert ready.wait(5)
            assert request(port)[0] == 429
        finally:
            release.set()
        assert all(f.result()[0] == 200 for f in futures)
        assert request(port)[0] == 200


def test_non_loopback_bind_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="loopback"):
        create_server(DemoProvider(), tmp_path, host="0.0.0.0")
