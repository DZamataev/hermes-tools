import pytest

from hermes_bridge.config import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings.from_env(
        {
            "API_SERVER_KEY": "test-api-server-key",
            "OPENWEBUI_API_KEY": "test-openwebui-key",
            "HERMES_BRIDGE_SECRET": "x" * 32,
            "BRIDGE_DATA_DIR": str(tmp_path),
        }
    )
