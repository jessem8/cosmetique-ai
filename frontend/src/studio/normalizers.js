export const responseData = (response) => response?.data ?? response

export const responseItems = (response, keys = []) => {
  const value = responseData(response)
  if (Array.isArray(value)) return value
  for (const key of keys) {
    if (Array.isArray(value?.[key])) return value[key]
  }
  return Array.isArray(value?.data) ? value.data : []
}

export const normalizeLock = (response, current = {}) => {
  const raw = responseData(response)?.lock || responseData(response)?.product_lock || responseData(response)
  if (!raw || typeof raw !== 'object') return current
  const target = raw.bbox || raw.target_box || raw.target_geometry || current.bbox
  return {
    ...current,
    ...raw,
    id: raw.id || raw.lock_id || current.id || null,
    revision: raw.revision ?? raw.revision_number ?? current.revision ?? 0,
    status: raw.status === 'processing' ? 'needs_review' : (raw.status || current.status || 'needs_review'),
    candidateId: raw.candidateId || raw.candidate_id || current.candidateId || null,
    bbox: target,
    points: raw.points || current.points || [],
    imageUrl: raw.imageUrl || raw.image_url || raw.source_image_url || current.imageUrl || '',
    sourceImageUrl: raw.sourceImageUrl || raw.source_image_url || current.sourceImageUrl || '',
    maskUrl: raw.maskUrl || raw.mask_url || current.maskUrl || '',
    cutoutUrl: raw.cutoutUrl || raw.cutout_url || current.cutoutUrl || '',
    candidates: raw.candidates || current.candidates || [],
    metrics: raw.metrics || current.metrics || {},
    modelProvenance: raw.modelProvenance || raw.model_provenance || current.modelProvenance || {},
    confidence: raw.confidence ?? raw.metrics?.score ?? current.confidence ?? null,
  }
}

export const normalizeProvider = (provider) => {
  const selection = provider.selection || provider.provider_selection || {}
  const capabilities = provider.capabilities || {}
  const flags = Array.isArray(provider.capability_flags)
    ? provider.capability_flags
    : Object.entries(capabilities)
      .filter(([key, value]) => value === true && key.startsWith('supports_'))
      .map(([key]) => key.replace(/^supports_/, '').replaceAll('_', '-'))
  return {
    ...provider,
    id: provider.id || [selection.provider, selection.profile, selection.model].filter(Boolean).join(':'),
    name: provider.name || selection.profile || 'Profil de rendu',
    model: provider.model || selection.model || 'Modèle non communiqué',
    selection,
    capabilities: flags,
    maxVariants: provider.maxVariants || capabilities.max_variants || 1,
    costPerVariant: provider.costPerVariant ?? ((capabilities.estimated_cost_per_variant_micros || 0) / 1000000),
    costCeiling: provider.costCeiling ?? ((capabilities.max_budget_micros || 0) / 1000000),
  }
}

export const normalizeGeneration = (response, current = {}) => {
  const raw = responseData(response)?.generation || responseData(response)
  if (!raw || typeof raw !== 'object') return current
  const sourceStatus = raw.status || raw.lifecycle_status || current.status || 'idle'
  const status = raw.unknown_remote_completion || raw.error?.code === 'PROVIDER_COMPLETION_UNKNOWN'
    ? 'uncertain'
    : ({ accepted: 'queued', waiting_for_provider: 'waiting_provider', processing_lock: 'preparing', planning_scene: 'preparing', generating: 'rendering', compositing: 'rendering', baseline_ready: 'rendering', enhancing: 'rendering', quality_review: 'qa', needs_review: 'qa', ready: 'done', accepted_final: 'done', cancelled: 'canceled' }[sourceStatus] || sourceStatus)
  return {
    ...current,
    ...raw,
    id: raw.id || raw.generation_id || current.id || null,
    status,
    stage: raw.stage || raw.current_stage || raw.lifecycle_status || current.stage || null,
    message: raw.message || raw.status_message || current.message || '',
    error: raw.error || null,
    backgroundPrompt: raw.backgroundPrompt || raw.background_prompt || raw.scene_prompt || current.backgroundPrompt || '',
    baseline: raw.baseline || raw.baseline_artifact || current.baseline || null,
    enhancement: raw.enhancement || raw.enhancement_artifact || current.enhancement || null,
    final_candidate: raw.final_candidate || raw.finalCandidate || raw.final_artifact || current.final_candidate || null,
    qa: raw.qa || raw.quality_assurance || current.qa || null,
    manifest: raw.manifest || current.manifest || null,
  }
}

export const normalizeVariants = (response) => responseItems(response, ['variants', 'items']).map((variant, index) => {
  const provenance = variant.provenance || {}
  const qa = variant.qa || {}
  const manifest = variant.manifest || {}
  const metadata = manifest.metadata || {}
  return {
    ...variant,
    id: variant.id || variant.variant_id || `variant-${index + 1}`,
    label: variant.label || variant.name || `Variante ${index + 1}`,
    imageUrl: variant.imageUrl || variant.image_url || variant.url || manifest.image_url || '',
    qa: { status: qa.status || (qa.passed ? 'pass' : 'review'), failures: qa.failures || qa.findings || [], ...qa },
    provenance: {
      provider: provenance.provider || variant.provider || null,
      model: provenance.model || variant.model || null,
      requestId: provenance.requestId || provenance.request_id || variant.request_id || null,
      cost: provenance.cost ?? provenance.cost_usd ?? variant.cost ?? null,
      sourceSha256: provenance.sourceSha256 || provenance.source_sha256 || metadata.source_sha256 || null,
      outputSha256: provenance.outputSha256 || provenance.output_sha256 || metadata.output_sha256 || manifest.image_sha256 || variant.checksum || null,
      ...provenance,
    },
  }
}).filter((variant) => Boolean(variant.imageUrl || variant.provenance.outputSha256))

export const isActiveGeneration = (status) => ['queued', 'preparing', 'waiting_provider', 'rendering', 'qa'].includes(status)

export const formatCost = (value) => value === null || value === undefined ? 'Non communiqué' : `${Number(value || 0).toFixed(2)} $`

export const shortHash = (value) => value ? `${value.slice(0, 8)}...${value.slice(-6)}` : 'Non disponible'
