const TOKEN_KEY = 'cosmetique_ai_token'
const EMAIL_KEY = 'cosmetique_ai_email'
const SESSION_EVENT = 'cosmetique:session-change'
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[89ab0-9a-f][0-9a-f]{3}-[0-9a-f]{12}$/i

const normalizeStoredValue = (value) => {
  const normalized = typeof value === 'string' ? value.trim() : ''
  if (!normalized || normalized === 'undefined' || normalized === 'null') {
    return null
  }
  return normalized
}

const decodeJwtPayload = (token) => {
  const payload = token.split('.')[1]
  if (!payload) return null

  try {
    const base64 = payload.replace(/-/g, '+').replace(/_/g, '/')
    const padding = '='.repeat((4 - (base64.length % 4)) % 4)
    return JSON.parse(atob(base64 + padding))
  } catch {
    return null
  }
}

const isValidToken = (token) => {
  const segments = token.split('.')
  if (segments.length !== 3 || segments.some((segment) => !segment)) return false
  const payload = decodeJwtPayload(token)
  if (
    !payload ||
    typeof payload.exp !== 'number' ||
    !Number.isFinite(payload.exp) ||
    typeof payload.sub !== 'string' ||
    !UUID_PATTERN.test(payload.sub)
  ) {
    return false
  }
  return payload.exp * 1000 > Date.now()
}

const emitSessionChange = () => {
  window.dispatchEvent(new Event(SESSION_EVENT))
}

export const getSession = () => ({
  token: normalizeStoredValue(localStorage.getItem(TOKEN_KEY)),
  email: normalizeStoredValue(localStorage.getItem(EMAIL_KEY)),
})

export const hasUsableSession = () => {
  const { token } = getSession()
  if (token && isValidToken(token)) return true
  if (token) {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(EMAIL_KEY)
  }
  return false
}

export const setSession = ({ token, email }) => {
  const normalizedToken = normalizeStoredValue(token)
  if (!normalizedToken || !isValidToken(normalizedToken)) {
    throw new TypeError('Un jeton de session valide est requis.')
  }

  localStorage.setItem(TOKEN_KEY, normalizedToken)
  if (normalizeStoredValue(email)) {
    localStorage.setItem(EMAIL_KEY, email.trim())
  } else {
    localStorage.removeItem(EMAIL_KEY)
  }
  emitSessionChange()
}

export const clearSession = () => {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(EMAIL_KEY)
  emitSessionChange()
}

export const subscribeToSession = (listener) => {
  window.addEventListener(SESSION_EVENT, listener)
  window.addEventListener('storage', listener)
  return () => {
    window.removeEventListener(SESSION_EVENT, listener)
    window.removeEventListener('storage', listener)
  }
}

export { EMAIL_KEY, SESSION_EVENT, TOKEN_KEY }
