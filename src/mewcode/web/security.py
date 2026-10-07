"""精确本机来源、实例凭据及显式公开字段；不信任代理转发头。"""

from collections.abc import Mapping
import hashlib
import hmac
import secrets
from uuid import uuid4

from .errors import WebError


class LocalSecurity:
    def __init__(self, port, *, server_instance_id=None):
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('本机端口必须在 1–65535 之间')
        self.server_instance_id = server_instance_id or uuid4().hex
        self.host = f'127.0.0.1:{port}'
        self.origin = f'http://{self.host}'
        self.token = secrets.token_urlsafe(32)
        suffix = hashlib.sha256(self.server_instance_id.encode()).hexdigest()[:16]
        self.cookie_name = f'mewcode_{suffix}'
        self._cookie_value = secrets.token_urlsafe(32)
        self.url = self.origin + '/#token=' + self.token

    def check_host(self, host):
        if host != self.host:
            raise WebError('invalid_host', '只接受本次本机监听地址', 403)

    def check_origin(self, origin):
        if origin != self.origin:
            raise WebError('invalid_origin', '只接受本机页面的同源请求', 403)

    def exchange(self, token):
        if not isinstance(token, str) or not token.isascii() or not hmac.compare_digest(token, self.token):
            raise WebError('unauthenticated', '启动凭据无效，请使用本次启动链接', 401)
        return self._cookie_value

    def authenticated(self, cookie_value):
        return (isinstance(cookie_value, str) and cookie_value.isascii()
                and hmac.compare_digest(cookie_value, self._cookie_value))


# 内部配置和供应商原始载荷不能因嵌套在某个公开字段里而泄漏。
_PRIVATE_FIELDS = frozenset({'api_key', 'apikey', 'authorization', 'headers', 'env',
                             'provider_content', 'password', 'secret', 'access_token',
                             'refresh_token', 'cookie', 'set-cookie'})


class Redactor:
    def __init__(self, *known_secrets):
        self.secrets = tuple(sorted({value for value in known_secrets if isinstance(value, str) and value},
                                    key=len, reverse=True))

    def text(self, value):
        text = str(value)
        for secret in self.secrets:
            text = text.replace(secret, '[已隐藏]')
        return text

    def value(self, value):
        """只处理 JSON 形状；不把任意对象的 repr 当成可公开信息。"""
        if isinstance(value, str):
            return self.text(value)
        if value is None or type(value) in (bool, int, float):
            return value
        if isinstance(value, Mapping):
            return {self.text(key): self.value(item) for key, item in value.items()
                    if isinstance(key, str) and key.lower() not in _PRIVATE_FIELDS}
        if isinstance(value, (list, tuple)):
            return [self.value(item) for item in value]
        return None


def public_fields(source, allowlist, *, redactor=None):
    """先按调用方白名单投影，再递归移除秘密字段和已知秘密值。"""
    projected = {name: source[name] for name in allowlist if name in source}
    return (redactor or Redactor()).value(projected)
