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
    agent_models: tuple[tuple[str, str, int, int], ...] = ()
    agent_plugin_dirs: tuple[str, ...] = ()
    agent_background_tools: frozenset[str] | None = None
    team_backend: str = 'auto'
    team_max_running: int = 4
    team_max_queued: int = 32
    team_coordinator_enabled: bool = False

    def __post_init__(self):
        if self.team_backend not in {'auto', 'tmux', 'inprocess'}:
            raise ConfigError('team_backend 必须是 auto、tmux 或 inprocess')
        for name, minimum in (('team_max_running', 1), ('team_max_queued', 0)):
            value = getattr(self, name)
            if type(value) is not int or value < minimum:
                raise ConfigError(f'{name} 必须是大于等于 {minimum} 的整数')
        if type(self.team_coordinator_enabled) is not bool:
            raise ConfigError('team_coordinator_enabled 必须是布尔值')

    def for_skill(self, model: str | None):
        """仅覆盖同一服务的模型及其显式预算。"""
        if model is None or model == self.model:
            return self
        for name, window, output in self.skill_models:
            if name == model:
                return replace(self, model=name, context_window=window, max_output_tokens=output)
        raise ConfigError(f'skill_models 未配置模型 {model} 的窗口')

    def for_agent(self, alias: str):
        """显式别名只覆盖模型预算，保留同一服务与思考配置。"""
        if alias == "inherit":
            return self
        for name, model, window, output in self.agent_models:
            if name == alias:
                return replace(self, model=model, context_window=window, max_output_tokens=output)
        raise ConfigError(f"agent_models 未配置模型别名 {alias}")

    def validate_agent_tools(self, registered) -> None:
        unknown = (self.agent_background_tools or frozenset()) - set(registered)
        if unknown:
            raise ConfigError(f"agent_background_tools 包含未知工具：{', '.join(sorted(unknown))}")


def _strict_json(raw):
    def mapping(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("重复 JSON 字段")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=mapping,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))


def _agent_models(raw, output):
    try:
        value = _strict_json(raw)
        if not isinstance(value, dict) or not value.keys() <= {"haiku", "sonnet", "opus"}:
            raise ValueError
        result = []
        for alias, limits in value.items():
            if (not isinstance(limits, dict) or not {"model", "context_window"} <= limits.keys()
                    or not limits.keys() <= {"model", "context_window", "max_output_tokens"}):
                raise ValueError
            model = limits["model"]
            if not isinstance(model, str) or not model or any(c.isspace() or not c.isprintable() for c in model):
                raise ValueError
            window, maximum = limits["context_window"], limits.get("max_output_tokens", output)
            if type(window) is not int or type(maximum) is not int or maximum <= 0 or window <= maximum + 13000:
                raise ValueError
            result.append((alias, model, window, maximum))
        return tuple(result)
    except (ValueError, TypeError, RecursionError):
        raise ConfigError("agent_models 必须是无重复字段的别名 JSON 映射，窗口须大于输出额度 + 13000") from None


def _agent_list(raw, field, *, tool_names=False):
    try:
        value = _strict_json(raw)
        if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() or "\x00" in item for item in value):
            raise ValueError
        if tool_names and (len(value) != len(set(value)) or any(any(c.isspace() or not c.isprintable() for c in item) for item in value)):
            raise ValueError
        return frozenset(value) if tool_names else tuple(value)
    except (ValueError, TypeError, RecursionError):
        raise ConfigError(f"{field} 必须是有效字符串 JSON 列表，工具名称必须精确且不重复") from None


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

    team = {'team_backend': values.get('team_backend', 'auto')}
    for key, default, minimum in (('team_max_running', '4', 1), ('team_max_queued', '32', 0)):
        value = values.get(key, default)
        if value is None or not re.fullmatch(r'[0-9]+', value.strip()) or int(value) < minimum:
            raise ConfigError(f'{key} 必须是大于等于 {minimum} 的十进制整数')
        team[key] = int(value)
    enabled = values.get('team_coordinator_enabled', 'false')
    if enabled not in {'true', 'false'}:
        raise ConfigError('team_coordinator_enabled 必须是 true 或 false')
    team['team_coordinator_enabled'] = enabled == 'true'

    return ProviderConfig(
        name=values["name"].strip(),
        protocol=protocol,
        model=values["model"].strip(),
        base_url=base_url,
        api_key=values["api_key"].strip(),
        thinking=thinking,
        max_iterations=int(budget),
        skill_models=_skill_models(values['skill_models'], limits['max_output_tokens']) if 'skill_models' in values else (),
        agent_models=_agent_models(values['agent_models'], limits['max_output_tokens']) if 'agent_models' in values else (),
        agent_plugin_dirs=_agent_list(values['agent_plugin_dirs'], 'agent_plugin_dirs') if 'agent_plugin_dirs' in values else (),
        agent_background_tools=_agent_list(values['agent_background_tools'], 'agent_background_tools', tool_names=True) if 'agent_background_tools' in values else None,
        **limits,
        **team,
    )
