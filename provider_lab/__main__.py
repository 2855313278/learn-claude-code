"""Run with python -m provider_lab; demo is the explicit offline default."""

from __future__ import annotations

import argparse
import json
import sys

from provider_lab.api import create_server
from provider_lab.providers import ProviderError, make_provider
from provider_lab.runtime import Workspace, run_agent


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Read-only agent lab: Claude, Ollama, offline demo"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser(
        "run", help="Run one prompt and print a JSON execution trace"
    )
    run.add_argument("prompt")
    run.add_argument(
        "--max-steps", type=int, default=4, help="Model-call budget, 1..20 (default: 4)"
    )
    serve = commands.add_parser("serve", help="Start the loopback JSON development API")
    serve.add_argument("--port", type=int, default=8000)
    for command in (run, serve):
        command.add_argument(
            "--provider", choices=["demo", "anthropic", "ollama"], default="demo"
        )
        command.add_argument(
            "--model", help="Required for online providers; no hardcoded cloud model"
        )
        command.add_argument(
            "--workspace", default=".", help="Trusted local directory to inspect"
        )
    args = parser.parse_args(argv)
    try:
        if args.command == "serve" and not 0 <= args.port <= 65535:
            raise ValueError("port must be in 0..65535")
        provider = make_provider(args.provider, args.model)
        if args.command == "run":
            result = run_agent(
                provider, Workspace(args.workspace), args.prompt, args.max_steps
            )
            print(json.dumps(result.to_dict(), ensure_ascii=False))
            return 0 if result.status == "completed" else 1
        with create_server(provider, args.workspace, port=args.port) as server:
            print(
                json.dumps(
                    {
                        "url": f"http://127.0.0.1:{server.server_port}",
                        "provider": provider.name,
                        "model": provider.model,
                    }
                ),
                flush=True,
            )
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
        return 0
    except (ValueError, OSError, ProviderError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except ImportError:
        print(
            json.dumps({"error": "Install requirements.txt for the Anthropic SDK"}),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
