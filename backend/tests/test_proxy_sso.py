"""OIDC through the site proxy: no database or real network required."""
import asyncio
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient
import httpx
from app import proxy

PORTAL = 'https://portal.makurochan.com'
ROOT = PORTAL + '/api/proxy/6dk0sx3ezp/__portal__/http/10.0.10.30:9600/'
CALLBACK = ROOT + 'mercariV2/src/use_web/sso/callback'

class EmptyStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b''

class ProxySsoTests(unittest.TestCase):
    def test_login_keeps_public_callback_and_leaves_proxy_for_authorization(self):
        target = proxy._to_target({'id': '6dk0sx3ezp', 'url': 'http://10.0.10.30:9600', 'proxy': 'site'})
        seen = []
        async def upstream(request):
            seen.append(request)
            if request.url.path.endswith('/callback'):
                self.assertIn('fm_portal_sso=test', request.headers.get('cookie', ''))
                self.assertNotIn('portal_session', request.headers.get('cookie', ''))
                return httpx.Response(303, headers={'location': ROOT + '#/login?sso_ticket=one-use'}, stream=EmptyStream())
            base = parse_qs(request.url.query.decode())['base'][0]
            authorization = PORTAL + '/api/oidc/authorize?' + urlencode({
                'client_id': 'freemm_org', 'redirect_uri': base + 'mercariV2/src/use_web/sso/callback',
                'response_type': 'code', 'scope': 'openid profile', 'state': 'test-state'})
            return httpx.Response(303, headers={'location': authorization,
                'set-cookie': 'fm_portal_sso=test; HttpOnly; SameSite=Lax; Path=/mercariV2/src/use_web/sso'}, stream=EmptyStream())
        app = FastAPI()
        app.include_router(proxy.router)
        app.add_middleware(proxy.Fallback)
        @app.get('/api/oidc/authorize')
        def authorize(request: Request):
            self.assertEqual(request.cookies.get('portal_session'), 'portal-only')
            self.assertNotIn('fm_portal_sso', request.cookies)
            return RedirectResponse(request.query_params['redirect_uri'] + '?code=test-code&state=test-state', status_code=303)
        app.dependency_overrides[proxy.require_login] = lambda: {'id': 1}
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        try:
            with patch.object(proxy, '_target', return_value=target), patch.object(proxy, '_client', return_value=upstream_client):
                with TestClient(app, base_url=PORTAL) as browser:
                    browser.cookies.set('portal_session', 'portal-only')
                    response = browser.get(ROOT + 'mercariV2/src/use_web/sso/login?' + urlencode({'base': ROOT}), follow_redirects=False)
                    authorization_response = browser.get(response.headers['location'], headers={'referer': ROOT}, follow_redirects=False)
                    callback_response = browser.get(authorization_response.headers['location'], follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            self.assertEqual(callback_response.headers['location'], ROOT + '#/login?sso_ticket=one-use')
            self.assertEqual(parse_qs(seen[0].url.query.decode())['base'][0], ROOT)
            location = urlsplit(response.headers['location'])
            self.assertEqual(location.path, '/api/oidc/authorize')
            self.assertEqual(parse_qs(location.query)['redirect_uri'][0], CALLBACK)
            self.assertIn('Path=/api/proxy/6dk0sx3ezp/', response.headers['set-cookie'])
        finally:
            asyncio.run(upstream_client.aclose())

    def test_public_callback_return_is_not_proxied_twice(self):
        target = proxy._to_target({'id': '6dk0sx3ezp', 'url': 'http://10.0.10.30:9600', 'proxy': 'site'})
        result = proxy._rewrite_location(ROOT + '#/login?sso_ticket=one-use', target, target.scheme, target.host, proxy._rewriter(target, portal_origin=PORTAL))
        self.assertEqual(result, ROOT + '#/login?sso_ticket=one-use')


    def test_only_sso_login_preserves_base(self):
        target = proxy._to_target({'id': '6dk0sx3ezp', 'url': 'http://10.0.10.30:9600', 'proxy': 'site'})
        rewriter = proxy._rewriter(target, portal_origin=PORTAL)
        query = urlencode({'base': ROOT, 'return_to': ROOT + 'page'}) + '&opaque=a+b%2f,c'
        normal = parse_qs(proxy._clean_query(query, rewriter))
        self.assertEqual(normal['base'], ['http://10.0.10.30:9600/'])
        sso = proxy._clean_query(query, rewriter, keep_public_base=True)
        self.assertEqual(parse_qs(sso)['base'], [ROOT])
        self.assertEqual(parse_qs(sso)['return_to'], ['http://10.0.10.30:9600/page'])
        self.assertTrue(sso.endswith('&opaque=a+b%2f,c'))

    def test_native_portal_redirects_are_origin_scoped(self):
        target = proxy._to_target({'id': '6dk0sx3ezp', 'url': 'http://10.0.10.30:9600', 'proxy': 'site'})
        rewriter = proxy._rewriter(target, portal_origin=PORTAL)
        for url in [PORTAL + '/api/oidc/authorize?state=123', '/api/oidc/authorize?state=123', '//portal.makurochan.com/api/oidc/authorize', PORTAL + '/.well-known/openid-configuration']:
            self.assertEqual(proxy._rewrite_location(url, target, target.scheme, target.host, rewriter), url)
        for url in ['https://other.example.com/api/oidc/authorize', PORTAL + '/other-page', 'https://other.example.com' + target.mount + 'page']:
            self.assertTrue(proxy._rewrite_location(url, target, target.scheme, target.host, rewriter).startswith(target.base))


    def test_https_redirects_when_tls_terminates_before_portal(self):
        target = proxy._to_target({'id': '6dk0sx3ezp', 'url': 'http://10.0.10.30:9600', 'proxy': 'site'})
        # TLS ends at the reverse proxy; ASGI sees http with the public Host.
        rewriter = proxy._rewriter(target, portal_origin='http://portal.makurochan.com')
        for location in [PORTAL + '/api/oidc/authorize?state=123', ROOT + '#/login?sso_ticket=one-use']:
            with self.subTest(location=location):
                self.assertEqual(proxy._rewrite_location(location, target, target.scheme, target.host, rewriter), location)
        for location in ['https://portal.makurochan.com:9443/api/oidc/authorize', 'https://other.example.com/api/oidc/authorize']:
            self.assertTrue(proxy._rewrite_location(location, target, target.scheme, target.host, rewriter).startswith(target.base))

if __name__ == '__main__':
    unittest.main()
