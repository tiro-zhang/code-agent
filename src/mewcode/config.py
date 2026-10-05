"""从单个 .env 文件读取供应商配置。"""

from dataclasses import dataclass, field, replace
import json
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
    context_window: int = field(kw_only=True)
    max_output_tokens: int = 8192
    skill_models: tuple[tuple[str, int, int], ...] = ()

    def for_skill(self, model: str | None):
        """仅覆盖同一服务的模型及其显式预算。"""
        if model is None or model == self.model:
            return self
        for name, window, output in self.skill_models:
            if name == model:
                return replace(self, model=name, context_window=window, max_output_tokens=output)
        raise ConfigError(f'skill_models 未配置模型 {model} 的窗口')


def _skill_models(raw, output):
    def mapping(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('重复 JSON 字段')
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=mapping,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(value, dict):
            raise ValueError
        result = []
        for name, limits in value.items():
            if (not name or any(c.isspace() or not c.isprintable() for c in name)
                    or not isinstance(limits, dict) or 'context_window' not in limits
                    or not set(limits) <= {'context_window', 'max_output_tokens'}):
                raise ValueError
            window, maximum = limits['context_window'], limits.get('max_output_tokens', output)
            if type(window) is not int or type(maximum) is not int or maximum <= 0 or window <= maximum + 13000:
                raise ValueError
            result.append((name, window, maximum))
        return tuple(result)
    except (ValueError, TypeError, RecursionError):
        raise ConfigError('skill_models 必须是无重复字段的模型预算 JSON 映射，每项窗口须大于输出额度 + 13000') from None


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

    limits = {}
    for key, default in (("context_window", None), ("max_output_tokens", "8192")):
        value = values.get(key, default)
        if value is None or not re.fullmatch(r"[0-9]+", value.strip()) or int(value) <= 0:
            raise ConfigError(f"{key} 必须配置为十进制正整数")
        limits[key] = int(value)
    if limits["context_window"] <= limits["max_output_tokens"] + 13000:
        raise ConfigError("context_window 必须大于 max_output_tokens + 13000")

    return ProviderConfig(
        name=values["name"].strip(),
        protocol=protocol,
        model=values["model"].strip(),
        base_url=base_url,
        api_key=values["api_key"].strip(),
        thinking=thinking,
        max_iterations=int(budget),
        skill_models=_skill_models(values['skill_models'], limits['max_output_tokens']) if 'skill_models' in values else (),
        **limits,
    )
