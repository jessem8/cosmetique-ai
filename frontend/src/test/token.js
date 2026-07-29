const encodeSegment = (value) =>
  btoa(JSON.stringify(value))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/g, '')

export const createTestToken = (payload = {}) =>
  `${encodeSegment({ alg: 'none', typ: 'JWT' })}.${encodeSegment({
    sub: '11111111-1111-4111-8111-111111111111',
    exp: 4_102_444_800,
    ...payload,
  })}.test-signature`

export const TEST_TOKEN = createTestToken()
