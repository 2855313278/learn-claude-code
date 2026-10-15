"""Provider-neutral turns; adapters preserve tool call/result pairing."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Protocol
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener


class ProviderError(RuntimeError):
    """A safe diagnostic that never includes credentials or remote bodies."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class Turn:
    text: str = ""
    calls: tuple[ToolCall, ...] = ()
    truncated: bool = False
    usage: dict = field(default_factory=dict)

    def content(self) -> list[dict]:
        blocks = [{"type": "text", "text": self.text}] if self.text else []
        return blocks + [
            {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
            for c in self.calls
        ]


class Provider(Protocol):
    name: str
    model: str

    def generate(
        self, messages: list[dict], tools: list[dict], system: str
    ) -> Turn: ...


class DemoProvider:
    """Deterministic tool round trip, explicitly not a language model."""

    name = "demo"
    model = "offline-demo"

    def generate(self, messages: list[dict], tools: list[dict], system: str) -> Turn:
        last = messages[-1]["content"]
        if isinstance(last, str):
            return Turn(calls=(ToolCall("demo-list", "list_files", {"path": "."}),))
        return Turn(
            text="Offline demo completed. Workspace entries:\n" + last[0]["content"]
        )


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str, client=None):
        self.model = model
        if client is None:
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise ValueError("Set ANTHROPIC_API_KEY for the anthropic provider")
            from anthropic import Anthropic

            client = Anthropic(timeout=60.0, max_retries=0)
        self.client = client

    def generate(self, messages: list[dict], tools: list[dict], system: str) -> Turn:
        from anthropic import APIError

        try:
            response = self.client.messages.create(
                model=self.model,
                system=system,
                messages=messages,
                tools=tools,
                max_tokens=2048,
            )
            text = []
            calls = []
            for block in response.content:
                if block.type == "text":
                    text.append(block.text)
                elif block.type == "tool_use":
                    calls.append(ToolCall(block.id, block.name, block.input))
            return Turn(
                text="\n".join(text),
                calls=tuple(calls),
                truncated=response.stop_reason not in {"end_turn", "tool_use"},
                usage={
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                },
            )
        except (APIError, AttributeError, TypeError, ValueError):
            raise ProviderError(
                "Anthropic request failed; check model, key and connectivity"
            ) from None


def ollama_messages(messages: list[dict], system: str) -> list[dict]:
    """Convert canonical Anthropic-style blocks into native Ollama messages."""
    result = [{"role": "system", "content": system}]
    tool_names = {}
    for message in messages:
        content = message["content"]
        if isinstance(content, str):
            result.append({"role": message["role"], "content": content})
            continue
        if message["role"] == "assistant":
            row = {"role": "assistant", "content": ""}
            for block in content:
                if block["type"] == "text":
                    row["content"] += block["text"]
                elif block["type"] == "tool_use":
                    tool_names[block["id"]] = block["name"]
                    row.setdefault("tool_calls", []).append(
                        {
                            "function": {
                                "name": block["name"],
                                "arguments": block["input"],
                            }
                        }
                    )
            result.append(row)
        else:
            for block in content:
                result.append(
                    {
                        "role": "tool",
                        "tool_name": tool_names[block["tool_use_id"]],
                        "content": block["content"],
                    }
                )
    return result


class OllamaProvider:
    name = "ollama"

    def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434"):
        address = urlsplit(base_url)
        if (
            address.scheme not in {"http", "https"}
            or not address.hostname
            or address.username
            or address.password
            or address.query
            or address.fragment
            or address.path not in {"", "/"}
        ):
            raise ValueError(
                "Ollama URL must be an http(s) origin without credentials or path"
            )
        self.base_url = base_url.rstrip("/")
        self.model = model
        # Local models must not be accidentally routed through a system proxy.
        self.opener = (
            build_opener(ProxyHandler({}))
            if address.hostname in {"localhost", "127.0.0.1", "::1"}
            else build_opener()
        )

    def generate(self, messages: list[dict], tools: list[dict], system: str) -> Turn:
        payload = {
            "model": self.model,
            "messages": ollama_messages(messages, system),
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": t["input_schema"],
                    },
                }
                for t in tools
            ],
            "stream": False,
            "options": {"num_predict": 2048},
        }
        request = Request(
            self.base_url + "/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with self.opener.open(request, timeout=60) as response:
                data = response.read(2_000_001)
            if len(data) > 2_000_000:
                raise ValueError("response too large")
            body = json.loads(data)
            message = body["message"]
            calls = tuple(
                ToolCall(
                    f"ollama-{len(messages)}-{i}",
                    c["function"]["name"],
                    c["function"]["arguments"],
                )
                for i, c in enumerate(message.get("tool_calls", []))
            )
            text = message.get("content", "")
            if not isinstance(text, str) or body.get("done") is not True:
                raise ValueError("incomplete response")
            return Turn(
                text=text,
                calls=calls,
                truncated=body.get("done_reason") == "length",
                usage={
                    "input_tokens": body.get("prompt_eval_count", 0),
                    "output_tokens": body.get("eval_count", 0),
                },
            )
        except (URLError, OSError, ValueError, KeyError, TypeError):
            raise ProviderError(
                "Ollama request failed; check server, model and response format"
            ) from None


def make_provider(name: str = "demo", model: str | None = None) -> Provider:
    if name == "demo":
        return DemoProvider()
    if not isinstance(model, str) or not model.strip():
        raise ValueError("An explicit --model is required for online providers")
    if name == "anthropic":
        return AnthropicProvider(model)
    if name == "ollama":
        return OllamaProvider(model, os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434"))
    raise ValueError("Unknown provider; choose demo, anthropic or ollama")
