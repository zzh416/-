import dns from 'node:dns/promises'
import ipaddr from 'ipaddr.js'

export class UrlSecurityError extends Error {
  constructor(code, message) {
    super(message)
    this.code = code
  }
}

export function normalizePublicUrl(value) {
  let parsed
  try {
    parsed = new URL(value)
  } catch {
    throw new UrlSecurityError('WEB_URL_INVALID', '网页地址格式不正确')
  }
  if (!['http:', 'https:'].includes(parsed.protocol)) {
    throw new UrlSecurityError('WEB_URL_INVALID', '网页地址只支持 HTTP 或 HTTPS')
  }
  if (parsed.username || parsed.password || !parsed.hostname) {
    throw new UrlSecurityError('WEB_URL_INVALID', '网页地址不能包含账号、密码或空域名')
  }
  const port = parsed.port || (parsed.protocol === 'https:' ? '443' : '80')
  if (!['80', '443'].includes(port)) {
    throw new UrlSecurityError('WEB_URL_INVALID', '网页地址只支持 80 或 443 端口')
  }
  parsed.hash = ''
  return parsed
}

function isGlobalAddress(value) {
  let parsed
  try {
    parsed = ipaddr.parse(value)
  } catch {
    return false
  }
  if (parsed.kind() === 'ipv6' && parsed.isIPv4MappedAddress()) {
    parsed = parsed.toIPv4Address()
  }
  return parsed.range() === 'unicast'
}

export async function assertPublicUrl(value, lookup = dns.lookup) {
  const parsed = normalizePublicUrl(value)
  let records
  try {
    records = await lookup(parsed.hostname, { all: true, verbatim: true })
  } catch {
    throw new UrlSecurityError('WEB_DNS_FAILED', '网页域名无法解析')
  }
  if (!Array.isArray(records) || records.length === 0) {
    throw new UrlSecurityError('WEB_DNS_FAILED', '网页域名没有可用地址')
  }
  if (records.some((record) => !record?.address || !isGlobalAddress(record.address))) {
    throw new UrlSecurityError(
      'WEB_PRIVATE_ADDRESS',
      '网页地址不能指向本机、内网或保留地址',
    )
  }
  return parsed.toString()
}
