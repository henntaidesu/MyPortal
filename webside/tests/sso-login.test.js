import test from 'node:test'
import assert from 'node:assert/strict'
import { ssoLoginFrom } from '../src/utils.js'

const portal = 'https://portal.makurochan.com'
for (const base of [
  'https://freemm.example.com/',
  `${portal}/api/proxy/6dk0sx3ezp/`,
  `${portal}/api/proxy/6dk0sx3ezp/__portal__/http/10.0.10.30:9600/`,
]) {
  for (const route of ['mercariV2/src/use_web/sso/', 'api/sso/']) {
    test(`derive login with public base ${base}${route}`, () => {
      const result = new URL(ssoLoginFrom(base + route + 'callback'))
      assert.equal(result.origin + result.pathname, base + route + 'login')
      assert.equal(result.searchParams.get('base'), base)
    })
  }
}
test('invalid callback is not inferred', () => {
  assert.equal(ssoLoginFrom('bad'), '')
  assert.equal(ssoLoginFrom(`${portal}/`), '')
})
