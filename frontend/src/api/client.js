import axios from 'axios'
import { clearSession, getSession } from '../auth/session.js'

export const API_ROOT = '/api/v1'
// Campaign Studio V2 is intentionally kept behind one path map. If the
// backend contract moves while the workspace is being integrated, update this
// map rather than every view or fixture.
export const STUDIO_V2_ROOT = '/studio/v2'
export const STUDIO_V2_ENDPOINTS = Object.freeze({
  productLocks: `${STUDIO_V2_ROOT}/product-locks`,
  providerProfiles: `${STUDIO_V2_ROOT}/provider-profiles`,
  generations: `${STUDIO_V2_ROOT}/generations`,
  engine: `${STUDIO_V2_ROOT}/engine`,
  directions: `${STUDIO_V2_ROOT}/directions`,
  batches: `${STUDIO_V2_ROOT}/batches`,
})
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
  get: (id, { signal } = {}) =>
    client.get(`/products/${encodeURIComponent(id)}`, { signal }),
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

const encodedId = (id) => encodeURIComponent(id)

export const studioV2 = {
  productLocks: {
    list: ({ cursor, productId, limit = 20, signal } = {}) =>
      client.get(STUDIO_V2_ENDPOINTS.productLocks, {
        signal,
        params: {
          ...(cursor ? { cursor } : {}),
          ...(productId ? { product_id: productId } : {}),
          limit,
        },
      }),
    create: (data, { idempotencyKey, signal } = {}) =>
      client.post(STUDIO_V2_ENDPOINTS.productLocks, data, {
        signal,
        headers: {
          'Idempotency-Key': idempotencyKey,
        },
      }),
    get: (id, { signal } = {}) =>
      client.get(`${STUDIO_V2_ENDPOINTS.productLocks}/${encodedId(id)}`, { signal }),
    artifact: (id, artifact, { signal } = {}) =>
      client.get(
        `${STUDIO_V2_ENDPOINTS.productLocks}/${encodedId(id)}/artifacts/${encodeURIComponent(artifact)}`,
        { signal, responseType: 'blob' }
      ),
    refine: (id, data, { signal } = {}) =>
      client.post(
        `${STUDIO_V2_ENDPOINTS.productLocks}/${encodedId(id)}/refine`,
        data,
        { signal }
      ),
    validate: (id, data = {}, { signal } = {}) =>
      client.post(
        `${STUDIO_V2_ENDPOINTS.productLocks}/${encodedId(id)}/validate`,
        data,
        { signal }
      ),
    reject: (id, data = {}, { signal } = {}) =>
      client.post(
        `${STUDIO_V2_ENDPOINTS.productLocks}/${encodedId(id)}/reject`,
        data,
        { signal }
      ),
  },
  providerProfiles: {
    list: ({ signal } = {}) =>
      client.get(STUDIO_V2_ENDPOINTS.providerProfiles, { signal }),
  },
  engine: {
    status: ({ signal } = {}) =>
      client.get(`${STUDIO_V2_ENDPOINTS.engine}/status`, { signal }),
  },
  directions: {
    preview: (data, { signal } = {}) =>
      client.post(`${STUDIO_V2_ENDPOINTS.directions}/preview`, data, { signal }),
  },
  batches: {
    list: ({ cursor, limit = 20, signal } = {}) =>
      client.get(STUDIO_V2_ENDPOINTS.batches, {
        signal,
        params: { ...(cursor ? { cursor } : {}), limit },
      }),
    create: (data, { idempotencyKey, signal } = {}) =>
      client.post(STUDIO_V2_ENDPOINTS.batches, data, {
        signal,
        headers: { 'Idempotency-Key': idempotencyKey },
      }),
    get: (id, { signal } = {}) =>
      client.get(`${STUDIO_V2_ENDPOINTS.batches}/${encodedId(id)}`, { signal }),
  },
  generations: {
    create: (data, { idempotencyKey, signal } = {}) =>
      client.post(STUDIO_V2_ENDPOINTS.generations, data, {
        signal,
        headers: {
          'Idempotency-Key': idempotencyKey,
        },
      }),
    get: (id, { signal } = {}) =>
      client.get(`${STUDIO_V2_ENDPOINTS.generations}/${encodedId(id)}`, { signal }),
    list: ({ cursor, limit = 20, signal } = {}) =>
      client.get(STUDIO_V2_ENDPOINTS.generations, {
        signal,
        params: {
          ...(cursor ? { cursor } : {}),
          limit,
        },
      }),
    cancel: (id, { signal } = {}) =>
      client.post(`${STUDIO_V2_ENDPOINTS.generations}/${encodedId(id)}/cancel`, {}, { signal }),
    variants: (id, { signal } = {}) =>
      client.get(`${STUDIO_V2_ENDPOINTS.generations}/${encodedId(id)}/variants`, { signal }),
    variantImage: (generationId, variantId, { signal } = {}) =>
      client.get(
        `${STUDIO_V2_ENDPOINTS.generations}/${encodedId(generationId)}/variants/${encodedId(variantId)}/image`,
        { signal, responseType: 'blob' }
      ),
    exportBundle: (id, { signal } = {}) =>
      client.get(`${STUDIO_V2_ENDPOINTS.generations}/${encodedId(id)}/export`, {
        signal,
        responseType: 'blob',
        timeout: DOWNLOAD_TIMEOUT,
      }),
  },
}

export default client
