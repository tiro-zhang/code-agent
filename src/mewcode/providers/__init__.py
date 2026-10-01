"""根据配置选择协议适配器。"""

from ..config import ProviderConfig
from ..types import Provider


def make_provider(config: ProviderConfig) -> Provider:
    if config.protocol == "anthropic":
        from .anthropic import AnthropicProvider

        return AnthropicProvider(config)
    if config.protocol == "openai":
        from .openai import OpenAIProvider

        return OpenAIProvider(config)
    raise ValueError("不支持的协议")
