"""供应商配置的外部行为测试。"""

from pathlib import Path

import pytest

from mewcode.config import ConfigError, load_config


def write_config(path: Path, **overrides: str) -> Path:
    values = {
        "name": "演示后端",
        "protocol": "anthropic",
        "model": "claude-sonnet-4-6",
        "base_url": "https://api.anthropic.com",
        "api_key": "secret-sentinel",
    }
    values.update(overrides)
    path.write_text("\n".join(f"{key}={value}" for key, value in values.items()))
    return path


def test_load_config_reads_one_profile_and_defaults_thinking_false(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path / ".env.claude"))

    assert config.name == "演示后端"
    assert config.protocol == "anthropic"
    assert config.model == "claude-sonnet-4-6"
    assert config.base_url == "https://api.anthropic.com"
    assert config.api_key == "secret-sentinel"
    assert config.thinking is False


def test_load_config_accepts_explicit_thinking_true(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path / ".env.claude", thinking="true"))

    assert config.thinking is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", ""),
        ("protocol", "gemini"),
        ("model", ""),
        ("base_url", "not-a-url"),
        ("api_key", ""),
        ("thinking", "sometimes"),
    ],
)
def test_load_config_rejects_invalid_values_without_leaking_key(
    tmp_path: Path, field: str, value: str
) -> None:
    path = write_config(tmp_path / ".env.bad", **{field: value})

    with pytest.raises(ConfigError) as error:
        load_config(path)

    assert field in str(error.value)
    assert "secret-sentinel" not in str(error.value)


def test_load_config_rejects_missing_file_and_missing_key(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="配置文件"):
        load_config(tmp_path / "missing.env")

    path = write_config(tmp_path / ".env.bad")
    path.write_text(path.read_text().replace("api_key=secret-sentinel", ""))
    with pytest.raises(ConfigError, match="api_key"):
        load_config(path)


def test_openai_compatible_profile_cannot_enable_claude_thinking(tmp_path: Path) -> None:
    path = write_config(tmp_path / ".env.openai", protocol="openai", thinking="true")

    with pytest.raises(ConfigError, match="thinking"):
        load_config(path)


def test_request_budget_defaults_and_custom_value(tmp_path):
    assert load_config(write_config(tmp_path / "default")).max_iterations == 20
    assert load_config(write_config(tmp_path / "custom", max_iterations="5")).max_iterations == 5


@pytest.mark.parametrize("value", ["0", "-1", "", "1.5", "abc", "1e2", "５"])
def test_invalid_request_budget_is_rejected(tmp_path, value):
    with pytest.raises(ConfigError, match="max_iterations") as error:
        load_config(write_config(tmp_path / "bad", max_iterations=value))
    assert "secret-sentinel" not in str(error.value)
