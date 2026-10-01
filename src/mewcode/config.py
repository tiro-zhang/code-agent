"""从单个 .env 文件读取供应商配置。"""

from dataclasses import dataclass
from pathlib import Path
import re
from urllib.parse import urlsplit

from dotenv import dotenv_values


class ConfigError(ValueError):
    """配置文件无法用于启动会话。"""


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    protocol: str
    model: str
    base_url: str
    api_key: str
    thinking: bool
    max_iterations: int = 20


def load_config(path: str | Path) -> ProviderConfig:
    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigError("配置文件不存在或不是普通文件")

    values = dotenv_values(config_path, interpolate=False)
    required = ("name", "protocol", "model", "base_url", "api_key")
    for field in required:
        if not values.get(field) or not values[field].strip():
            raise ConfigError(f"{field} 不能为空")

    protocol = values["protocol"].strip()
    if protocol not in {"anthropic", "openai"}:
        raise ConfigError("protocol 必须是 anthropic 或 openai")

    base_url = values["base_url"].strip()
    parsed_url = urlsplit(base_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise ConfigError("base_url 必须是有效的 HTTP(S) 基础地址")

    thinking_value = (values.get("thinking") or "false").strip().lower()
    if thinking_value not in {"true", "false"}:
        raise ConfigError("thinking 必须是 true 或 false")
    thinking = thinking_value == "true"
    if protocol == "openai" and thinking:
        raise ConfigError("thinking=true 目前仅适用于 anthropic 协议")

    budget = values.get("max_iterations", "20")
    if budget is None or not re.fullmatch(r"[0-9]+", budget.strip()) or int(budget) <= 0:
        raise ConfigError("max_iterations 必须是十进制正整数")

    return ProviderConfig(
        name=values["name"].strip(),
        protocol=protocol,
        model=values["model"].strip(),
        base_url=base_url,
        api_key=values["api_key"].strip(),
        thinking=thinking,
        max_iterations=int(budget),
    )
