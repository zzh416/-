import { createReadStream, statSync } from 'node:fs'
import http from 'node:http'
import https from 'node:https'
import { extname, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const host = process.env.HOST || '0.0.0.0'
const port = Number.parseInt(process.env.PORT || '5173', 10)
const apiTarget = new URL(process.env.VITE_PROXY_TARGET || 'http://api:9000')
const distDirectory = resolve(fileURLToPath(new URL('./dist/', import.meta.url)))
const indexPath = resolve(distDirectory, 'index.html')

const contentTypes = new Map([
  ['.css', 'text/css; charset=utf-8'],
  ['.html', 'text/html; charset=utf-8'],
  ['.ico', 'image/x-icon'],
  ['.jpeg', 'image/jpeg'],
  ['.jpg', 'image/jpeg'],
  ['.js', 'text/javascript; charset=utf-8'],
  ['.json', 'application/json; charset=utf-8'],
  ['.png', 'image/png'],
  ['.svg', 'image/svg+xml'],
  ['.webp', 'image/webp'],
  ['.woff', 'font/woff'],
  ['.woff2', 'font/woff2'],
])

const securityHeaders = {
  'Content-Security-Policy': [
    "default-src 'self'",
    "base-uri 'self'",
    "connect-src 'self'",
    "font-src 'self' data:",
    "form-action 'self'",
    "frame-ancestors 'none'",
    "img-src 'self' data: blob:",
    "object-src 'none'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
  ].join('; '),
  'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
  'Referrer-Policy': 'no-referrer',
  'X-Content-Type-Options': 'nosniff',
  'X-Frame-Options': 'DENY',
}

function writePlain(response, statusCode, message) {
  response.writeHead(statusCode, {
    ...securityHeaders,
    'Cache-Control': 'no-store',
    'Content-Type': 'text/plain; charset=utf-8',
  })
  response.end(message)
}

function proxyApi(request, response) {
  const transport = apiTarget.protocol === 'https:' ? https : http
  const headers = { ...request.headers, host: apiTarget.host }
  delete headers.connection
  const proxyRequest = transport.request(
    {
      protocol: apiTarget.protocol,
      hostname: apiTarget.hostname,
      port: apiTarget.port,
      method: request.method,
      path: request.url,
      headers,
      timeout: 120_000,
    },
    (proxyResponse) => {
      response.writeHead(proxyResponse.statusCode || 502, proxyResponse.headers)
      proxyResponse.pipe(response)
    },
  )
  proxyRequest.on('timeout', () => proxyRequest.destroy(new Error('API proxy timeout')))
  proxyRequest.on('error', () => {
    if (!response.headersSent) writePlain(response, 502, 'API 暂时不可用')
    else response.destroy()
  })
  request.pipe(proxyRequest)
}

function serveFile(request, response, filePath, immutable = false) {
  const extension = extname(filePath).toLowerCase()
  response.writeHead(200, {
    ...securityHeaders,
    'Cache-Control': immutable ? 'public, max-age=31536000, immutable' : 'no-cache',
    'Content-Type': contentTypes.get(extension) || 'application/octet-stream',
  })
  if (request.method === 'HEAD') response.end()
  else createReadStream(filePath).pipe(response)
}

const server = http.createServer((request, response) => {
  const rawPath = request.url || '/'
  if (rawPath === '/healthz') {
    writePlain(response, 200, 'ok')
    return
  }
  if (rawPath === '/api' || rawPath.startsWith('/api/')) {
    proxyApi(request, response)
    return
  }
  if (!['GET', 'HEAD'].includes(request.method || '')) {
    writePlain(response, 405, 'Method Not Allowed')
    return
  }

  let pathname
  try {
    pathname = decodeURIComponent(new URL(rawPath, 'http://localhost').pathname)
  } catch {
    writePlain(response, 400, 'Bad Request')
    return
  }
  const requestedPath = resolve(distDirectory, `.${pathname}`)
  if (requestedPath !== distDirectory && !requestedPath.startsWith(`${distDirectory}${sep}`)) {
    writePlain(response, 400, 'Bad Request')
    return
  }

  try {
    if (statSync(requestedPath).isFile()) {
      serveFile(request, response, requestedPath, pathname.startsWith('/assets/'))
      return
    }
  } catch {
    // Missing client-side routes fall through to the SPA entry document.
  }
  if (extname(pathname)) {
    writePlain(response, 404, 'Not Found')
    return
  }
  serveFile(request, response, indexPath)
})

server.listen(port, host, () => {
  console.log(`web_ready host=${host} port=${port}`)
})
