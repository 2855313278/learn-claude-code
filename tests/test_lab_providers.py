import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from provider_lab.providers import (
    AnthropicProvider,
    OllamaProvider,
    ProviderError,
    ToolCall,
    Turn,
    make_provider,
    ollama_messages,
)


@pytest.fixture
def ollama_server():
    state = {"requests": [], "reply": {"message": {"content": "你好"}, "done": True}}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            state["requests"].append(
                (
                    self.path,
                    json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                )
            )
            self.send_response(state.get("status", 200))
            self.end_headers()
            reply = state["reply"]
            self.wfile.write(
                reply if isinstance(reply, bytes) else json.dumps(reply).encode()
            )

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield state, f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_turn_serialization_and_multi_tool_history():
    turn = Turn("Looking", (ToolCall("a", "read_file", {"path": "README.md"}),))
    messages = [
        {"role": "user", "content": "read"},
        {"role": "assistant", "content": turn.content()},
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "a", "content": "contents"}
            ],
        },
    ]
    converted = ollama_messages(messages, "system")
    assert converted[2]["tool_calls"][0]["function"]["arguments"] == {
        "path": "README.md"
    }
    assert converted[3] == {
        "role": "tool",
        "tool_name": "read_file",
        "content": "contents",
    }
    assert converted[2]["content"] == "Looking"


def test_demo_is_stateless_and_does_not_need_credentials(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    provider = make_provider()
    request = [{"role": "user", "content": "anything"}]
    assert provider.generate(request, [], "").calls[0].name == "list_files"
    assert provider.generate(request, [], "").calls[0].name == "list_files"
    assert (
        "Offline demo completed"
        in provider.generate(
            [{"role": "user", "content": [{"content": "one.py"}]}], [], ""
        ).text
    )


@pytest.mark.parametrize("name", ["anthropic", "ollama", "unknown"])
def test_online_providers_require_explicit_model(name):
    with pytest.raises(ValueError, match="explicit"):
        make_provider(name)


def test_unknown_provider_and_missing_key(monkeypatch):
    with pytest.raises(ValueError, match="Unknown"):
        make_provider("unknown", "model")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        make_provider("anthropic", "model")


@pytest.mark.parametrize(
    "url",
    [
        "ftp://host",
        "http://user:secret@host",
        "http://host/api",
        "http://host?key=x",
        "",
    ],
)
def test_ollama_rejects_invalid_origins(url):
    with pytest.raises(ValueError):
        OllamaProvider("model", url)


def test_ollama_real_http_tool_request_and_usage(ollama_server, monkeypatch):
    state, url = ollama_server
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    state["reply"] = {
        "message": {
            "content": "",
            "tool_calls": [
                {"function": {"name": "list_files", "arguments": {"path": "."}}}
            ],
        },
        "done": True,
        "prompt_eval_count": 12,
        "eval_count": 3,
    }
    monkeypatch.setenv("OLLAMA_HOST", url)
    provider = make_provider("ollama", "local-model")
    turn = provider.generate(
        [{"role": "user", "content": "list"}],
        [
            {
                "name": "list_files",
                "description": "list",
                "input_schema": {"type": "object"},
            }
        ],
        "system",
    )
    assert turn.calls[0].name == "list_files"
    assert turn.usage == {"input_tokens": 12, "output_tokens": 3}
    path, body = state["requests"][0]
    assert path == "/api/chat"
    assert body["model"] == "local-model"
    assert body["stream"] is False
    assert body["tools"][0]["function"]["parameters"] == {"type": "object"}
    assert body["messages"][0] == {"role": "system", "content": "system"}


@pytest.mark.parametrize(
    "reply",
    [
        b"not json",
        {"error": "secret"},
        {"message": {"content": 123}, "done": True},
        {"message": {"content": "partial"}, "done": False},
        b"x" * 2_000_001,
    ],
    ids=["invalid-json", "remote-error", "invalid-text", "incomplete", "oversized"],
)
def test_ollama_malformed_responses_are_safe(ollama_server, reply):
    state, url = ollama_server
    state["reply"] = reply
    with pytest.raises(ProviderError, match="Ollama request failed") as error:
        OllamaProvider("model", url).generate([], [], "")
    assert "secret" not in str(error.value)


def test_ollama_http_error_and_length_stop(ollama_server):
    state, url = ollama_server
    state["status"] = 500
    with pytest.raises(ProviderError):
        OllamaProvider("model", url).generate([], [], "")
    state["status"] = 200
    state["reply"] = {
        "message": {"content": "partial"},
        "done": True,
        "done_reason": "length",
    }
    assert OllamaProvider("model", url).generate([], [], "").truncated


def test_anthropic_maps_content_usage_and_request():
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(
            content=[
                SimpleNamespace(type="text", text="Reading"),
                SimpleNamespace(
                    type="tool_use", id="id1", name="read_file", input={"path": "x"}
                ),
            ],
            stop_reason="tool_use",
            usage=SimpleNamespace(input_tokens=9, output_tokens=4),
        )

    provider = AnthropicProvider(
        "explicit-model", SimpleNamespace(messages=SimpleNamespace(create=create))
    )
    history = [{"role": "user", "content": "hi"}]
    turn = provider.generate(history, [], "system")
    assert turn.calls == (ToolCall("id1", "read_file", {"path": "x"}),)
    assert turn.text == "Reading" and not turn.truncated
    assert turn.usage["input_tokens"] == 9
    assert requests[0]["messages"] is history
    assert requests[0]["model"] == "explicit-model"


def test_anthropic_malformed_response_does_not_expose_remote_details():
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kwargs: None))
    with pytest.raises(ProviderError, match="Anthropic request failed"):
        AnthropicProvider("model", client).generate([], [], "")
