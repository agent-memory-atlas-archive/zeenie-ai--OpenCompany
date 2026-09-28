const NOTICE_INTERVAL_MS = 10_000
const MAX_NOTICE_KEYS = 128
const PROXY_ERROR = /\b(?:http|ws) proxy (?:socket )?error:/i
const WS_PROXY_ERROR = /\bws proxy (?:socket )?error:/i
const TRANSIENT_CODES = /\b(ECONNREFUSED|ECONNABORTED|ECONNRESET)\b/

// Requests can contain credentials in their query string. Only retain a bounded,
// printable pathname in diagnostics, never headers, query parameters or bodies.
const safePath = (url) => {
  try {
    return new URL(url || '/', 'http://vite.invalid').pathname
      .replace(/[\u0000-\u001f\u007f-\u009f]/g, '')
      .slice(0, 200)
  } catch {
    return '<invalid path>'
  }
}

export const createDevProxyDiagnostics = (logger, { now = Date.now } = {}) => {
  const logError = logger.error.bind(logger)
  const logInfo = logger.info.bind(logger)
  const logWarn = logger.warn.bind(logger)
  const errorPaths = new WeakMap()
  const seenErrors = new WeakSet()
  const notices = new Map()

  const notice = (key, message, log) => {
    const time = now()
    const previous = notices.get(key)
    if (previous && time >= previous.at && time - previous.at < NOTICE_INTERVAL_MS) {
      previous.suppressed += 1
      return
    }
    const suffix = previous?.suppressed
      ? ` (${previous.suppressed} similar failures since the last notice)`
      : ''
    // Long-lived dev servers must not retain an unlimited set of request paths.
    if (!previous && notices.size >= MAX_NOTICE_KEYS) {
      notices.delete(notices.keys().next().value)
    }
    notices.set(key, { at: time, suppressed: 0 })
    log(message + suffix, { timestamp: true })
  }

  // Vite installs its own proxy listeners after configure(). customLogger is
  // the supported way to avoid reporting the same socket error twice.
  // https://vite.dev/config/shared-options.html#customlogger
  logger.error = (message, options) => {
    const text = typeof message === 'string' ? message : String(message ?? '')
    const error = options?.error
    const code = error?.code || text.match(TRANSIENT_CODES)?.[1]
    const isRefused = PROXY_ERROR.test(text) && code === 'ECONNREFUSED'
    const isDisconnect = WS_PROXY_ERROR.test(text)
      && (code === 'ECONNABORTED' || code === 'ECONNRESET')

    if (!isRefused && !isDisconnect) {
      logError(message, options)
      return
    }

    const hasErrorObject = error !== null && typeof error === 'object'
    if (hasErrorObject) {
      if (seenErrors.has(error)) return
      seenErrors.add(error)
    }

    if (isRefused) {
      notice('backend-unavailable',
        'backend unavailable (ECONNREFUSED); WebSocket reconnects will retry', logInfo)
      return
    }

    const path = (hasErrorObject && errorPaths.get(error)) || '<unknown path>'
    notice(`transport:${code}:${path}`,
      `WebSocket proxy transport error for ${path}: ${code}`, logWarn)
  }

  const rememberErrorPath = (error, path) => {
    if (error !== null && typeof error === 'object') errorPaths.set(error, path)
  }

  // Attach before Vite's listeners so its logger receives the request context.
  // The ClientRequest response event is the non-upgrade HTTP response path;
  // successful WebSocket handshakes instead emit upgrade.
  // https://vite.dev/config/server-options.html#server-proxy
  const configureWebSocketProxy = (proxy) => {
    proxy.on('error', (error, request) => {
      rememberErrorPath(error, safePath(request?.url))
    })
    proxy.on('proxyReqWs', (proxyRequest, request, socket) => {
      const path = safePath(request?.url)
      socket.on('error', (error) => rememberErrorPath(error, path))
      proxyRequest.once('response', (response) => {
        if (response.statusCode === 101) return
        const status = Number.isInteger(response.statusCode)
          ? response.statusCode
          : 'unknown'
        notice(`upgrade:${status}:${path}`,
          `WebSocket upgrade failed for ${path}: backend returned HTTP ${status}`, logWarn)
      })
    })
  }

  return { logger, configureWebSocketProxy }
}
