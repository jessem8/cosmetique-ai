export const fixtureCandidates = [
  { id: 'candidate-primary', label: 'Primary pack', confidence: 0.96, box: { x: 0.28, y: 0.18, width: 0.44, height: 0.64 } },
  { id: 'candidate-shadow', label: 'Pack plus shadow', confidence: 0.81, box: { x: 0.22, y: 0.16, width: 0.55, height: 0.69 } },
  { id: 'candidate-cap', label: 'Cap detail', confidence: 0.72, box: { x: 0.35, y: 0.2, width: 0.28, height: 0.22 } },
]

export const fixtureProviders = [
  {
    id: 'studio-safe-v1',
    name: 'Studio Safe',
    model: 'Product-preserve 1.4',
    description: 'Verified product-conditioned rendering for controlled social scenes.',
    capabilities: ['mask-preservation', 'scene-generation', 'qa-provenance'],
    costPerVariant: 0.8,
    maxVariants: 4,
    supportsCustomDirection: true,
  },
  {
    id: 'editorial-v2',
    name: 'Editorial Light',
    model: 'Editorial Light 2.1',
    description: 'Soft studio environments with strict copy-safe negative space.',
    capabilities: ['mask-preservation', 'scene-generation', 'qa-provenance', 'wide-crop'],
    costPerVariant: 1.15,
    maxVariants: 3,
    supportsCustomDirection: true,
  },
  {
    id: 'layout-only-v1',
    name: 'Layout Preview',
    model: 'Layout Preview 0.9',
    description: 'Composition preview only. Final product-preserving render is unavailable.',
    capabilities: ['scene-generation'],
    costPerVariant: 0.2,
    maxVariants: 4,
    supportsCustomDirection: false,
  },
]

export const fixtureVariants = [
  {
    id: 'variant-sage-01',
    label: 'Soft daylight',
    imageUrl: '/studio/fixture-scene.svg',
    platform: 'instagram',
    qa: { status: 'pass', score: 0.98, failures: [] },
    provenance: { provider: 'Studio Safe', model: 'Product-preserve 1.4', seed: 2808, cost: 0.8 },
  },
  {
    id: 'variant-sage-02',
    label: 'Cool counter',
    imageUrl: '/studio/fixture-scene-alt.svg',
    platform: 'instagram',
    qa: { status: 'review', score: 0.91, failures: ['Lower edge confidence below review threshold'] },
    provenance: { provider: 'Studio Safe', model: 'Product-preserve 1.4', seed: 2809, cost: 0.8 },
  },
  {
    id: 'variant-sage-03',
    label: 'Wide editorial',
    imageUrl: '/studio/fixture-scene-wide.svg',
    platform: 'linkedin',
    qa: { status: 'pass', score: 0.95, failures: [] },
    provenance: { provider: 'Studio Safe', model: 'Product-preserve 1.4', seed: 2810, cost: 0.8 },
  },
]

export const fixtureLock = {
  id: 'lock-fixture-01',
  status: 'needs_review',
  revision: 2,
  candidateId: fixtureCandidates[0].id,
  bbox: fixtureCandidates[0].box,
  points: [
    { id: 'positive-1', kind: 'positive', x: 0.45, y: 0.39 },
    { id: 'negative-1', kind: 'negative', x: 0.13, y: 0.79 },
  ],
  imageUrl: '/studio/fixture-product.svg',
  confidence: 0.96,
  abstentionReason: '',
  candidates: fixtureCandidates,
}

export const fixtureGeneration = {
  id: 'generation-fixture-01',
  status: 'done',
  stage: 'qa',
  message: 'Fixture review set. No model call was made.',
  error: null,
  startedAt: '2026-08-22T00:00:00.000Z',
}

export const fixtureStudio = {
  lock: fixtureLock,
  providerProfiles: fixtureProviders,
  variants: fixtureVariants,
  generation: fixtureGeneration,
  direction: {
    mode: 'automatic',
    category: 'skincare',
    audience: 'Premium skincare shoppers',
    placement: 'Square social post',
    visualGuardrail: 'Keep the pack crisp, upright, and fully visible.',
    prompt: 'A quiet pale-aqua bathroom counter, soft morning daylight, condensation in the distance, clean left-side negative space for copy.',
    seed: 2808,
    variantCount: 3,
    providerId: fixtureProviders[0].id,
    costCeiling: 3.5,
  },
}
