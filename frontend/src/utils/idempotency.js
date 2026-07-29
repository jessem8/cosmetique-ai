export const createIdempotencyKey = () => {
  const id =
    typeof globalThis.crypto?.randomUUID === 'function'
      ? globalThis.crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2, 18)}`
  return `campaign-${id}`
}
