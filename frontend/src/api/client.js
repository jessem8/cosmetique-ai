import axios from 'axios'

const client = axios.create({
  baseURL: import.meta.env.VITE_API_URL || 'http://localhost:8000',
  timeout: 30000,
})

// Request interceptor — attach Bearer token
client.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('cosmetique_ai_token')
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => Promise.reject(error)
)

// Response interceptor — handle 401
client.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('cosmetique_ai_token')
      localStorage.removeItem('cosmetique_ai_email')
      window.location.href = '/login'
    }
    return Promise.reject(error)
  }
)

// ---------- Auth ----------
export const auth = {
  register: (data) => client.post('/auth/register', data),
  login: (data) => client.post('/auth/login', data),
}

// ---------- Products ----------
export const products = {
  create: (formData) =>
    client.post('/products', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }),
  list: () => client.get('/products'),
  get: (id) => client.get(`/products/${id}`),
}

// ---------- Generations ----------
export const generations = {
  create: (productId, data) => client.post(`/generations/${productId}`, data),
  get: (id) => client.get(`/generations/${id}`),
  getAssets: (id) => client.get(`/generations/${id}/assets`),
  listForProduct: (productId, limit = 5) =>
    client.get(`/products/${productId}/generations`, { params: { limit } }),
  regenerateText: (id, tone) =>
    client.post(`/generations/${id}/regenerate-text`, null, {
      params: { tone },
    }),
  regenerateDecor: (id) => client.post(`/generations/${id}/regenerate-decor`),
  downloadZip: (id) =>
    client.get(`/generations/${id}/download-zip`, {
      responseType: 'blob',
    }),
  downloadZipUrl: (id) => `${client.defaults.baseURL}/generations/${id}/download-zip`,
}

export default client
