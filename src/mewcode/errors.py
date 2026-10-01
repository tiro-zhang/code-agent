"""将 SDK 异常转换成不含凭据的终端提示。"""

from .types import ContextLimitError, ProviderError


def safe_provider_error(provider: str, error: Exception, *, thinking: bool = False) -> ProviderError:
    """只用异常作分类，不把服务端原文直接展示给用户。"""
    status = getattr(error, "status_code", None)
    detail = str(error).lower()

    if "context" in detail or "上下文" in detail or status == 413:
        return ContextLimitError(f"{provider} 上下文长度已超出模型限制")
    if thinking and status == 400 and ("thinking" in detail or "budget_tokens" in detail):
        return ProviderError(f"{provider} 模型与思考配置不兼容")
    if status in {401, 403}:
        return ProviderError(f"{provider} 认证失败，请检查 api_key")
    if status == 429:
        return ProviderError(f"{provider} 请求受到速率限制，请稍后重试")
    if status == 400 and any(word in detail for word in ("tool", "function", "parallel")):
        return ProviderError(f"{provider} 工具协议参数不兼容，请检查服务对工具调用的支持")
    if status == 400:
        return ProviderError(f"{provider} 请求参数被拒绝，请检查 model 与配置")
    return ProviderError(f"{provider} 请求失败，请检查网络与服务状态")
