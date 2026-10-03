"""将 SDK 异常转换成不含凭据的终端提示。"""

import ssl

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
    # SDK 往往包装传输异常；按类型检查因果链，不回显可能含凭据的原文。
    pending, seen, tls = [error], set(), False
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, ssl.SSLCertVerificationError):
            return ProviderError(f"{provider} TLS 证书校验失败，请检查系统证书、代理证书及服务地址")
        tls |= isinstance(current, ssl.SSLError)
        pending.extend(item for item in (current.__cause__, current.__context__) if item is not None)
    if tls:
        return ProviderError(f"{provider} TLS 连接失败，请检查网络、代理配置及服务端连接状态")
    return ProviderError(f"{provider} 请求失败，请检查网络与服务状态")
