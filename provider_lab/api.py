"""Small loopback-only JSON API for the shared agent runtime."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from provider_lab.providers import Provider, ProviderError
from provider_lab.runtime import Workspace, run_agent

MAX_BODY = 16_384


def create_server(
    provider: Provider,
    root: str | Path,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("The development API only binds to IPv4 loopback")
    workspace = Workspace(root)
    slots = threading.BoundedSemaphore(2)

    class Handler(BaseHTTPRequestHandler):
        server_version = "AgentProviderLab/1"
        sys_version = ""

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *args):
            # Prompts and provider responses do not belong in access logs.
            pass

        def reply(self, status: int, body: dict):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def local_request(self) -> bool:
            allowed = {
                f"127.0.0.1:{self.server.server_port}",
                f"localhost:{self.server.server_port}",
            }
            if (
                self.headers.get("Origin")
                or self.headers.get("Host", "").lower() not in allowed
            ):
                self.reply(
                    403, {"error": "Only direct local API clients are supported"}
                )
                return False
            return True

        def do_GET(self):
            if not self.local_request():
                return
            if self.path != "/health":
                self.reply(404, {"error": "Route not found"})
                return
            self.reply(
                200,
                {"status": "ok", "provider": provider.name, "model": provider.model},
            )

        def do_POST(self):
            if not self.local_request():
                return
            if self.path != "/v1/runs":
                self.reply(404, {"error": "Route not found"})
                return
            if self.headers.get_content_type() != "application/json":
                self.reply(415, {"error": "Use application/json"})
                return
            if self.headers.get("Transfer-Encoding"):
                self.reply(400, {"error": "Transfer-Encoding is unsupported"})
                return
            if self.headers.get("Content-Length") is None:
                self.reply(411, {"error": "Content-Length is required"})
                return
            try:
                size = int(self.headers["Content-Length"])
            except ValueError:
                self.reply(400, {"error": "Invalid Content-Length"})
                return
            if not 0 < size <= MAX_BODY:
                self.reply(413, {"error": "Body must contain 1..16384 bytes"})
                return
            if not slots.acquire(blocking=False):
                self.reply(429, {"error": "Two runs are already active; retry later"})
                return
            try:
                raw = self.rfile.read(size)
                if len(raw) != size:
                    raise ValueError("Incomplete request body")
                body = json.loads(raw)
                if not isinstance(body, dict) or set(body) - {"prompt", "max_steps"}:
                    raise ValueError("Allowed fields: prompt, max_steps")
                result = run_agent(
                    provider, workspace, body.get("prompt"), body.get("max_steps", 4)
                )
                self.reply(200, result.to_dict())
            except (ValueError, UnicodeError) as error:
                # JSON errors may contain input snippets; return a fixed message.
                message = (
                    "Invalid JSON body"
                    if isinstance(error, json.JSONDecodeError)
                    else str(error)
                )
                self.reply(400, {"error": message})
            except ProviderError as error:
                self.reply(502, {"error": str(error)})
            except TimeoutError:
                self.reply(408, {"error": "Request body timed out"})
            finally:
                slots.release()

    return ThreadingHTTPServer((host, port), Handler)
