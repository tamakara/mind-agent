import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPOSITORY_ROOT = Path(__file__).parents[2]


def test_mock_oa_module_entrypoint_reports_version() -> None:
    environment = os.environ.copy()
    source_path = str(REPOSITORY_ROOT / "mock-oa-service" / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        value for value in (source_path, environment.get("PYTHONPATH")) if value
    )

    result = subprocess.run(
        [sys.executable, "-m", "mock_oa_service", "--version"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "python -m mock_oa_service 0.1.0"
