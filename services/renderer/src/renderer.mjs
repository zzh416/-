import { chromium } from 'playwright'

import { assertPublicUrl, UrlSecurityError } from './security.mjs'

const MAX_REQUESTS = 120
const NAVIGATION_TIMEOUT_MS = 20_000
const SETTLE_TIMEOUT_MS = 6_000

export class RenderFailure extends Error {
  constructor(code, message, status = 422) {
    super(message)
    this.code = code
    this.status = status
  }
}

let browserPromise

async function getBrowser() {
  if (!browserPromise) {
    browserPromise = chromium.launch({ headless: true, chromiumSandbox: true })
    browserPromise.catch(() => {
      browserPromise = undefined
    })
  }
  return browserPromise
}

async function waitForStableText(page) {
  const deadline = Date.now() + SETTLE_TIMEOUT_MS
  let previousLength = -1
  let stableSamples = 0
  while (Date.now() < deadline) {
    const length = await page.evaluate(() => document.body?.innerText?.trim().length ?? 0)
    if (length > 0 && length === previousLength) {
      stableSamples += 1
      if (stableSamples >= 3) return
    } else {
      stableSamples = 0
      previousLength = length
    }
    await page.waitForTimeout(400)
  }
}

async function boundedScroll(page) {
  for (let index = 0; index < 3; index += 1) {
    const moved = await page.evaluate(() => {
      const before = window.scrollY
      window.scrollBy(0, Math.max(window.innerHeight, 600))
      return window.scrollY > before
    })
    if (!moved) break
    await page.waitForTimeout(350)
  }
  await page.evaluate(() => window.scrollTo(0, 0))
}

function looksLikeAuthenticationWall(text) {
  if (text.length > 500) return false
  return [
    /请先登录/,
    /登录后(查看|继续|访问)/,
    /扫码登录/,
    /sign in to continue/i,
    /log in to continue/i,
  ].some((pattern) => pattern.test(text))
}

export async function renderPage(url, maxHtmlBytes) {
  let normalized
  try {
    normalized = await assertPublicUrl(url)
  } catch (error) {
    if (error instanceof UrlSecurityError) {
      throw new RenderFailure(error.code, error.message)
    }
    throw error
  }

  const browser = await getBrowser()
  const browserVersion = browser.version()
  const context = await browser.newContext({
    acceptDownloads: false,
    javaScriptEnabled: true,
    serviceWorkers: 'block',
    viewport: { width: 1365, height: 900 },
    userAgent:
      'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 ' +
      `(KHTML, like Gecko) Chrome/${browserVersion} Safari/537.36`,
  })
  const page = await context.newPage()
  let requestCount = 0
  let requestLimitExceeded = false
  let mainRequestBlocked = false

  await context.route('**/*', async (route) => {
    const request = route.request()
    requestCount += 1
    if (requestCount > MAX_REQUESTS) {
      requestLimitExceeded = true
      await route.abort('blockedbyclient')
      return
    }
    if (!['GET', 'HEAD'].includes(request.method())) {
      await route.abort('blockedbyclient')
      return
    }
    if (['image', 'media', 'font', 'websocket'].includes(request.resourceType())) {
      await route.abort('blockedbyclient')
      return
    }
    try {
      await assertPublicUrl(request.url())
      await route.continue()
    } catch {
      if (request.isNavigationRequest() && request.frame() === page.mainFrame()) {
        mainRequestBlocked = true
      }
      await route.abort('blockedbyclient')
    }
  })

  context.on('page', async (popup) => {
    if (popup !== page) await popup.close()
  })

  try {
    let response
    try {
      response = await page.goto(normalized, {
        waitUntil: 'domcontentloaded',
        timeout: NAVIGATION_TIMEOUT_MS,
      })
    } catch (error) {
      if (mainRequestBlocked) {
        throw new RenderFailure('WEB_PRIVATE_ADDRESS', '页面导航到了本机、内网或保留地址')
      }
      if (error?.name === 'TimeoutError') {
        throw new RenderFailure('DYNAMIC_RENDER_TIMEOUT', '网页动态内容加载超时')
      }
      throw new RenderFailure('WEB_ACCESS_DENIED', '网页无法由匿名浏览器打开')
    }
    if (requestLimitExceeded) {
      throw new RenderFailure('DYNAMIC_RENDER_REQUEST_LIMIT', '网页加载的资源数量超过上限')
    }
    const status = response?.status() ?? 0
    if ([401, 403].includes(status)) {
      throw new RenderFailure('WEB_ACCESS_DENIED', '网页拒绝匿名浏览器访问')
    }
    if (status >= 400) {
      throw new RenderFailure('WEB_ACCESS_DENIED', `网页返回 HTTP ${status}`)
    }

    await page.waitForLoadState('networkidle', { timeout: 5_000 }).catch(() => undefined)
    await waitForStableText(page)
    await boundedScroll(page)
    if (requestLimitExceeded) {
      throw new RenderFailure('DYNAMIC_RENDER_REQUEST_LIMIT', '网页加载的资源数量超过上限')
    }

    const finalUrl = page.url()
    try {
      await assertPublicUrl(finalUrl)
    } catch (error) {
      if (error instanceof UrlSecurityError) {
        throw new RenderFailure(error.code, error.message)
      }
      throw error
    }
    const visibleText = (await page.locator('body').innerText()).trim()
    if (looksLikeAuthenticationWall(visibleText)) {
      throw new RenderFailure('WEB_AUTH_REQUIRED', '页面需要登录')
    }
    const html = await page.content()
    if (!html.trim() || Buffer.byteLength(html, 'utf8') > maxHtmlBytes) {
      throw new RenderFailure('DYNAMIC_RENDER_TOO_LARGE', '网页渲染结果超过大小上限')
    }
    return {
      url: finalUrl,
      contentType: 'text/html',
      title: (await page.title()).trim().slice(0, 200),
      html,
    }
  } finally {
    await context.close()
  }
}

export async function closeBrowser() {
  if (!browserPromise) return
  const browser = await browserPromise.catch(() => undefined)
  browserPromise = undefined
  await browser?.close()
}
