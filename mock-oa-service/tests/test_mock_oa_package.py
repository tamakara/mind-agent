import mock_oa_service
from mock_oa_service.mcp_server import create_mcp_server


def test_package_exposes_version() -> None:
    assert mock_oa_service.__version__ == "0.1.0"


def test_mcp_transport_allows_compose_host_without_disabling_rebinding_protection() -> None:
    server = create_mcp_server(lambda: None)  # type: ignore[arg-type]
    security = server.settings.transport_security

    assert security is not None
    assert security.enable_dns_rebinding_protection is True
    assert "mock-oa:*" in security.allowed_hosts
    assert "*" not in security.allowed_hosts
