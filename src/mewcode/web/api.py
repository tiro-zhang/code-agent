"""本机同源 JSON 操作、快照和有界事件订阅。"""

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from ..sessions import SessionError
from .errors import WebError


class Envelope(BaseModel):
    model_config = ConfigDict(extra='forbid')
    server_instance_id: StrictStr = Field(max_length=128)
    client_id: StrictStr = Field(max_length=128)
    sequence: StrictInt = Field(ge=1)
    generation: StrictInt
    state_version: StrictInt


class Input(Envelope):
    text: StrictStr = Field(max_length=256 * 1024)


class Control(Envelope):
    action: Literal['stop', 'plan', 'execute', 'compact', 'reset', 'permission_mode', 'revoke']
    value: StrictStr | None = Field(default=None, max_length=32)


class Decision(Envelope):
    decision: Literal['deny', 'once', 'session', 'permanent']


class Authentication(BaseModel):
    model_config = ConfigDict(extra='forbid')
    token: StrictStr = Field(max_length=256)


HEADERS = {
    'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
    'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                             "font-src 'self'; img-src 'self' data:; connect-src 'self'; "
                             "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
}


class LocalBoundary:
    """在解析 JSON 前限制正文大小，并为所有响应设置同一安全边界。"""
    def __init__(self, app, *, security):
        self.app, self.security = app, security

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        request = Request(scope)
        async def safe_send(message):
            if message['type'] == 'http.response.start':
                extra = [(key.lower().encode(), value.encode()) for key, value in HEADERS.items()]
                names = {key for key, _ in extra}
                message['headers'] = [(key, value) for key, value in message.get('headers', []) if key.lower() not in names] + extra
            await send(message)
        try:
            hosts = [value.decode('latin1') for key, value in scope['headers'] if key.lower() == b'host']
            if len(hosts) != 1:
                raise WebError('invalid_host', '本机监听地址无效', 403)
            self.security.check_host(hosts[0])
            origin = request.headers.get('origin')
            if origin is not None or request.method not in {'GET', 'HEAD'}:
                self.security.check_origin(origin)
            if request.method not in {'GET', 'HEAD'} and request.headers.get('content-type', '').split(';')[0].strip() != 'application/json':
                raise WebError('invalid_content_type', '操作必须使用 JSON', 415)
            if request.url.path.startswith('/api/') and request.url.path != '/api/v1/auth':
                if not self.security.authenticated(request.cookies.get(self.security.cookie_name)):
                    raise WebError('unauthenticated', '请使用本次服务的启动链接登录', 401)
            size = 0
            async def limited_receive():
                nonlocal size
                message = await receive()
                if message['type'] == 'http.request':
                    size += len(message.get('body', b''))
                    if size > 1024 * 1024:
                        raise WebError('request_too_large', '请求过大；输入上限为 256 KiB', 413)
                return message
            await self.app(scope, limited_receive, safe_send)
        except WebError as error:
            await JSONResponse(error.body(), status_code=error.status)(scope, receive, safe_send)


def create_app(manager, security, *, static_root=None):
    @asynccontextmanager
    async def lifespan(app):
        yield
        await manager.aclose()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.manager, app.state.security = manager, security
    app.add_middleware(LocalBoundary, security=security)

    @app.exception_handler(WebError)
    async def web_error(request, error):
        return JSONResponse(error.body(), status_code=error.status)

    @app.exception_handler(SessionError)
    async def archive_error(request, error):
        return JSONResponse({'error': {'code': 'archive_unavailable', 'message': manager.redactor.text(str(error))}}, status_code=400)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        return JSONResponse({'error': {'code': 'invalid_request', 'message': '请求字段或类型不合法'}}, status_code=422)

    @app.post('/api/v1/auth')
    async def authenticate(body: Authentication):
        cookie = security.exchange(body.token)
        response = JSONResponse({'server_instance_id': manager.server_instance_id})
        response.set_cookie(security.cookie_name, cookie, httponly=True, samesite='strict', path='/api/v1')
        return response

    @app.post('/api/v1/clients')
    async def register():
        manager._check(manager.generation)
        return manager.ledger.register()

    @app.get('/api/v1/state')
    async def state():
        return manager.hub.snapshot()

    @app.get('/api/v1/sessions')
    async def sessions(cursor: str | None = None, limit: int = 50):
        return await manager.browser.list_sessions(cursor=cursor, limit=limit)

    async def operation(body, route, invoke):
        payload = body.model_dump()
        envelope = {key: payload[key] for key in Envelope.model_fields}
        async def checked():
            try:
                return await invoke()
            except SessionError as error:
                raise WebError('archive_unavailable', manager.redactor.text(str(error)), 400) from None
        status, value = await manager.ledger.run(envelope, {'route': route, 'body': payload}, checked)
        return JSONResponse(value, status_code=status)

    @app.post('/api/v1/sessions')
    async def create(body: Envelope):
        async def invoke():
            manager._check(body.generation)
            result = await manager.browser.create(manager.config.protocol, manager.config.model)
            manager.publish('sessions_changed')
            return 201, result
        return await operation(body, 'sessions', invoke)

    @app.get('/api/v1/sessions/{identity}/history')
    async def history(identity: str, cursor: str | None = None, limit: int = 50):
        return await manager.browser.history(identity, cursor=cursor, limit=limit)

    @app.post('/api/v1/sessions/{identity}/activate')
    async def activate(identity: str, body: Envelope):
        return await operation(body, f'sessions/{identity}/activate',
            lambda: manager.activate(identity, body.generation, body.state_version))

    @app.post('/api/v1/sessions/{identity}/inputs')
    async def submit(identity: str, body: Input):
        return await operation(body, f'sessions/{identity}/inputs',
            lambda: manager.submit(identity, body.text, body.generation, body.state_version))

    @app.post('/api/v1/sessions/{identity}/controls')
    async def control(identity: str, body: Control):
        return await operation(body, f'sessions/{identity}/controls',
            lambda: manager.control(identity, body.action, body.value, body.generation, body.state_version))

    @app.get('/api/v1/operations/{client_id}/{sequence}')
    async def receipt(client_id: str, sequence: int):
        status, body = manager.ledger.lookup(client_id, sequence)
        if body.get('pending'):
            return JSONResponse(body, status_code=202)
        return {'status_code': status, 'body': body}

    @app.get('/api/v1/approvals/{identity}')
    async def approval(identity: str, section: str = 'arguments', offset: int = 0, limit: int = 32768):
        return manager.approvals.preview(identity, section=section, offset=offset, limit=limit)

    @app.post('/api/v1/approvals/{identity}/decision')
    async def decide(identity: str, body: Decision):
        async def invoke():
            manager._check(body.generation)
            value = manager.approvals.decide(identity, body.decision,
                server_instance_id=body.server_instance_id, generation=body.generation, run_id=manager.run_id)
            return 200, value
        return await operation(body, f'approvals/{identity}/decision', invoke)

    @app.get('/api/v1/results/{identity}')
    async def result(identity: str, cursor: str | None = None):
        return await manager.browser.result(identity, cursor=cursor)

    @app.get('/api/v1/events')
    async def events(request: Request, after: int | None = None):
        if after is None and request.headers.get('last-event-id'):
            try:
                after = int(request.headers['last-event-id'])
            except ValueError:
                raise WebError('invalid_cursor', '事件游标无效', 400) from None
        subscriber = manager.hub.subscribe(after)
        async def stream():
            pending = None
            try:
                while True:
                    pending = asyncio.create_task(anext(subscriber))
                    while not pending.done():
                        done, _ = await asyncio.wait((pending,), timeout=15)
                        if not done:
                            yield ': heartbeat\n\n'
                    try:
                        event = pending.result()
                    except StopAsyncIteration:
                        return
                    yield f'id: {event["seq"]}\ndata: {json.dumps(event, ensure_ascii=False, separators=(",", ":"))}\n\n'
            finally:
                if pending and not pending.done():
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                manager.hub.unsubscribe(subscriber)
        return StreamingResponse(stream(), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no'})

    static = Path(static_root) if static_root else Path(__file__).parent / 'static'
    static = static.resolve()

    @app.get('/{path:path}')
    async def asset(path: str):
        candidate = static / (path or 'index.html')
        if '..' in Path(path).parts or candidate.is_symlink() or not candidate.resolve().is_relative_to(static) or not candidate.is_file():
            raise WebError('not_found', '资源不存在', 404)
        return FileResponse(candidate)

    return app
