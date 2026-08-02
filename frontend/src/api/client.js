import axios from 'axios'
import { clearSession, getSession } from '../auth/session.js'

export const API_ROOT = '/api/v1'
const DEFAULT_TIMEOUT = 30_000
const DOWNLOAD_TIMEOUT = 120_000

export class ApiError extends Error {
  constructor({
    message,
    code = 'INTERNAL_ERROR',
    status = null,
    retryable = false,
    request = null,
    cause,
  }) {
    super(message, { cause })
    this.name = 'ApiError'
    this.code = code
    this.status = status
    this.retryable = retryable
    this.request = request
  }

  toJSON() {
    return {
      name: this.name,
      message: this.message,
      code: this.code,
      status: this.status,
      retryable: this.retryable,
      request: this.request,
    }
  }
}

const requestContext = (error) => {
  if (!error?.config) return null
  return {
    method: error.config.method?.toUpperCase() || null,
    url: error.config.url || null,
  }
}

export const normalizeApiError = (error) => {
  if (error instanceof ApiError) return error

  if (axios.isCancel(error)) {
    return new ApiError({
      code: 'REQUEST_CANCELLED',
      message: 'La requête a été annulée.',
      request: requestContext(error),
      cause: error,
    })
  }

  const status = error?.response?.status ?? null
  const body = error?.response?.data
  const structured =
    body?.error && typeof body.error === 'object'
      ? body.error
      : body?.detail && typeof body.detail === 'object'
        ? body.detail
        : null
  const code =
    structured?.code ||
    body?.error_code ||
    (status === 401 ? 'AUTH_REQUIRED' : 'INTERNAL_ERROR')
  const message =
    structured?.message ||
    (typeof body?.detail === 'string' ? body.detail : null) ||
    body?.message ||
    (error?.request
      ? 'Le service ne répond pas. Vérifiez votre connexion.'
      : 'Une erreur inattendue est survenue.')

  return new ApiError({
    status,
    code,
    message,
    retryable: Boolean(structured?.retryable ?? body?.retryable),
    request: requestContext(error),
    cause: error,
  })
}

const client = axios.create({
  baseURL: API_ROOT,
  timeout: DEFAULT_TIMEOUT,
})

client.interceptors.request.use((config) => {
  const { token } = getSession()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

client.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      clearSession()
    }
    return Promise.reject(normalizeApiError(error))
  }
)

export const auth = {
  register: (data, { signal } = {}) =>
    client.post('/auth/register', data, { signal }),
  login: (data, { signal } = {}) =>
    client.post('/auth/login', data, { signal }),
}

export const products = {
  create: (formData, { signal } = {}) =>
    client.post('/products', formData, { signal }),
  getImage: (id, { signal } = {}) =>
    client.get(`/products/${encodeURIComponent(id)}/image`, {
      signal,
      responseType: 'blob',
    }),
}

export const generations = {
  create: (productId, data, { idempotencyKey, signal } = {}) =>
    client.post(
      `/products/${encodeURIComponent(productId)}/generations`,
      data,
      {
        signal,
        headers: {
          'Idempotency-Key': idempotencyKey,
        },
      }
    ),
  get: (id, { signal } = {}) =>
    client.get(`/generations/${encodeURIComponent(id)}`, { signal }),
  list: ({ cursor, limit = 20, signal } = {}) =>
    client.get('/generations', {
      signal,
      params: {
        ...(cursor ? { cursor } : {}),
        limit,
      },
    }),
  getArtifact: (id, filename, { signal } = {}) =>
    client.get(
      `/generations/${encodeURIComponent(id)}/artifacts/${encodeURIComponent(filename)}`,
      {
        signal,
        responseType: 'blob',
      }
    ),
  getBundle: (id, { signal } = {}) =>
    client.get(`/generations/${encodeURIComponent(id)}/bundle`, {
      signal,
      responseType: 'blob',
      timeout: DOWNLOAD_TIMEOUT,
    }),
  generateCaption: (id, platform, { signal } = {}) =>
    client.post(
      `/generations/${encodeURIComponent(id)}/captions/${encodeURIComponent(platform)}`,
      {},
      { signal }
    ),
  enhance: (id, platform, { signal } = {}) =>
    client.post(
      `/generations/${encodeURIComponent(id)}/enhancements/${encodeURIComponent(platform)}`,
      {},
      { signal }
    ),
}

export default client
