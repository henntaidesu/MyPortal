"""Bounded, credential-free diagnostics for proxy/OIDC requests."""
import contextvars
import json
import logging
from logging.handlers import RotatingFileHandler
import secrets
import time
import traceback
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

_trace = contextvars.ContextVar('portal_trace', default='-')
_file = logging.getLogger('portal.diagnostics.file')
_file.propagate = False
_file.setLevel(logging.INFO)


def configure(base_dir):
    if _file.handlers:
        return
    try:
        folder = Path(base_dir) / 'logs'
        folder.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(folder / 'proxy-diagnostics.log', maxBytes=2 * 1024 * 1024,
                                      backupCount=3, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(message)s'))
        _file.addHandler(handler)
        event('diagnostics.ready', file=str(folder / 'proxy-diagnostics.log'))
    except OSError as exc:
        event('diagnostics.file_unavailable', error_type=type(exc).__name__)


def safe_url(value):
    """Never retain query values, fragments, or URL userinfo."""
    try:
        parts = urlsplit(str(value))
        authority = parts.netloc.rsplit('@', 1)[-1]
        prefix = (parts.scheme + '://' if parts.scheme else '//') + authority if authority else ''
        return (prefix + parts.path)[:400]
    except ValueError:
        return '<invalid-url>'


def event(stage, **fields):
    record = {'time': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'trace': _trace.get(), 'stage': stage, **fields}
    line = '[portal-diag] ' + json.dumps(record, ensure_ascii=False)
    # Logs must not interrupt the proxy if the run window or disk is unavailable.
    try:
        print(line, flush=True)
    except Exception:
        pass
    if _file.handlers:
        try:
            _file.info(line)
        except Exception:
            pass


class RequestDiagnostics:
    """Pure ASGI: logs before routing/auth and never buffers request/response bodies."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        if scope['type'] != 'http' or not (path.startswith('/api/proxy/')
                or path.startswith('/api/oidc/') or path == '/.well-known/openid-configuration'):
            return await self.app(scope, receive, send)
        token = _trace.set(secrets.token_hex(6))
        began = time.perf_counter()
        started = False
        try:
            headers = {k.lower(): v for k, v in scope.get('headers', [])}
            query = parse_qsl(scope.get('query_string', b'').decode('latin-1'), keep_blank_values=True)
            public_urls = {k: safe_url(v) for k, v in query if k in ('base', 'redirect_uri')}
            event('request.begin', method=scope.get('method'), path=safe_url(path),
                  scheme=scope.get('scheme'), host=headers.get(b'host', b'').decode('latin-1')[:200],
                  forwarded_proto=headers.get(b'x-forwarded-proto', b'').decode('latin-1')[:30],
                  cf_ray=headers.get(b'cf-ray', b'').decode('latin-1')[:100],
                  query_keys=sorted({k[:60] for k, _ in query})[:30], public_urls=public_urls)

            async def traced_send(message):
                nonlocal started
                if message['type'] == 'http.response.start':
                    started = True
                    raw = list(message.get('headers', []))
                    location = next((v.decode('latin-1') for k, v in raw if k.lower() == b'location'), '')
                    lengths = [v.decode('latin-1')[:30] for k, v in raw if k.lower() == b'content-length']
                    encodings = [v.decode('latin-1')[:30] for k, v in raw if k.lower() == b'transfer-encoding']
                    event('response.start', status=message['status'], location=safe_url(location),
                          content_lengths=lengths, transfer_encodings=encodings,
                          header_bytes=sum(len(k) + len(v) + 4 for k, v in raw),
                          elapsed_ms=round((time.perf_counter() - began) * 1000))
                    message = {**message, 'headers': raw + [(b'x-portal-trace', _trace.get().encode())]}
                await send(message)

            await self.app(scope, receive, traced_send)
            event('request.end', elapsed_ms=round((time.perf_counter() - began) * 1000))
        except BaseException as exc:
            # Exception messages/locals may contain URLs or credentials. Log only type and frame locations.
            frames = [f'{Path(f.filename).name}:{f.lineno}:{f.name}' for f in traceback.extract_tb(exc.__traceback__)[-12:]]
            event('request.error', error_type=type(exc).__name__, response_started=started,
                  frames=frames, elapsed_ms=round((time.perf_counter() - began) * 1000))
            raise
        finally:
            _trace.reset(token)
