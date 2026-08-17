"""Run the reproducible L1 acceptance checks used by the Docker image."""

from __future__ import annotations

import os
import subprocess
import sys


def _run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    completed = subprocess.run(command, env=env, check=False)
    if completed.returncode:
        raise SystemExit(completed.returncode)


def main() -> int:
    integration_env = os.environ.copy()
    integration_env["LOOMQ_RUN_SDK_INTEGRATION"] = "1"

    _run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        env=integration_env,
    )
    _run(
        [
            sys.executable,
            "evaluator.py",
            "--level",
            "l1",
            "--target",
            "spinq,originq,braket",
            "--json-out",
            "/tmp/loomq-public-report.json",
        ]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
