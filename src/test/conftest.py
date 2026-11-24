"""Shared pytest plugins for src/test."""

pytest_plugins = [
    "src.test.mcp_oauth_test_helpers",
    "src.test.whatsapp_test_helpers",
]


import pytest


@pytest.fixture(autouse=True)
def _clear_access_principal_cache():
    """Each test starts with an empty ``src.access`` principal cache: tests
    reuse user ids across isolated DBs and write access rows directly."""
    from src.access.principal import clear_principal_cache

    clear_principal_cache()
    yield
    clear_principal_cache()


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: test con processi o servizi reali (node, rete)"
    )
