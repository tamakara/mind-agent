import tomllib
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.contract

REPOSITORY_ROOT = Path(__file__).parents[2]


def load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as file:
        return tomllib.load(file)


def test_mock_oa_is_an_independent_workspace_package() -> None:
    workhub_config = load_toml(REPOSITORY_ROOT / "pyproject.toml")
    mock_oa_config = load_toml(REPOSITORY_ROOT / "mock-oa-service" / "pyproject.toml")

    assert workhub_config["tool"]["uv"]["workspace"]["members"] == ["mock-oa-service"]
    assert mock_oa_config["project"]["name"] == "mock-oa-service"
    assert mock_oa_config["project"]["requires-python"] == ">=3.12,<3.13"


def test_compose_covers_fixed_startup_contract() -> None:
    compose = (REPOSITORY_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "WORKHUB_DATA_DIR: /data/workhub" in compose
    assert "WORKHUB_ADMIN_USERNAME: admin" in compose
    assert "WORKHUB_ADMIN_PASSWORD: workhub-demo-password" in compose
    assert "WORKHUB_MOCK_OA_SHARED_SECRET: workhub-demo-shared-secret" in compose
    assert "MOCK_OA_WORKHUB_SHARED_SECRET: workhub-demo-shared-secret" in compose
    assert "WORKHUB_SESSION_SECRET" not in compose
    assert "WORKHUB_BOOTSTRAP_ADMIN" not in compose
