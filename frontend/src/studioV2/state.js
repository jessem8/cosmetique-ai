export const STUDIO_STEPS = [
  { id: 'product', label: 'Product', shortLabel: 'Source' },
  { id: 'lock', label: 'Lock', shortLabel: 'Product lock' },
  { id: 'direction', label: 'Direction', shortLabel: 'Creative brief' },
  { id: 'generate', label: 'Generate', shortLabel: 'Render' },
  { id: 'compare', label: 'Compare', shortLabel: 'Review' },
  { id: 'export', label: 'Export', shortLabel: 'Delivery' },
]

export const LOCK_STATUSES = ['needs_review', 'validated', 'rejected']

export const GENERATION_STATUSES = [
  'idle',
  'queued',
  'preparing',
  'waiting_provider',
  'rendering',
  'qa',
  'done',
  'canceled',
  'failed',
  'uncertain',
]

export const TOOL_MODES = ['select', 'draw', 'positive', 'negative', 'pan']

export const initialStudioState = (fixture = {}) => ({
  step: 'product',
  source: {
    productId: null,
    name: '',
    brand: '',
    category: 'skincare',
    imageUrl: '',
    fileName: '',
    sourceSha256: null,
    width: null,
    height: null,
    exifOrientation: 1,
    ...fixture.source,
  },
  lock: {
    id: null,
    status: 'needs_review',
    revision: 0,
    candidateId: null,
    bbox: { x: 0.28, y: 0.18, width: 0.44, height: 0.64 },
    points: [],
    imageUrl: '',
    confidence: null,
    maskUrl: '',
    cutoutUrl: '',
    sourceImageUrl: '',
    candidates: [],
    modelProvenance: {},
    metrics: {},
    abstentionReason: '',
    ...fixture.lock,
  },
  direction: {
    mode: 'automatic',
    category: 'skincare',
    audience: '',
    placement: 'Square social post',
    visualGuardrail: 'Keep the pack crisp, upright, and fully visible.',
    prompt: '',
    seed: 2808,
    variantCount: 1,
    providerId: '',
    costCeiling: 2.0,
    ...fixture.direction,
  },
  providerProfiles: fixture.providerProfiles || [],
  generation: {
    id: null,
    status: 'idle',
    stage: null,
    message: '',
    error: null,
    startedAt: null,
    ...fixture.generation,
  },
  variants: fixture.variants || [],
  selectedVariantId: fixture.variants?.[0]?.id || null,
  compareMode: 'result',
  exportPlatform: 'instagram',
  tool: 'select',
  zoom: 1,
  pan: { x: 0, y: 0 },
  maskOverlay: true,
  notice: null,
  error: null,
})

const clamp = (value, min, max) => Math.min(max, Math.max(min, value))

const nextStep = (step, offset = 1) => {
  const current = STUDIO_STEPS.findIndex((item) => item.id === step)
  return STUDIO_STEPS[clamp(current + offset, 0, STUDIO_STEPS.length - 1)].id
}

export const studioReducer = (state, action) => {
  switch (action.type) {
    case 'SET_STEP':
      return {
        ...state,
        step: STUDIO_STEPS.some((item) => item.id === action.step)
          ? action.step
          : state.step,
        notice: null,
      }
    case 'SET_SOURCE':
      return {
        ...state,
        source: { ...state.source, ...action.source },
        lock: {
          ...state.lock,
          imageUrl: action.source.imageUrl || state.lock.imageUrl,
          sourceImageUrl: action.source.imageUrl || state.lock.sourceImageUrl,
        },
        notice: null,
      }
    case 'ADVANCE':
      return { ...state, step: nextStep(state.step), notice: null }
    case 'BACK':
      return { ...state, step: nextStep(state.step, -1), notice: null }
    case 'SET_TOOL':
      return { ...state, tool: TOOL_MODES.includes(action.tool) ? action.tool : state.tool }
    case 'SET_LOCK_BOX':
      return {
        ...state,
        lock: {
          ...state.lock,
          bbox: {
            x: clamp(action.bbox.x, 0, 1),
            y: clamp(action.bbox.y, 0, 1),
            width: clamp(action.bbox.width, 0.02, 1),
            height: clamp(action.bbox.height, 0.02, 1),
          },
          status: 'needs_review',
        },
        notice: null,
      }
    case 'ADD_POINT':
      return {
        ...state,
        lock: {
          ...state.lock,
          points: [
            ...state.lock.points,
            {
              id: action.point.id || `${action.kind}-${Date.now()}`,
              kind: action.kind,
              x: clamp(action.point.x, 0, 1),
              y: clamp(action.point.y, 0, 1),
            },
          ],
          status: 'needs_review',
        },
        notice: null,
      }
    case 'UNDO_POINT': {
      const points = state.lock.points.slice()
      points.pop()
      return { ...state, lock: { ...state.lock, points }, notice: null }
    }
    case 'RESET_LOCK':
      return {
        ...state,
        lock: {
          ...state.lock,
          bbox: { x: 0.28, y: 0.18, width: 0.44, height: 0.64 },
          points: [],
          status: 'needs_review',
          abstentionReason: '',
        },
        zoom: 1,
        pan: { x: 0, y: 0 },
        maskOverlay: true,
        tool: 'select',
        notice: 'Lock reset to the source candidate.',
      }
    case 'SET_ZOOM':
      return { ...state, zoom: clamp(action.zoom, 0.5, 3.5) }
    case 'SET_PAN':
      return {
        ...state,
        pan: { x: clamp(action.pan.x, -30, 30), y: clamp(action.pan.y, -30, 30) },
      }
    case 'TOGGLE_MASK':
      return { ...state, maskOverlay: !state.maskOverlay }
    case 'SET_CANDIDATE':
      return {
        ...state,
        lock: {
          ...state.lock,
          candidateId: action.candidateId,
          bbox: action.bbox || state.lock.bbox,
          status: 'needs_review',
        },
        notice: 'Candidate selected. Review the edge before validating.',
      }
    case 'SET_LOCK_STATUS':
      return {
        ...state,
        lock: {
          ...state.lock,
          status: LOCK_STATUSES.includes(action.status) ? action.status : state.lock.status,
          abstentionReason: action.status === 'validated' ? '' : state.lock.abstentionReason,
        },
        notice: action.notice || null,
      }
    case 'SET_ABSTENTION_REASON':
      return {
        ...state,
        lock: { ...state.lock, abstentionReason: action.reason },
      }
    case 'SET_LOCK_ID':
      return {
        ...state,
        lock: { ...state.lock, id: action.id, revision: action.revision ?? state.lock.revision },
      }
    case 'SET_LOCK':
      return {
        ...state,
        lock: { ...state.lock, ...action.lock },
        notice: action.notice || null,
      }
    case 'SET_DIRECTION':
      return {
        ...state,
        direction: { ...state.direction, ...action.patch },
        notice: null,
      }
    case 'SET_PROVIDERS':
      return {
        ...state,
        providerProfiles: action.providers,
        direction: {
          ...state.direction,
          providerId: action.providers.some((provider) => provider.id === state.direction.providerId)
            ? state.direction.providerId
            : action.providers[0]?.id || '',
          costCeiling: action.providers.find((provider) => provider.id === state.direction.providerId)?.costCeiling
            ?? action.providers[0]?.costCeiling
            ?? state.direction.costCeiling,
          variantCount: Math.min(
            state.direction.variantCount,
            action.providers.find((provider) => provider.id === state.direction.providerId)?.maxVariants
              ?? action.providers[0]?.maxVariants
              ?? state.direction.variantCount,
          ),
        },
      }
    case 'SET_GENERATION':
      return {
        ...state,
        generation: { ...state.generation, ...action.patch },
        error: action.patch.error === undefined ? state.error : action.patch.error,
      }
    case 'SET_VARIANTS':
      return {
        ...state,
        variants: action.variants,
        selectedVariantId: state.selectedVariantId || action.variants[0]?.id || null,
      }
    case 'SELECT_VARIANT':
      return { ...state, selectedVariantId: action.variantId }
    case 'SET_COMPARE_MODE':
      return { ...state, compareMode: action.mode }
    case 'SET_EXPORT_PLATFORM':
      return { ...state, exportPlatform: action.platform }
    case 'SET_NOTICE':
      return { ...state, notice: action.notice }
    case 'SET_ERROR':
      return { ...state, error: action.error }
    default:
      return state
  }
}

export const stepIndex = (step) => Math.max(0, STUDIO_STEPS.findIndex((item) => item.id === step))

export const isLockReady = (lock) =>
  lock.status === 'validated' && Boolean(lock.id && lock.maskUrl && lock.cutoutUrl)

export const isGenerationReady = (state) =>
  isLockReady(state.lock) && Boolean(state.direction.providerId) && state.direction.prompt.trim().length > 0

export const statusLabel = (status) =>
  ({
    idle: 'Ready to render',
    queued: 'Queued',
    preparing: 'Preparing source',
    waiting_provider: 'Waiting for provider',
    rendering: 'Rendering variants',
    qa: 'Running QA',
    done: 'Ready for review',
    canceled: 'Canceled',
    failed: 'Generation failed',
    uncertain: 'Completion uncertain',
  })[status] || status
