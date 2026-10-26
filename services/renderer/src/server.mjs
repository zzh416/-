import http from 'node:http'

import { closeBrowser, RenderFailure, renderPage } from './renderer.mjs'

const PORT = 9100
const MAX_BODY_BYTES = 16 * 1024
const MAX_HTML_BYTES = 5 * 1024 * 1024
let active = false

function sendJson(response, status, payload) {
  const body = JSON.stringify(payload)
  response.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': Buffer.byteLength(body),
    'cache-control': 'no-store',
  })
  response.end(body)
}

async function readJson(request) {
  const chunks = []
  let size = 0
  for await (const chunk of request) {
    size += chunk.length
    if (size > MAX_BODY_BYTES) {
      throw new RenderFailure('REQUEST_TOO_LARGE', '请求体超过大小上限', 413)
    }
    chunks.push(chunk)
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf8'))
  } catch {
    throw new RenderFailure('REQUEST_INVALID', '请求格式不正确', 400)
  }
}

const server = http.createServer(async (request, response) => {
  if (request.method === 'GET' && request.url === '/health') {
    sendJson(response, 200, { status: 'ok' })
    return
  }
  if (request.method !== 'POST' || request.url !== '/render') {
    sendJson(response, 404, { code: 'NOT_FOUND', message: 'not found' })
    return
  }
  if (active) {
    sendJson(response, 503, { code: 'RENDERER_BUSY', message: 'renderer is busy' })
    return
  }

  active = true
  try {
    const payload = await readJson(request)
    const url = typeof payload?.url === 'string' ? payload.url : ''
    const requestedMax = Number(payload?.maxHtmlBytes)
    const maxHtmlBytes = Number.isSafeInteger(requestedMax)
      ? Math.min(Math.max(requestedMax, 1024), MAX_HTML_BYTES)
      : MAX_HTML_BYTES
    const rendered = await renderPage(url, maxHtmlBytes)
    sendJson(response, 200, rendered)
  } catch (error) {
    if (error instanceof RenderFailure) {
      sendJson(response, error.status, { code: error.code, message: error.message })
      return
    }
    console.error('Unexpected renderer failure', error)
    sendJson(response, 500, { code: 'RENDERER_INTERNAL_ERROR', message: 'render failed' })
  } finally {
    active = false
  }
})

server.listen(PORT, '0.0.0.0', () => {
  console.log(`renderer listening on ${PORT}`)
})

async function shutdown() {
  server.close()
  await closeBrowser()
  process.exit(0)
}

process.on('SIGTERM', shutdown)
process.on('SIGINT', shutdown)
