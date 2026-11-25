"""Shared cards share configuration, not login: no database or real network required."""
import asyncio
import contextlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from app import proxy

PORTAL = 'https://portal.makurochan.com'
CARD = '6dk0sx3ezp'
ROOT = PORTAL + f'/api/proxy/{CARD}/__portal__/http/10.0.10.30:9600/'


class ProxyIsolationTests(unittest.TestCase):
    def fetch(self, user_id, headers, upstream_headers=(), body=b'{}', login=0, jar=False):
        target = proxy._to_target({'id': CARD, 'url': 'http://10.0.10.30:9600', 'proxy': 'site'})
        # 10.0.10.30 是内网地址：按管理员的卡片（或管理员分享的）算
        target = target._replace(login=login, jar=jar, private_ok=True)
        seen = []

        async def upstream(request):
            seen.append(request)
            return httpx.Response(200, headers=list(upstream_headers), content=body)

        app = FastAPI()
        app.include_router(proxy.router)
        app.dependency_overrides[proxy.require_login] = lambda: {'id': user_id}
        client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        try:
            with patch.object(proxy, '_target', return_value=target), \
                    patch.object(proxy, '_client', return_value=client):
                with TestClient(app, base_url=PORTAL) as browser:
                    response = browser.get(ROOT + 'api/me', headers=headers)
        finally:
            asyncio.run(client.aclose())
        return seen[0], response

    def run_as(self, user_id, cookies, upstream_cookies=(), login=0, jar=False):
        headers = [('content-type', 'application/json')]
        headers += [('set-cookie', c) for c in upstream_cookies]
        sent, response = self.fetch(user_id, {'cookie': cookies}, headers, login=login, jar=jar)
        return sent.headers.get('cookie', ''), response.headers.get_list('set-cookie')

    def test_injected_html_is_never_cached(self):
        _, response = self.fetch(1, {}, [('content-type', 'text/html; charset=utf-8'),
                                         ('etag', '"abc"'), ('last-modified', 'Mon, 01 Jan 2024 00:00:00 GMT'),
                                         ('cache-control', 'max-age=600')],
                                 body=b'<html><head></head><body></body></html>')
        self.assertEqual(response.headers.get_list('cache-control'), ['no-store'])
        self.assertNotIn('etag', response.headers)
        self.assertNotIn('last-modified', response.headers)
        self.assertIn('__portal:u1:', response.text)

    def test_other_responses_keep_upstream_caching(self):
        _, response = self.fetch(1, {}, [('content-type', 'text/css'), ('etag', '"abc"'),
                                         ('cache-control', 'max-age=600')], body=b'a{}')
        self.assertEqual(response.headers['cache-control'], 'max-age=600')
        self.assertEqual(response.headers['etag'], '"abc"')

    def test_navigation_drops_conditional_headers(self):
        conditional = {'if-none-match': '"abc"', 'if-modified-since': 'Mon, 01 Jan 2024 00:00:00 GMT'}
        json_body = [('content-type', 'application/json')]
        sent, _ = self.fetch(1, {'sec-fetch-dest': 'document', **conditional}, json_body)
        self.assertNotIn('if-none-match', sent.headers)
        self.assertNotIn('if-modified-since', sent.headers)
        sent, _ = self.fetch(1, {'sec-fetch-dest': 'script', **conditional}, json_body)
        self.assertEqual(sent.headers['if-none-match'], '"abc"')

    def test_portal_login_and_logout_clear_the_browser_cache(self):
        from fastapi import Response
        from app import auth
        for call in (lambda r: auth.set_cookie(r, 'token'), auth.clear_cookie):
            response = Response()
            call(response)
            self.assertEqual(response.headers['clear-site-data'], '"cache"')

    def test_other_users_cookies_are_not_forwarded_and_get_cleared(self):
        sent, set_cookies = self.run_as(
            2, 'portal_session=zav; session=makuro; portal_proxy_owner=1',
            upstream_cookies=['session=fresh; Path=/'])
        self.assertEqual(sent, '')
        mount = f'/api/proxy/{CARD}/'
        self.assertEqual(set_cookies[0], f'session=; Path={mount}; Max-Age=0; Secure'
                         if proxy.COOKIE_SECURE else f'session=; Path={mount}; Max-Age=0')
        self.assertTrue(set_cookies[1].startswith('portal_proxy_owner=2; '))
        self.assertTrue(set_cookies[2].startswith('session=fresh'))
        self.assertFalse(any(c.startswith('portal_session') for c in set_cookies))

    def test_cookies_without_owner_are_treated_as_someone_elses(self):
        sent, set_cookies = self.run_as(1, 'session=old')
        self.assertEqual(sent, '')
        self.assertTrue(set_cookies[0].startswith('session=; '))

    def test_own_cookies_are_forwarded_without_marker(self):
        sent, set_cookies = self.run_as(1, 'portal_session=x; session=mine; portal_proxy_owner=1')
        self.assertEqual(sent, 'session=mine')
        self.assertEqual(set_cookies, [])

    def test_marker_is_refreshed_when_upstream_sets_cookies(self):
        _, set_cookies = self.run_as(1, 'session=mine; portal_proxy_owner=1',
                                     upstream_cookies=['session=new'])
        self.assertTrue(set_cookies[0].startswith('portal_proxy_owner=1; '))
        self.assertTrue(set_cookies[1].startswith('session=new'))

    def test_injected_script_scopes_storage_by_user_and_card(self):
        target = proxy._to_target({'id': CARD, 'url': 'http://10.0.10.30:9600', 'proxy': 'site'})
        html = proxy._rewriter(target, portal_origin=PORTAL, user_id=7).body(
            'html', '<html><head></head><body></body></html>', 'http', '10.0.10.30:9600')
        config = json.loads(html.split('var C = ', 1)[1].split(';\n', 1)[0])
        self.assertEqual(config['store'], f'__portal:u7:{CARD}:')

    def test_shared_login_uses_the_sharers_jar_and_marker(self):
        loads, stores = [], []
        with patch.object(proxy.cookiejar, 'load', lambda u, i: loads.append((u, i)) or []), \
                patch.object(proxy.cookiejar, 'store', lambda u, i, *a: stores.append((u, i))):
            sent, set_cookies = self.run_as(2, 'session=makuro; portal_proxy_owner=1',
                                            upstream_cookies=['session=rotated'], login=1, jar=True)
        self.assertEqual(sent, 'session=makuro')
        self.assertEqual(loads, [(1, CARD)])
        self.assertEqual(stores, [(1, CARD)])
        self.assertTrue(set_cookies[0].startswith('portal_proxy_owner=1; '))

    def test_shared_login_off_keeps_recipients_own_jar(self):
        loads = []
        with patch.object(proxy.cookiejar, 'load', lambda u, i: loads.append((u, i)) or []):
            sent, _ = self.run_as(2, 'session=makuro; portal_proxy_owner=1', login=2, jar=True)
        self.assertEqual(sent, '')
        self.assertEqual(loads, [(2, CARD)])


class ShareLoginSsoTests(unittest.TestCase):
    def login_as(self, user, granted):
        from app import idp

        class Cur:
            def execute(self, sql, args):
                self.args = args

            def fetchone(self):
                return {'1': 1} if granted else None

        owner = {'id': 1, 'username': 'makuro', 'is_active': 1}
        cli = {'client_id': 'freemm_org', 'owner_id': 1, 'item_id': CARD}
        with patch.object(idp.db, 'cursor', lambda write=False: contextlib.nullcontext(Cur())), \
                patch.object(idp, '_lookup', lambda uid: owner if uid == 1 else None):
            return idp.login_as(cli, user)

    def test_code_is_issued_to_sharer_only_when_login_is_shared(self):
        zav = {'id': 2, 'username': 'zav', 'is_active': 1}
        self.assertEqual(self.login_as(zav, granted=True)['username'], 'makuro')
        self.assertIs(self.login_as(zav, granted=False), zav)

    def test_sharer_logs_in_as_themselves(self):
        makuro = {'id': 1, 'username': 'makuro', 'is_active': 1}
        self.assertIs(self.login_as(makuro, granted=True), makuro)


if __name__ == '__main__':
    unittest.main()
