"""新委派配置保持缺省兼容并拒绝含混预算。"""

import pytest

from mewcode.config import ConfigError, load_config
from test_config import write_config


def test_default_config_inherits_model_and_background_scope(tmp_path):
    config = load_config(write_config(tmp_path / "config"))
    assert hasattr(config, "for_agent"), "缺少角色模型解析"
    assert config.for_agent("inherit") is config
    assert config.agent_plugin_dirs == ()
    assert config.agent_background_tools is None
    with pytest.raises(ConfigError, match="haiku"):
        config.for_agent("haiku")


def test_model_alias_is_explicit_and_does_not_mutate_parent(tmp_path):
    config = load_config(write_config(tmp_path / "config", agent_models='\'{"sonnet":{"model":"actual-model","context_window":64000,"max_output_tokens":4096}}\''))
    assert hasattr(config, "for_agent"), "缺少角色模型解析"
    child = config.for_agent("sonnet")
    assert (child.model, child.context_window, child.max_output_tokens) == ("actual-model", 64000, 4096)
    assert config.model == "claude-sonnet-4-6"
    assert (child.protocol, child.base_url, child.api_key, child.thinking) == (config.protocol, config.base_url, config.api_key, config.thinking)


@pytest.mark.parametrize("raw", [
    '[]', '{"small":{"model":"x","context_window":64000}}', '{"haiku":{}}',
    '{"haiku":{"model":"x","context_window":true}}',
    '{"haiku":{"model":"x","context_window":21000}}',
    '{"haiku":{"model":"x","context_window":64000,"other":1}}',
    '{"haiku":{"model":"x","context_window":64000,"context_window":65000}}',
    '{"haiku":{"model":"x","context_window":64000},"haiku":{"model":"y","context_window":64000}}',
    '{"haiku":{"model":" ","context_window":64000}}',
    '{"haiku":{"model":"x","context_window":64000,"max_output_tokens":false}}',
])
def test_agent_models_strict_json(tmp_path, raw):
    with pytest.raises(ConfigError, match="agent_models"):
        load_config(write_config(tmp_path / "config", agent_models="'" + raw + "'"))


@pytest.mark.parametrize("key,raw", [
    ("agent_plugin_dirs", '{}'), ("agent_plugin_dirs", '[true]'),
    ("agent_plugin_dirs", '[""]'), ("agent_background_tools", '{}'),
    ("agent_background_tools", '[null]'), ("agent_background_tools", '[" read_file"]'),
    ("agent_background_tools", '["read_file", "read_file"]'),
])
def test_agent_lists_are_exact_and_typed(tmp_path, key, raw):
    with pytest.raises(ConfigError, match=key):
        load_config(write_config(tmp_path / "config", **{key: "'" + raw + "'"}))


def test_plugin_paths_empty_scope_and_unknown_registered_tool(tmp_path):
    config = load_config(write_config(tmp_path / "config", agent_plugin_dirs='\'["a path", "../roles"]\'', agent_background_tools="'[]'"))
    assert getattr(config, "agent_plugin_dirs", None) == ("a path", "../roles")
    assert config.agent_background_tools == frozenset()
    config.validate_agent_tools({"read_file"})
    other = load_config(write_config(tmp_path / "other", agent_background_tools='\'["read_flie"]\''))
    with pytest.raises(ConfigError, match="read_flie"):
        other.validate_agent_tools({"read_file"})
