import pytest

from hermes_bridge.config import Settings


def test_settings_require_all_three_credentials(tmp_path):
    with pytest.raises(ValueError, match="OPENWEBUI_API_KEY"):
        Settings.from_env(
            {
                "API_SERVER_KEY": "hermes-key",
                "HERMES_BRIDGE_SECRET": "x" * 32,
                "BRIDGE_DATA_DIR": str(tmp_path),
            }
        )


def test_settings_accept_explicit_local_configuration(tmp_path):
    settings = Settings.from_env(
        {
            "API_SERVER_KEY": "hermes-key",
            "OPENWEBUI_API_KEY": "sk-openwebui",
            "HERMES_BRIDGE_SECRET": "x" * 32,
            "BRIDGE_DATA_DIR": str(tmp_path),
        }
    )
    assert settings.hermes_base_url == "http://host.docker.internal:8642"
    assert settings.openwebui_base_url == "http://open-webui:8080"
    assert settings.database_path == tmp_path / "bridge.sqlite3"
    assert settings.bridge_host_port == 8787
    assert settings.log_level == "info"
    assert settings.sync_interval_seconds == 30.0
    assert settings.connector_heartbeat_timeout_seconds == 20.0


@pytest.mark.parametrize("missing", ["API_SERVER_KEY", "OPENWEBUI_API_KEY", "HERMES_BRIDGE_SECRET"])
def test_settings_name_each_missing_credential(tmp_path, missing):
    env = {
        "API_SERVER_KEY": "hermes-key",
        "OPENWEBUI_API_KEY": "sk-openwebui",
        "HERMES_BRIDGE_SECRET": "x" * 32,
        "BRIDGE_DATA_DIR": str(tmp_path),
    }
    del env[missing]

    with pytest.raises(ValueError, match=missing):
        Settings.from_env(env)


@pytest.mark.parametrize("port", ["0", "65536", "not-a-port"])
def test_settings_reject_invalid_bridge_host_port(tmp_path, port):
    with pytest.raises(ValueError, match="BRIDGE_HOST_PORT"):
        Settings.from_env(
            {
                "API_SERVER_KEY": "hermes-key",
                "OPENWEBUI_API_KEY": "sk-openwebui",
                "HERMES_BRIDGE_SECRET": "x" * 32,
                "BRIDGE_DATA_DIR": str(tmp_path),
                "BRIDGE_HOST_PORT": port,
            }
        )
