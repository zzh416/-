import assert from 'node:assert/strict'
import test from 'node:test'

import { assertPublicUrl, normalizePublicUrl, UrlSecurityError } from '../src/security.mjs'

test('normalizes public http URLs and removes fragments', () => {
  assert.equal(
    normalizePublicUrl('https://example.com/article?q=1#answer').toString(),
    'https://example.com/article?q=1',
  )
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
