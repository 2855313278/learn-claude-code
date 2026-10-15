import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]


def command(*args):
    return [sys.executable, "-m", "provider_lab", *args]


def environment():
    return {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC_")}


def test_demo_cli_outputs_utf8_json_and_success_exit(tmp_path):
    (tmp_path / "中文.md").write_text("text", encoding="utf-8")
    result = subprocess.run(
        command("run", "列出文件", "--workspace", str(tmp_path)),
        check=False,
        cwd=ROOT,
        env=environment(),
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0 and not result.stderr
    body = json.loads(result.stdout)
    assert "中文.md" in body["answer"] and body["provider"] == "demo"


def test_budget_limit_has_nonzero_exit_and_trace(tmp_path):
    result = subprocess.run(
        command("run", "list", "--workspace", str(tmp_path), "--max-steps", "1"),
        check=False,
        cwd=ROOT,
        env=environment(),
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["status"] == "step_limit"


@pytest.mark.parametrize(
    "args",
    [
        ("run", "hi", "--provider", "anthropic"),
        ("run", "hi", "--provider", "anthropic", "--model", "explicit"),
        ("run", "hi", "--max-steps", "21"),
        ("serve", "--port", "99999"),
    ],
)
def test_configuration_errors_are_json_and_do_not_call_network(args):
    result = subprocess.run(
        command(*args),
        check=False,
        cwd=ROOT,
        env=environment(),
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "error" in json.loads(result.stderr)
    assert not result.stdout


def test_help_is_available_without_sdk_or_keys():
    result = subprocess.run(
        command("--help"),
        check=False,
        cwd=ROOT,
        env=environment(),
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0 and b"run,serve" in result.stdout


def test_serve_cli_accepts_real_http_and_prints_bound_url(tmp_path):
    process = subprocess.Popen(
        command("serve", "--workspace", str(tmp_path), "--port", "0"),
        cwd=ROOT,
        env=environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        with ThreadPoolExecutor(1) as pool:
            try:
                line = pool.submit(process.stdout.readline).result(timeout=10)
            except TimeoutError:
                process.terminate()
                raise
        info = json.loads(line)
        assert info["provider"] == "demo"
        address = urlsplit(info["url"])
        assert address.hostname == "127.0.0.1"
        connection = HTTPConnection(address.hostname, address.port, timeout=5)
        try:
            connection.request("GET", "/health")
            response = connection.getresponse()
            assert (
                response.status == 200 and json.loads(response.read())["status"] == "ok"
            )
        finally:
            connection.close()
    finally:
        process.terminate()
        process.communicate(timeout=5)
