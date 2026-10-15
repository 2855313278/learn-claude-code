"""Run lab release checks; --upstream adds the original Linux/Python 3.11 suite."""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAB_TESTS = [
    "tests/test_lab_providers.py",
    "tests/test_lab_runtime.py",
    "tests/test_lab_api.py",
    "tests/test_lab_cli.py",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--upstream",
        action="store_true",
        help="Include all upstream tests (upstream baseline: Linux, Python 3.11)",
    )
    args = parser.parse_args()
    paths = ["provider_lab", "scripts/verify_lab.py", *LAB_TESTS]
    commands = [
        ["pip", "check"],
        ["ruff", "check", "--config", "ruff-lab.toml", *paths],
        ["ruff", "format", "--check", "--config", "ruff-lab.toml", *paths],
        ["compileall", "-q", "provider_lab"],
        [
            "pytest",
            *(
                ["tests"]
                if args.upstream
                else [
                    *LAB_TESTS,
                    "tests/test_agents_smoke.py",
                    "tests/test_chapter_readmes.py",
                    "tests/test_web_scenarios.py::test_generated_s16_metadata_extends_s15_without_registry_false_positives",
                ]
            ),
            "-q",
        ],
    ]
    for arguments in commands:
        # Upstream tests deliberately mock the locale to exercise GBK decoding.
        # Forced UTF-8 mode bypasses that mock inside subprocess.Popen.
        utf8_mode = "utf8=0" if args.upstream and arguments[0] == "pytest" else "utf8"
        command = [sys.executable, "-X", utf8_mode, "-m", *arguments]
        print("Running:", " ".join(command), flush=True)
        result = subprocess.run(command, cwd=ROOT, check=False)
        if result.returncode:
            return result.returncode
    print("All requested checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
