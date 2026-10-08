import stat

import pytest

from spider.core.config import (
    MIN_TOKEN_LENGTH,
    ClientConfig,
    client_config_path,
    default_device_name,
    has_insecure_permissions,
    load_client_config,
    load_server_config,
    save_client_config,
)
from spider.core.errors import ErrorCode, SpiderError

GOOD_TOKEN = "t" * MIN_TOKEN_LENGTH


def test_server_config_reads_every_variable(tmp_path):
    config = load_server_config(
        {
            "SPIDER_TOKEN": GOOD_TOKEN,
            "SPIDER_DATA_DIR": str(tmp_path),
            "SPIDER_MIN_FREE_GB": "2.5",
            "SPIDER_MAX_ITEM_MB": "100",
            "SPIDER_RETENTION_DAYS": "30",
        }
    )
    assert config.token == GOOD_TOKEN
    assert config.data_dir == tmp_path
    assert config.min_free_gb == 2.5
    assert config.max_item_mb == 100
    assert config.retention_days == 30


def test_server_config_defaults(tmp_path):
    config = load_server_config({"SPIDER_TOKEN": GOOD_TOKEN, "SPIDER_DATA_DIR": str(tmp_path)})
    assert config.min_free_gb == 10.0
    assert config.max_item_mb == 0
    assert config.retention_days is None


def test_server_refuses_a_missing_token(tmp_path):
    with pytest.raises(RuntimeError, match="SPIDER_TOKEN"):
        load_server_config({"SPIDER_DATA_DIR": str(tmp_path)})


def test_server_refuses_a_short_token(tmp_path):
    with pytest.raises(RuntimeError, match="32"):
        load_server_config({"SPIDER_TOKEN": "t" * 31, "SPIDER_DATA_DIR": str(tmp_path)})


def test_client_config_saves_with_owner_only_permissions(tmp_path):
    path = tmp_path / "config.toml"
    save_client_config(
        ClientConfig(server="http://example:8181", token=GOOD_TOKEN, device="laptop"), path
    )
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_client_config_roundtrips(tmp_path):
    path = tmp_path / "config.toml"
    original = ClientConfig(server="http://example:8181", token=GOOD_TOKEN, device="laptop")
    save_client_config(original, path)
    assert load_client_config(path, env={}) == original


def test_client_config_survives_special_characters_in_token(tmp_path):
    path = tmp_path / "config.toml"
    original = ClientConfig(server="http://x:8181", token='a"b\\c/d+e=' + "z" * 30, device="pc")
    save_client_config(original, path)
    assert load_client_config(path, env={}).token == original.token


@pytest.mark.parametrize(
    "awkward",
    ["\n", "\r", "\t", "\x00", "\x1f", "\x7f", '"', "\\", "\b", "\f"],
    ids=["newline", "cr", "tab", "nul", "unit-sep", "del", "quote", "backslash", "bs", "ff"],
)
def test_control_characters_survive_the_toml_round_trip(tmp_path, awkward):
    """A token pasted with a trailing newline used to write a file tomllib
    refused to read back."""
    path = tmp_path / "config.toml"
    original = ClientConfig(
        server=f"http://x{awkward}:8181", token=awkward + "z" * 40, device=f"pc{awkward}"
    )
    save_client_config(original, path)
    assert load_client_config(path, env={}) == original


def test_saving_over_a_wide_file_narrows_it_before_writing(tmp_path):
    """os.open's mode applies only at creation, so an existing 0644 config would
    otherwise hold the new token at the wider mode."""
    path = tmp_path / "config.toml"
    path.write_text('server = "old"\n', encoding="utf-8")
    path.chmod(0o644)
    save_client_config(ClientConfig(server="http://x:8181", token=GOOD_TOKEN, device="d"), path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_env_token_overrides_the_file(tmp_path):
    path = tmp_path / "config.toml"
    save_client_config(
        ClientConfig(server="http://example:8181", token=GOOD_TOKEN, device="laptop"), path
    )
    loaded = load_client_config(path, env={"SPIDER_TOKEN": "e" * 40})
    assert loaded.token == "e" * 40


def test_missing_client_config_raises_a_readable_error(tmp_path):
    with pytest.raises(SpiderError) as caught:
        load_client_config(tmp_path / "absent.toml", env={})
    assert caught.value.code is ErrorCode.bad_request
    assert "spider init" in caught.value.message


def test_insecure_permissions_detected(tmp_path):
    path = tmp_path / "config.toml"
    save_client_config(ClientConfig(server="http://x:8181", token=GOOD_TOKEN, device="d"), path)
    assert has_insecure_permissions(path) is False
    path.chmod(0o644)
    assert has_insecure_permissions(path) is True


def test_config_path_ends_with_the_expected_name():
    assert client_config_path().name == "config.toml"
    assert client_config_path().parent.name == "spider"


def test_default_device_name_is_not_empty():
    assert default_device_name().strip() != ""
