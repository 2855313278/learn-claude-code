from copy import deepcopy
from pathlib import Path

import pytest

from provider_lab.providers import DemoProvider, ProviderError, ToolCall, Turn
from provider_lab.runtime import Workspace, run_agent


class ScriptedProvider:
    name = "scripted"
    model = "test"

    def __init__(self, *turns):
        self.turns = iter(turns)
        self.requests = []

    def generate(self, messages, tools, system):
        self.requests.append(deepcopy(messages))
        return next(self.turns)


def test_demo_runs_are_independent_and_have_trace(tmp_path):
    (tmp_path / "说明.md").write_text("hello", encoding="utf-8")
    provider = DemoProvider()
    first = run_agent(provider, Workspace(tmp_path), "list")
    second = run_agent(provider, Workspace(tmp_path), "again")
    assert first.status == second.status == "completed"
    assert first.run_id != second.run_id
    assert "说明.md" in first.answer
    assert first.steps == 2
    assert first.to_dict()["trace"][0]["tools"][0]["name"] == "list_files"


def test_multiple_tool_calls_receive_paired_results_and_errors(tmp_path):
    (tmp_path / "note.md").write_text("你好\n", encoding="utf-8", newline="\n")
    provider = ScriptedProvider(
        Turn(
            calls=(
                ToolCall("a", "read_file", {"path": "note.md"}),
                ToolCall("b", "bash", {"path": "."}),
            )
        ),
        Turn("Finished"),
    )
    result = run_agent(provider, Workspace(tmp_path), "read")
    results = provider.requests[1][-1]["content"]
    assert results[0]["tool_use_id"] == "a" and results[0]["content"] == "你好\n"
    assert results[0]["is_error"] is False
    assert results[1]["tool_use_id"] == "b" and results[1]["is_error"] is True
    assert result.answer == "Finished"


@pytest.mark.parametrize("budget", [0, 21, True, 1.5, "4"])
def test_invalid_budgets_rejected(tmp_path, budget):
    with pytest.raises(ValueError, match="max_steps"):
        run_agent(DemoProvider(), Workspace(tmp_path), "hi", budget)


@pytest.mark.parametrize(
    "prompt", ["", "  ", None, "x" * 8001], ids=["empty", "blank", "null", "long"]
)
def test_invalid_prompts_rejected(tmp_path, prompt):
    with pytest.raises(ValueError, match="prompt"):
        run_agent(DemoProvider(), Workspace(tmp_path), prompt)


def test_step_budget_stops_before_executing_unusable_tools(tmp_path):
    result = run_agent(DemoProvider(), Workspace(tmp_path), "list", 1)
    assert result.status == "step_limit"
    assert result.steps == 1
    assert result.trace[0]["tools"] == []
    assert result.trace[0]["pending_tools"] == ["list_files"]


def test_output_limit_does_not_report_success_or_execute_partial_call(tmp_path):
    provider = ScriptedProvider(
        Turn("partial", (ToolCall("a", "list_files", {"path": "."}),), True)
    )
    result = run_agent(provider, Workspace(tmp_path), "hi")
    assert result.status == "output_limit" and result.trace[0]["tools"] == []


@pytest.mark.parametrize(
    "turn",
    [
        Turn(),
        Turn(calls=(ToolCall("same", "x", {}), ToolCall("same", "y", {}))),
        Turn(calls=(ToolCall("x", "read_file", "not-a-dict"),)),
        Turn(calls=tuple(ToolCall(str(i), "x", {}) for i in range(9))),
    ],
    ids=["empty", "duplicate", "invalid-arguments", "too-many"],
)
def test_invalid_model_turns_raise_provider_error(tmp_path, turn):
    with pytest.raises(ProviderError):
        run_agent(ScriptedProvider(turn), Workspace(tmp_path), "hi")


@pytest.mark.parametrize(
    "path",
    [
        "../outside.md",
        ".env",
        ".git/config",
        "credentials.json",
        "secrets.yaml",
        "x:y",
        "",
        None,
    ],
)
def test_paths_are_contained_and_hidden_files_excluded(tmp_path, path):
    result = Workspace(tmp_path).execute("read_file", {"path": path})
    assert result.startswith("Error:")
    assert str(tmp_path) not in result


def test_absolute_path_and_symlink_escape_are_rejected(tmp_path):
    outside = tmp_path.parent / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    workspace = Workspace(tmp_path)
    assert workspace.execute("read_file", {"path": str(outside)}).startswith("Error:")
    link = tmp_path / "link.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Host does not grant symlink creation privileges")
    assert workspace.execute("read_file", {"path": "link.md"}).startswith("Error:")


def test_file_and_listing_limits_are_visible(tmp_path):
    (tmp_path / "large.md").write_bytes(b"a" * 20_000)
    (tmp_path / ".env").write_text("secret", encoding="utf-8")
    for i in range(201):
        (tmp_path / f"f{i}.md").touch()
    workspace = Workspace(tmp_path)
    content = workspace.execute("read_file", {"path": "large.md"})
    assert content.startswith("a" * 16_384)
    assert "file truncated" in content
    listing = workspace.execute("list_files", {"path": "."})
    assert ".env" not in listing and "listing truncated" in listing
    assert len(listing.splitlines()) == 201


def test_only_supported_regular_text_files_are_read(tmp_path):
    (tmp_path / "image.png").write_bytes(b"binary")
    workspace = Workspace(tmp_path)
    for name, args in [
        ("read_file", {"path": "image.png"}),
        ("read_file", {"path": "."}),
        ("list_files", {"path": "image.png"}),
        ("read_file", {"path": "x", "extra": 1}),
    ]:
        assert workspace.execute(name, args).startswith("Error:")


def test_workspace_requires_directory(tmp_path):
    file = tmp_path / "note.md"
    file.touch()
    with pytest.raises(ValueError, match="directory"):
        Workspace(file)
    with pytest.raises(FileNotFoundError):
        Workspace(Path(tmp_path / "missing"))
