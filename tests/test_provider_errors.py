"""底层连接异常被 SDK 包装后，仍提供安全且可采取行动的错误类别。"""

import ssl

from mewcode.errors import safe_provider_error


def test_wrapped_tls_eof_reports_connection_layer_without_leaking_credentials():
    tls = ssl.SSLEOFError(8, 'unexpected EOF test-key')
    transport = RuntimeError('')
    transport.__context__ = tls
    sdk = RuntimeError('Connection error. test-key')
    sdk.__cause__ = transport

    result = str(safe_provider_error('OpenAI 兼容服务', sdk))

    assert 'TLS' in result and '代理' in result
    assert 'test-key' not in result and 'unexpected EOF' not in result


def test_wrapped_certificate_error_has_distinct_safe_guidance():
    sdk = RuntimeError('Connection error. test-key')
    sdk.__cause__ = ssl.SSLCertVerificationError(1, 'certificate verify failed test-key')

    result = str(safe_provider_error('兼容服务', sdk))

    assert '证书校验失败' in result and 'test-key' not in result


def test_unclassified_connection_error_with_cyclic_chain_still_returns():
    sdk = RuntimeError('test-key')
    sdk.__cause__ = sdk

    result = str(safe_provider_error('兼容服务', sdk))

    assert '请求失败' in result and 'test-key' not in result
