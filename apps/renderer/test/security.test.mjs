import assert from 'node:assert/strict'
import test from 'node:test'

import { assertPublicUrl, normalizePublicUrl, UrlSecurityError } from '../src/security.mjs'

for (const fragment of ['#answer', '#/chapter/1?view=full', '#!/chapter/2']) {
  test(`preserves the full URL fragment ${fragment}`, () => {
    const url = `https://example.com/Guide/Chapter/?b=2&a=1${fragment}`
    assert.equal(normalizePublicUrl(url).toString(), url)
  })
}

test('normalizes matching default ports without merging different ports', () => {
  assert.equal(normalizePublicUrl('HTTP://EXAMPLE.COM:80/').toString(), 'http://example.com/')
  assert.equal(normalizePublicUrl('https://example.com:443/').toString(), 'https://example.com/')
  assert.equal(normalizePublicUrl('https://example.com:80/').toString(), 'https://example.com:80/')
})

test('retains brackets around public IPv6 addresses', () => {
  assert.equal(
    normalizePublicUrl('https://[2606:2800:220:1:248:1893:25c8:1946]:443/Guide/?b=2&a=1').toString(),
    'https://[2606:2800:220:1:248:1893:25c8:1946]/Guide/?b=2&a=1',
  )
})

test('public URL validation preserves a hash route', async () => {
  const lookup = async () => [{ address: '93.184.216.34', family: 4 }]
  const url = 'https://example.com/Guide/?b=2&a=1#!/chapter/2?view=full'
  assert.equal(await assertPublicUrl(url, lookup), url)
})

test('rejects credentials and non-standard ports', () => {
  assert.throws(() => normalizePublicUrl('https://user:pass@example.com/'), UrlSecurityError)
  assert.throws(() => normalizePublicUrl('https://example.com:8443/'), UrlSecurityError)
})

test('accepts only hosts whose every address is public', async () => {
  const publicLookup = async () => [
    { address: '93.184.216.34', family: 4 },
    { address: '2606:2800:220:1:248:1893:25c8:1946', family: 6 },
  ]
  assert.equal(await assertPublicUrl('https://example.com/', publicLookup), 'https://example.com/')

  const mixedLookup = async () => [
    { address: '93.184.216.34', family: 4 },
    { address: '127.0.0.1', family: 4 },
  ]
  await assert.rejects(
    assertPublicUrl('https://example.com/', mixedLookup),
    (error) => error instanceof UrlSecurityError && error.code === 'WEB_PRIVATE_ADDRESS',
  )
})

test('rejects IPv4-mapped private IPv6 addresses', async () => {
  const lookup = async () => [{ address: '::ffff:10.0.0.8', family: 6 }]
  await assert.rejects(
    assertPublicUrl('https://example.com/', lookup),
    (error) => error instanceof UrlSecurityError && error.code === 'WEB_PRIVATE_ADDRESS',
  )
})
