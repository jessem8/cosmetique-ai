import { describe, expect, it } from 'vitest'
import {
  clearSession,
  getSession,
  hasUsableSession,
  setSession,
} from './session.js'
import { createTestToken, TEST_TOKEN } from '../test/token.js'

describe('session storage', () => {
  it.each(['', 'undefined', 'null', '   '])(
    'rejects the unusable token value %j',
    (token) => {
      localStorage.setItem('cosmetique_ai_token', token)
      expect(hasUsableSession()).toBe(false)
    }
  )

  it('stores and clears a validated token and email', () => {
    setSession({ token: TEST_TOKEN, email: 'studio@example.com' })

    expect(getSession()).toEqual({
      token: TEST_TOKEN,
      email: 'studio@example.com',
    })
    expect(hasUsableSession()).toBe(true)

    clearSession()
    expect(getSession()).toEqual({ token: null, email: null })
  })

  it('rejects a JWT whose exp timestamp is in the past', () => {
    localStorage.setItem(
      'cosmetique_ai_token',
      createTestToken({ exp: Math.floor(Date.now() / 1000) - 60 })
    )

    expect(hasUsableSession()).toBe(false)
    expect(getSession().token).toBeNull()
  })

  it.each([
    'header.payload.signature',
    createTestToken({ exp: undefined }),
    createTestToken({ sub: 'not-a-user-id' }),
  ])('fails closed and clears malformed JWT %j', (token) => {
    localStorage.setItem('cosmetique_ai_token', token)

    expect(hasUsableSession()).toBe(false)
    expect(getSession().token).toBeNull()
  })

  it('refuses to store a malformed session token', () => {
    expect(() =>
      setSession({
        token: 'header.payload.signature',
        email: 'studio@example.com',
      })
    ).toThrow(/jeton de session valide/i)
  })
})
