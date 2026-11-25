"""2026-11-26 安全审计那几条修复的回归测试：不用数据库，也不连真网络。"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from app import proxy

PORTAL = 'https://portal.example.com'
CARD = 'card1'
BASE = f'/api/proxy/{CARD}/__portal__/https/'
PNG = b'\x89PNG....'


def _resolver(table):
    async def fake(host):
        name = proxy._bare_host(host)
        return (name,) if proxy._ip(name) is not None else table.get(name)
    return fake


class ProxyGuardTests(unittest.TestCase):
    """C1（外站只能从门户页面里进）、H2（外站不给 Cookie）、H1（普通用户不能转内网）。"""

    DNS = {
        'www.shop.example': ('93.184.216.34',),
        'cdn.shop.example': ('93.184.216.35',),
        'intranet.shop.example': ('10.0.0.5',),
    }

    def call(self, path, headers=None, *, private_ok=False, url='https://www.shop.example',
             upstream_headers=()):
        target = proxy._to_target({'id': CARD, 'url': url, 'proxy': 'site'})
        target = target._replace(private_ok=private_ok, login=1)
        seen = []

        async def upstream(request):
            seen.append(request)
            return httpx.Response(200, headers=[('content-type', 'application/json'), *upstream_headers],
                                  content=b'{}')

        async def public(host):
            return proxy._bare_host(host) == 'evil.example'

        app = FastAPI()
        app.include_router(proxy.router)
        app.dependency_overrides[proxy.require_login] = lambda: {'id': 1}
        client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        try:
            with patch.object(proxy, '_target', return_value=target), \
                    patch.object(proxy, '_client', return_value=client), \
                    patch.object(proxy, '_addresses', _resolver(self.DNS)), \
                    patch.object(proxy, '_public', public):
                with TestClient(app, base_url=PORTAL) as browser:
                    response = browser.get(path, headers=headers or {})
        finally:
            asyncio.run(client.aclose())
        return response, seen

    # ---- C1
    def test_crafted_cross_site_link_to_foreign_host_is_refused(self):
        response, seen = self.call(BASE + 'evil.example/x.html', {'sec-fetch-site': 'cross-site'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(seen, [])

    def test_link_without_fetch_metadata_or_referer_is_refused(self):
        response, seen = self.call(BASE + 'evil.example/x.html')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(seen, [])

    def test_click_out_from_proxied_page_still_works(self):
        response, seen = self.call(BASE + 'evil.example/x.html', {'sec-fetch-site': 'same-origin'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(seen), 1)
        # 老浏览器：没有 Sec-Fetch-*，靠 Referer 是同一张卡片底下的页面
        response, _ = self.call(BASE + 'evil.example/x.html',
                                {'referer': PORTAL + BASE + 'www.shop.example/item/1'})
        self.assertEqual(response.status_code, 200)

    def test_sibling_whitelisted_host_also_needs_same_origin(self):
        response, _ = self.call(BASE + 'cdn.shop.example/a.html', {'sec-fetch-site': 'cross-site'})
        self.assertEqual(response.status_code, 403)
        response, _ = self.call(BASE + 'cdn.shop.example/a.js', {'sec-fetch-site': 'same-origin'})
        self.assertEqual(response.status_code, 200)

    def test_card_own_host_accepts_deep_links(self):
        response, _ = self.call(BASE + 'www.shop.example/item/1', {'sec-fetch-site': 'cross-site'})
        self.assertEqual(response.status_code, 200)

    # ---- H2
    def test_foreign_host_gets_no_cookies_and_cannot_set_any(self):
        response, seen = self.call(
            BASE + 'evil.example/x', {'sec-fetch-site': 'same-origin',
                                      'cookie': 'shop_session=secret; portal_proxy_owner=1'},
            upstream_headers=[('set-cookie', 'shop_session=evil; Path=/')])
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('cookie', seen[0].headers)
        self.assertEqual(response.headers.get_list('set-cookie'), [])

    def test_whitelisted_host_still_gets_cookies(self):
        _, seen = self.call(BASE + 'www.shop.example/', {'cookie': 'shop_session=mine; portal_proxy_owner=1'})
        self.assertEqual(seen[0].headers['cookie'], 'shop_session=mine')

    # ---- H1
    def test_regular_users_card_cannot_reach_private_addresses(self):
        for url in ('http://10.0.0.5:8080', 'http://127.0.0.1:9921', 'http://nas'):
            with self.subTest(url=url):
                response, seen = self.call(f'/api/proxy/{CARD}/', url=url)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(seen, [])

    def test_whitelisted_name_resolving_to_intranet_is_refused_for_regular_users(self):
        response, seen = self.call(BASE + 'intranet.shop.example/', {'sec-fetch-site': 'same-origin'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(seen, [])

    def test_admin_card_reaches_intranet_but_never_link_local(self):
        response, _ = self.call(f'/api/proxy/{CARD}/', url='http://10.0.0.5:8080', private_ok=True)
        self.assertEqual(response.status_code, 200)
        response, seen = self.call(f'/api/proxy/{CARD}/latest/meta-data/',
                                   url='http://169.254.169.254', private_ok=True)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(seen, [])


class IconGuardTests(unittest.TestCase):
    """H1 的另一半：/api/icon 不再替普通用户往内网发请求，跳转每一跳都查。"""

    def run_fetch(self, url, private_ok):
        from app import icon

        def handler(request):
            if request.url.host == 'pub.example':
                return httpx.Response(302, headers={'location': 'http://10.0.0.1/f.ico'})
            return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})

        async def go():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
                return await icon._fetch_image(http, url, private_ok)

        with patch.object(icon, '_addresses', _resolver({'pub.example': ('8.8.8.8',)})):
            return asyncio.run(go())

    def test_regular_user_cannot_fetch_intranet_directly_or_via_redirect(self):
        self.assertIsNone(self.run_fetch('http://10.0.0.1/f.ico', False))
        self.assertIsNone(self.run_fetch('https://pub.example/a', False))

    def test_admin_can_fetch_intranet_but_not_link_local(self):
        self.assertIsNotNone(self.run_fetch('https://pub.example/a', True))
        self.assertIsNone(self.run_fetch('http://169.254.169.254/f.ico', True))


class OidcClaimTests(unittest.TestCase):
    """H3：首次单点登录不再按同名认领有口令 / 已绑定的本地账号。"""

    def resolve(self, claims, existing, identities=()):
        from app import oidc
        from app.routers import auth as routes
        linked = []
        with patch.object(routes.users, 'by_identity', lambda p, s: None),                 patch.object(routes.users, 'by_username', lambda n: existing),                 patch.object(routes.users, 'identities', lambda uid: list(identities)),                 patch.object(routes.users, 'link_identity', lambda *a: linked.append(a)),                 patch.object(oidc, 'is_admin', lambda c: None),                 patch.object(oidc.config, 'OIDC_USERNAME_CLAIM', 'preferred_username'):
            return routes._resolve(claims), linked

    def test_local_account_with_password_is_not_claimed(self):
        from app import oidc
        admin = {'id': 1, 'username': 'admin', 'password_hash': 'scrypt$...', 'is_active': 1, 'role': 'admin'}
        with self.assertRaises(oidc.OidcError):
            self.resolve({'sub': 'x', 'preferred_username': 'admin'}, admin)

    def test_already_bound_account_is_not_claimed(self):
        from app import oidc
        user = {'id': 2, 'username': 'bob', 'password_hash': None, 'is_active': 1, 'role': 'user'}
        with self.assertRaises(oidc.OidcError):
            self.resolve({'sub': 'x', 'preferred_username': 'bob'}, user, identities=[{'provider': 'oidc'}])

    def test_admin_precreated_oidc_only_account_is_claimed(self):
        user = {'id': 3, 'username': 'carol', 'password_hash': None, 'is_active': 1, 'role': 'user'}
        got, linked = self.resolve({'sub': 'x', 'preferred_username': 'carol'}, user)
        self.assertEqual(got['id'], 3)
        self.assertEqual(linked[0][:3], (3, 'oidc', 'x'))

    def test_unverified_email_cannot_be_username(self):
        from app import oidc
        with self.assertRaises(oidc.OidcError):
            self.resolve({'sub': 'x', 'email': 'admin@example.com'}, None)


class LoginLimitTests(unittest.TestCase):
    """H4：限流不再认请求里自己写的 X-Forwarded-For，另按用户名计数。"""

    def setUp(self):
        from app import auth
        auth._fails.clear()

    def test_forged_forwarded_for_is_ignored(self):
        from app import auth
        app = FastAPI()

        @app.get('/ip')
        def ip(request: __import__('fastapi').Request):
            return {'ip': auth.client_ip(request)}

        with TestClient(app) as client:
            got = client.get('/ip', headers={'x-forwarded-for': '1.2.3.4'}).json()['ip']
        self.assertNotEqual(got, '1.2.3.4')

    def test_username_is_limited_across_ips(self):
        from app import auth
        for i in range(auth._MAX_FAILS_USER):
            self.assertEqual(auth.login_blocked(f'10.0.0.{i}', 'Admin'), 0)
            auth.note_fail(f'10.0.0.{i}', 'Admin')
        self.assertGreater(auth.login_blocked('10.9.9.9', 'admin'), 0)
        self.assertEqual(auth.login_blocked('10.9.9.9', 'someone-else'), 0)

    def test_ip_is_limited(self):
        from app import auth
        for i in range(auth._MAX_FAILS):
            auth.note_fail('10.0.0.1', f'user{i}')
        self.assertGreater(auth.login_blocked('10.0.0.1', 'fresh'), 0)
        auth.clear_fails('10.0.0.1')
        self.assertEqual(auth.login_blocked('10.0.0.1', 'fresh'), 0)


if __name__ == '__main__':
    unittest.main()
