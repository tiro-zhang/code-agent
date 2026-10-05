"""同服务独立模型的显式窗口映射。"""

import pytest

from mewcode.config import ConfigError, load_config
from test_config import write_config


def test_skill_model_selection_reuses_service_and_own_window(tmp_path):
    config = load_config(write_config(tmp_path / "config", skill_models='\'{"small":{"context_window":64000,"max_output_tokens":4096}}\''))
    selected = config.for_skill("small")
    assert selected.model == "small"
    assert selected.context_window == 64000
    assert selected.max_output_tokens == 4096
    assert (selected.protocol, selected.base_url, selected.api_key, selected.thinking) == (config.protocol, config.base_url, config.api_key, config.thinking)
    assert config.for_skill(None) is config
    assert config.for_skill(config.model) is config
    with pytest.raises(ValueError):
        config.for_skill("not-configured")


@pytest.mark.parametrize("mapping", [
    '[]', '{"small":{}}', '{"small":{"context_window":true}}',
    '{"small":{"context_window":21000}}', '{"small":{"context_window":64000,"other":1}}',
    '{"small":{"context_window":64000,"context_window":65000}}',
    '{"small":{"context_window":64000},"small":{"context_window":65000}}',
    '{" ":{"context_window":64000}}', '{"small":{"context_window":64000,"max_output_tokens":false}}',
])
def test_skill_models_strict_json(tmp_path, mapping):
    with pytest.raises(ConfigError, match="skill_models"):
        load_config(write_config(tmp_path / "config", skill_models="'" + mapping + "'"))
