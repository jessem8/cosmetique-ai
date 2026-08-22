import { useEffect, useMemo, useReducer, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  ArrowCounterClockwise, Check, CheckCircle, Crosshair, DownloadSimple,
  Eye, EyeSlash, FrameCorners, Hand, ImageSquare, Info, MagicWand,
  Minus, Plus, Rectangle, Scissors, ShieldCheck, SpinnerGap, Stop,
  UploadSimple, Warning, X,
} from '@phosphor-icons/react'
import { ApiError, products, studioV2 } from '../api/client.js'
import { createIdempotencyKey } from '../utils/idempotency.js'
import {
  STUDIO_STEPS, initialStudioState, isGenerationReady, isLockReady,
  statusLabel, stepIndex, studioReducer,
} from '../studioV2/state.js'

const dataOf = (response) => response?.data ?? response
const itemsOf = (response, keys = []) => {
  const value = dataOf(response)
  if (Array.isArray(value)) return value
  for (const key of keys) if (Array.isArray(value?.[key])) return value[key]
  return Array.isArray(value?.data) ? value.data : []
}
const lifeStatus = (status) => ({
  accepted: 'queued', queued: 'queued', waiting_for_provider: 'waiting_provider',
  processing_lock: 'preparing', planning_scene: 'preparing', generating: 'rendering',
  compositing: 'rendering', quality_review: 'qa', needs_review: 'qa',
  ready: 'done', failed: 'failed', cancelled: 'canceled',
}[status] || status)
const activeGeneration = (status) => ['queued', 'preparing', 'waiting_provider', 'rendering', 'qa'].includes(status)
const money = (value) => '$' + Number(value || 0).toFixed(2)

const errorMessage = (error) => {
  if (error?.code === 'PROVIDER_COMPLETION_UNKNOWN') return 'The provider response was not confirmed. Retry and export stay unavailable until reconciliation.'
  if (error?.code === 'PROVIDER_PREFLIGHT_FAILED') return 'The allowlisted provider did not pass preflight. No image was generated.'
  if (error?.code === 'QA_NEEDS_REVIEW') return 'The generated image failed a preservation check and is held for review.'
  if (error?.status === 429) return 'The provider is rate limited. Wait before retrying.'
  if (error?.status === 408) return 'The provider timed out. The completion state is unknown.'
  if (error?.status >= 500) return 'The provider is unavailable. Your lock and brief are preserved.'
  return error?.message || 'The studio could not complete this request.'
}

const lockFrom = (response, current) => {
  const raw = dataOf(response)?.lock || dataOf(response)?.product_lock || dataOf(response)
  if (!raw || typeof raw !== 'object') return current
  return {
    ...current, ...raw,
    id: raw.id || raw.lock_id || current.id,
    revision: raw.revision ?? raw.revision_number ?? current.revision,
    candidateId: raw.candidateId || raw.candidate_id || current.candidateId,
    bbox: raw.bbox || raw.target_box || raw.target_geometry || current.bbox,
    points: raw.points || current.points,
    imageUrl: raw.imageUrl || raw.image_url || raw.source_image_url || current.imageUrl,
    sourceImageUrl: raw.sourceImageUrl || raw.source_image_url || current.sourceImageUrl,
    maskUrl: raw.maskUrl || raw.mask_url || current.maskUrl,
    cutoutUrl: raw.cutoutUrl || raw.cutout_url || current.cutoutUrl,
    candidates: raw.candidates || current.candidates,
    metrics: raw.metrics || current.metrics,
    modelProvenance: raw.modelProvenance || raw.model_provenance || current.modelProvenance,
    confidence: raw.confidence ?? raw.metrics?.score ?? current.confidence,
    status: raw.status === 'processing' ? 'needs_review' : (raw.status || current.status),
  }
}

const generationFrom = (response, current) => {
  const raw = dataOf(response)?.generation || dataOf(response)
  if (!raw || typeof raw !== 'object') return current
  const unknown = Boolean(raw.unknown_remote_completion || raw.error?.code === 'PROVIDER_COMPLETION_UNKNOWN')
  return {
    ...current, ...raw,
    id: raw.id || raw.generation_id || current.id,
    status: unknown ? 'uncertain' : lifeStatus(raw.status || raw.lifecycle_status || current.status),
    stage: raw.stage || raw.current_stage || raw.lifecycle_status || current.stage,
    message: raw.message || raw.status_message || current.message,
    error: raw.error || null,
  }
}

const providersFrom = (response) => itemsOf(response, ['providers', 'profiles']).map((provider) => {
  const selection = provider.selection || provider.provider_selection || {}
  const capabilities = provider.capabilities || {}
  const flags = Array.isArray(provider.capability_flags)
    ? provider.capability_flags
    : Object.entries(capabilities).filter((entry) => entry[1] === true && entry[0].startsWith('supports_')).map((entry) => entry[0].replace(/^supports_/, '').replaceAll('_', '-'))
  return {
    ...provider,
    id: provider.id || [selection.provider, selection.profile, selection.model].filter(Boolean).join(':'),
    name: provider.name || selection.profile || 'Allowlisted profile',
    model: provider.model || selection.model || 'Model not disclosed',
    selection,
    capabilities: flags,
    maxVariants: provider.maxVariants || capabilities.max_variants || 1,
    costPerVariant: provider.costPerVariant ?? ((capabilities.estimated_cost_per_variant_micros || 0) / 1000000),
    costCeiling: provider.costCeiling ?? ((capabilities.max_budget_micros || 0) / 1000000),
  }
})

const variantsFrom = (response) => itemsOf(response, ['variants', 'items']).map((variant, index) => {
  const provenance = variant.provenance || {}
  const qa = variant.qa || {}
  const manifest = variant.manifest || {}
  const metadata = manifest.metadata || {}
  return {
    ...variant,
    id: variant.id || variant.variant_id || 'variant-' + (index + 1),
    label: variant.label || variant.name || 'Variant ' + (index + 1),
    imageUrl: variant.imageUrl || variant.image_url || variant.url || manifest.image_url || '',
    qa: { status: qa.status || (qa.passed ? 'pass' : 'review'), failures: qa.failures || qa.findings || [], ...qa },
    provenance: {
      provider: provenance.provider || variant.provider || null,
      model: provenance.model || variant.model || null,
      requestId: provenance.requestId || provenance.request_id || variant.request_id || null,
      cost: provenance.cost ?? provenance.cost_usd ?? variant.cost ?? null,
      sourceSha256: provenance.sourceSha256 || provenance.source_sha256 || metadata.source_sha256 || null,
      outputSha256: provenance.outputSha256 || provenance.output_sha256 || manifest.image_sha256 || variant.checksum || null,
      ...provenance,
    },
  }
}).filter((variant) => Boolean(variant.imageUrl || variant.provenance.outputSha256))

const usePrivateProductImage = (productId) => {
  const [url, setUrl] = useState('')
  useEffect(() => {
    if (!productId) {
      setUrl('')
      return undefined
    }
    const controller = new AbortController()
    let objectUrl = ''
    products.getImage(productId, { signal: controller.signal }).then((response) => {
      if (!controller.signal.aborted) {
        objectUrl = URL.createObjectURL(response.data)
        setUrl(objectUrl)
      }
    }).catch(() => {})
    return () => {
      controller.abort()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [productId])
  return url
}

const usePrivateLockArtifact = (lockId, artifact) => {
  const [url, setUrl] = useState('')
  useEffect(() => {
    if (!lockId || !artifact) {
      setUrl('')
      return undefined
    }
    const controller = new AbortController()
    let objectUrl = ''
    studioV2.productLocks.artifact(lockId, artifact, { signal: controller.signal }).then((response) => {
      if (!controller.signal.aborted) {
        objectUrl = URL.createObjectURL(response.data)
        setUrl(objectUrl)
      }
    }).catch(() => {})
    return () => {
      controller.abort()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [lockId, artifact])
  return url
}

const usePrivateVariantImages = (generationId, variants) => {
  const [urls, setUrls] = useState({})
  const variantKey = variants.map((variant) => variant.id + ':' + (variant.imageUrl || '')).join('|')
  useEffect(() => {
    if (!generationId || variants.length === 0) {
      setUrls({})
      return undefined
    }
    const controller = new AbortController()
    const objectUrls = []
    Promise.all(variants.map(async (variant) => {
      if (!variant.imageUrl) return null
      try {
        const response = await studioV2.generations.variantImage(generationId, variant.id, { signal: controller.signal })
        if (controller.signal.aborted) return null
        const objectUrl = URL.createObjectURL(response.data)
        objectUrls.push(objectUrl)
        return [variant.id, objectUrl]
      } catch {
        return null
      }
    })).then((entries) => {
      if (!controller.signal.aborted) setUrls(Object.fromEntries(entries.filter(Boolean)))
    })
    return () => {
      controller.abort()
      objectUrls.forEach((objectUrl) => URL.revokeObjectURL(objectUrl))
    }
  }, [generationId, variantKey, variants])
  return urls
}

function StageNav({ state, onStep }) {
  const active = stepIndex(state.step)
  const enabled = (id) => {
    if (id === 'product') return true
    if (id === 'lock') return Boolean(state.source.productId)
    if (id === 'direction') return isLockReady(state.lock)
    if (id === 'generate') return isLockReady(state.lock) && Boolean(state.direction.prompt.trim())
    return state.generation.status === 'done' && state.variants.length > 0
  }
  return <nav className="studio-v2__stage-nav" aria-label="Campaign Studio workflow"><ol>{STUDIO_STEPS.map((item, index) => {
    const current = index === active
    const done = index < active
    const canClick = enabled(item.id)
    return <li key={item.id} data-current={current} data-complete={done}><button type="button" aria-current={current ? 'step' : undefined} disabled={!current && !canClick} onClick={() => canClick && onStep(item.id)}><span className="studio-v2__stage-dot" aria-hidden="true">{done ? <Check size={13} weight="bold" /> : index + 1}</span><span><strong>{item.id === 'direction' ? 'Brief' : item.label}</strong><small>{item.shortLabel}</small></span></button></li>
  })}</ol></nav>
}

function SourceStage({ state, dispatch, file, preview, storedUrl, busy, onFile, onCreate, onRemove }) {
  const source = state.source
  const image = preview || storedUrl || source.imageUrl
  const stored = Boolean(source.productId)
  return <section className="studio-v2__stage" aria-labelledby="source-title">
    <header className="studio-v2__stage-header"><div><p className="studio-v2__kicker">Start here</p><h2 id="source-title">Use the real product photo</h2><p>Store the source privately before the lock is calculated.</p></div><span className={'studio-v2__state-pill ' + (stored ? 'is-good' : '')}>{stored ? 'Source stored' : image ? 'Ready to store' : 'Waiting for upload'}</span></header>
    <div className="studio-v2__source-layout"><div className="studio-v2__source-figure" data-empty={!image}>{image ? <img src={image} alt="Uploaded product source" /> : <div className="studio-v2__source-empty"><UploadSimple size={32} aria-hidden="true" /><strong>Upload a product image</strong><span>JPG, PNG or WebP, up to 10 MB</span></div>}{stored && <span className="studio-v2__source-stamp"><CheckCircle size={14} weight="fill" aria-hidden="true" />Private source</span>}</div>
      <div className="studio-v2__source-form"><label className="field"><span>Product name</span><input value={source.name} onChange={(event) => dispatch({ type: 'SET_SOURCE', source: { name: event.target.value } })} /></label><label className="field"><span>Brand <small>Optional</small></span><input value={source.brand} onChange={(event) => dispatch({ type: 'SET_SOURCE', source: { brand: event.target.value } })} /></label><label className="field"><span>Category</span><select value={source.category} onChange={(event) => dispatch({ type: 'SET_SOURCE', source: { category: event.target.value } })}><option value="skincare">Skincare</option><option value="makeup">Makeup</option><option value="perfume">Perfume</option><option value="haircare">Haircare</option><option value="bodycare">Body care</option></select></label><div className="studio-v2__source-facts"><div><span>File</span><strong>{source.fileName || file?.name || 'No image selected'}</strong></div><div><span>Storage</span><strong>{stored ? 'Private product storage' : 'Not stored yet'}</strong></div></div><div className="studio-v2__source-actions"><label className="button button--secondary" htmlFor="studio-product-image"><UploadSimple size={17} aria-hidden="true" />{image ? 'Change image' : 'Choose source image'}</label><input id="studio-product-image" className="visually-hidden" type="file" accept="image/jpeg,image/png,image/webp" aria-label="Choose source image" onChange={(event) => onFile(event.target.files?.[0])} />{image && !stored && <button type="button" className="button button--quiet" onClick={onRemove}><X size={16} aria-hidden="true" />Remove</button>}<button type="button" className="button button--primary" onClick={onCreate} disabled={busy || (!stored && !file)}><Scissors size={17} aria-hidden="true" />{busy ? 'Creating Product Lock' : stored ? 'Refresh Product Lock' : 'Create Product Lock'}</button></div><p className="studio-v2__helper">This source is segmented only after it has been stored.</p></div>
    </div>
  </section>
}

function LockCanvas({ state, dispatch, sourceUrl, maskUrl }) {
  const ref = useRef(null)
  const drag = useRef(null)
  const pointAt = (event) => {
    const rect = ref.current?.getBoundingClientRect()
    if (!rect || !rect.width || !rect.height) return null
    const zoom = state.zoom || 1
    const pan = state.pan || { x: 0, y: 0 }
    const rawX = (event.clientX - rect.left) / rect.width
    const rawY = (event.clientY - rect.top) / rect.height
    return {
      x: Math.max(0, Math.min(1, ((rawX - 0.5 - pan.x / 100) / zoom) + 0.5)),
      y: Math.max(0, Math.min(1, ((rawY - 0.5 - pan.y / 100) / zoom) + 0.5)),
    }
  }
  const down = (event) => {
    if (state.tool === 'pan') {
      drag.current = {
        kind: 'pan',
        startX: event.clientX,
        startY: event.clientY,
        pan: state.pan || { x: 0, y: 0 },
      }
      ref.current?.setPointerCapture?.(event.pointerId)
      return
    }
    const point = pointAt(event)
    if (!point) return
    if (state.tool === 'select') {
      const candidate = state.lock.candidates?.find((item) => {
        const box = item.box || item.bbox || item.target_box
        return box && point.x >= box.x && point.x <= box.x + box.width && point.y >= box.y && point.y <= box.y + box.height
      }) || state.lock.candidates?.[0]
      if (candidate) dispatch({ type: 'SET_CANDIDATE', candidateId: candidate.id, bbox: candidate.box || candidate.bbox || candidate.target_box })
      return
    }
    if (state.tool === 'positive' || state.tool === 'negative') return dispatch({ type: 'ADD_POINT', kind: state.tool, point })
    if (state.tool === 'draw') {
      drag.current = { kind: 'draw', point }
      dispatch({ type: 'SET_LOCK_BOX', bbox: { x: point.x, y: point.y, width: 0.02, height: 0.02 } })
      ref.current?.setPointerCapture?.(event.pointerId)
    }
  }
  const move = (event) => {
    if (drag.current?.kind === 'pan' && state.tool === 'pan') {
      const rect = ref.current?.getBoundingClientRect()
      if (!rect || !rect.width || !rect.height) return
      const startPan = drag.current.pan || { x: 0, y: 0 }
      dispatch({
        type: 'SET_PAN',
        pan: {
          x: startPan.x + ((event.clientX - drag.current.startX) / rect.width) * 100,
          y: startPan.y + ((event.clientY - drag.current.startY) / rect.height) * 100,
        },
      })
      return
    }
    if (!drag.current || drag.current.kind !== 'draw' || state.tool !== 'draw') return
    const point = pointAt(event)
    if (!point) return
    dispatch({ type: 'SET_LOCK_BOX', bbox: { x: Math.min(drag.current.point.x, point.x), y: Math.min(drag.current.point.y, point.y), width: Math.abs(point.x - drag.current.point.x), height: Math.abs(point.y - drag.current.point.y) } })
  }
  const endDrag = () => { drag.current = null }
  const bbox = state.lock.bbox
  const transform = `translate(${state.pan?.x || 0}%, ${state.pan?.y || 0}%) scale(${state.zoom || 1})`
  return <div className="studio-v2__canvas-wrap"><div ref={ref} className="studio-v2__lock-canvas" data-tool={state.tool} onPointerDown={down} onPointerMove={move} onPointerUp={endDrag} onPointerCancel={endDrag} role="application" aria-label="Product Lock canvas. Select a candidate, draw a boundary, place correction points, or pan the zoomed source." tabIndex="0"><div className="studio-v2__canvas-stage" style={{ transform }}>{sourceUrl ? <img className="studio-v2__lock-image" src={sourceUrl} alt="Product source on the lock canvas" /> : <div className="studio-v2__canvas-missing">The stored source is not available.</div>}{maskUrl && <img className="studio-v2__mask-image" src={maskUrl} alt="" aria-hidden="true" />}<div className="studio-v2__bbox" style={{ left: bbox.x * 100 + '%', top: bbox.y * 100 + '%', width: bbox.width * 100 + '%', height: bbox.height * 100 + '%' }}><span>Product boundary</span></div>{state.lock.points.map((point) => <span className={'studio-v2__point studio-v2__point--' + point.kind} key={point.id} style={{ left: point.x * 100 + '%', top: point.y * 100 + '%' }} aria-label={(point.kind === 'negative' ? 'Negative' : 'Positive') + ' correction point'}>{point.kind === 'positive' ? '+' : '−'}</span>)}<span className="studio-v2__canvas-note">{state.maskOverlay && maskUrl ? 'Mask visible' : 'Source view'}</span></div></div><div className="studio-v2__canvas-controls" aria-label="Canvas controls"><button type="button" className="icon-button" aria-label="Zoom out" title="Zoom out" onClick={() => dispatch({ type: 'SET_ZOOM', zoom: state.zoom - 0.1 })}><Minus size={17} aria-hidden="true" /></button><button type="button" className="icon-button" aria-label="Reset zoom and pan" title="Reset view" onClick={() => { dispatch({ type: 'SET_ZOOM', zoom: 1 }); dispatch({ type: 'SET_PAN', pan: { x: 0, y: 0 } }) }}><FrameCorners size={17} aria-hidden="true" /></button><button type="button" className="icon-button" aria-label="Zoom in" title="Zoom in" onClick={() => dispatch({ type: 'SET_ZOOM', zoom: state.zoom + 0.1 })}><Plus size={17} aria-hidden="true" /></button><button type="button" className="button button--quiet button--small" onClick={() => dispatch({ type: 'TOGGLE_MASK' })}>{state.maskOverlay ? <EyeSlash size={16} aria-hidden="true" /> : <Eye size={16} aria-hidden="true" />}{state.maskOverlay ? 'Hide mask' : 'Show mask'}</button><span className="studio-v2__canvas-help">{Math.round((state.zoom || 1) * 100)}% view · Choose Pan to drag</span></div></div>
}

function LockStage({ state, dispatch, onRefine, onValidate, onReject, sourceUrl, maskUrl, cutoutUrl }) {
  const negative = state.lock.points.filter((point) => point.kind === 'negative').length
  const positive = state.lock.points.filter((point) => point.kind === 'positive').length
  const provenance = state.lock.modelProvenance || {}
  return <section className="studio-v2__stage" aria-labelledby="lock-title"><header className="studio-v2__stage-header"><div><p className="studio-v2__kicker">Product Lock</p><h2 id="lock-title">Review what stays in the image</h2><p>Use negative points to remove hands and nearby objects before validation.</p></div><span className={'studio-v2__state-pill studio-v2__state-pill--' + state.lock.status}>{state.lock.status === 'validated' ? 'Validated' : 'Review required'}</span></header><div className="studio-v2__lock-layout"><div className="studio-v2__canvas-panel"><div className="studio-v2__canvas-heading"><div><strong>Source and mask</strong><span>Tap the image to add the selected correction.</span></div><div className="studio-v2__legend"><span><i className="studio-v2__legend-dot" />Keep</span><span><i className="studio-v2__legend-dot studio-v2__legend-dot--negative" />Exclude</span></div></div><LockCanvas state={state} dispatch={dispatch} sourceUrl={sourceUrl} maskUrl={maskUrl} /></div><aside className="studio-v2__lock-rail" aria-label="Product Lock controls"><div className="studio-v2__rail-block"><div className="studio-v2__rail-label">Correction tool</div><div className="studio-v2__tool-grid" role="toolbar" aria-label="Product Lock tools"><button type="button" data-active={state.tool === 'select'} onClick={() => dispatch({ type: 'SET_TOOL', tool: 'select' })}><Crosshair size={17} aria-hidden="true" />Select</button><button type="button" data-active={state.tool === 'draw'} onClick={() => dispatch({ type: 'SET_TOOL', tool: 'draw' })}><Rectangle size={17} aria-hidden="true" />Draw box</button><button type="button" data-active={state.tool === 'positive'} onClick={() => dispatch({ type: 'SET_TOOL', tool: 'positive' })}><Plus size={17} aria-hidden="true" />Keep area</button><button type="button" data-active={state.tool === 'negative'} onClick={() => dispatch({ type: 'SET_TOOL', tool: 'negative' })}><Minus size={17} aria-hidden="true" />Exclude area</button><button type="button" data-active={state.tool === 'pan'} onClick={() => dispatch({ type: 'SET_TOOL', tool: 'pan' })}><Hand size={17} aria-hidden="true" />Pan</button></div></div><div className="studio-v2__rail-block"><div className="studio-v2__rail-label">Corrections recorded</div><div className="studio-v2__correction-counts"><strong>{negative}</strong><span>negative</span><strong>{positive}</strong><span>positive</span></div><p className="studio-v2__helper">Negative points are the hand-contamination control for this source.</p></div>{state.lock.candidates?.length > 0 && <div className="studio-v2__rail-block"><div className="studio-v2__rail-label">Candidates</div><div className="studio-v2__candidate-list">{state.lock.candidates.map((candidate) => <button type="button" key={candidate.id} data-selected={candidate.id === state.lock.candidateId} onClick={() => dispatch({ type: 'SET_CANDIDATE', candidateId: candidate.id, bbox: candidate.box })}><span>{candidate.label || candidate.id}</span><small>{candidate.confidence == null ? 'Review' : Math.round(candidate.confidence * 100) + '% measured'}</small></button>)}</div></div>}<div className="studio-v2__rail-block studio-v2__evidence-block"><div className="studio-v2__rail-label">Model record</div><dl><div><dt>Runtime</dt><dd>{provenance.segmenter_provider || 'Not recorded'}</dd></div><div><dt>Model</dt><dd>{provenance.segmenter_model || 'Not recorded'}</dd></div><div><dt>Revision</dt><dd>{state.lock.revision || 'New'}</dd></div></dl></div>{cutoutUrl && <div className="studio-v2__cutout"><div className="studio-v2__rail-label">Stored cutout</div><img src={cutoutUrl} alt="Stored transparent product cutout" /></div>}<div className="studio-v2__rail-actions"><button type="button" className="button button--secondary" onClick={onRefine} disabled={!state.lock.id}><Scissors size={16} aria-hidden="true" />Save corrections</button><button type="button" className="button button--primary" onClick={onValidate} disabled={!state.lock.id}><Check size={16} aria-hidden="true" />Validate Product Lock</button></div><div className="studio-v2__review-field"><label className="field"><span>Why should this lock stay under review?</span><select value={state.lock.abstentionReason} onChange={(event) => dispatch({ type: 'SET_ABSTENTION_REASON', reason: event.target.value })}><option value="">Select a reason</option><option value="hand contamination">Hand contamination</option><option value="ambiguous boundary">Ambiguous boundary</option><option value="multiple products">Multiple products</option></select></label><button type="button" className="button button--quiet button--wide" onClick={onReject}><Warning size={15} aria-hidden="true" />Keep lock under review</button></div></aside></div></section>
}

function Field({ label, hint, children }) {
  return <label className="field"><span>{label} {hint && <small>{hint}</small>}</span>{children}</label>
}

function BriefStage({ state, dispatch, provider }) {
  const unsupported = provider && !provider.capabilities.includes('mask-preservation')
  return <section className="studio-v2__stage" aria-labelledby="direction-title"><header className="studio-v2__stage-header"><div><p className="studio-v2__kicker">Creative brief</p><h2 id="direction-title">Describe the scene</h2><p>The product stays protected. This brief controls the world around it.</p></div><span className={'studio-v2__state-pill ' + (provider ? 'is-good' : '')}>{provider ? 'Provider ready' : 'Provider unavailable'}</span></header><div className="studio-v2__brief-layout"><div className="studio-v2__brief-main"><Field label="Scene direction" hint="Required"><textarea aria-label="Scene direction" placeholder="Describe light, surface, atmosphere, and copy-safe space." value={state.direction.prompt} maxLength={800} onChange={(event) => dispatch({ type: 'SET_DIRECTION', patch: { prompt: event.target.value } })} /></Field><div className="studio-v2__brief-fields"><Field label="Product category"><select value={state.direction.category} onChange={(event) => dispatch({ type: 'SET_DIRECTION', patch: { category: event.target.value } })}><option value="skincare">Skincare</option><option value="perfume">Perfume</option><option value="makeup">Makeup</option><option value="haircare">Haircare</option></select></Field><Field label="Audience"><input value={state.direction.audience} onChange={(event) => dispatch({ type: 'SET_DIRECTION', patch: { audience: event.target.value } })} /></Field><Field label="Placement"><select value={state.direction.placement} onChange={(event) => dispatch({ type: 'SET_DIRECTION', patch: { placement: event.target.value } })}><option>Square social post</option><option>Wide editorial</option><option>Story portrait</option></select></Field><Field label="Product guardrail"><input value={state.direction.visualGuardrail} onChange={(event) => dispatch({ type: 'SET_DIRECTION', patch: { visualGuardrail: event.target.value } })} /></Field></div></div><aside className="studio-v2__brief-rail" aria-label="Generation settings"><div className="studio-v2__rail-block"><div className="studio-v2__rail-label">Allowlisted provider</div>{provider ? <><strong className="studio-v2__provider-name">{provider.name}</strong><code>{provider.model}</code><p className="studio-v2__helper">Server-owned product-preserving profile.</p></> : <p className="studio-v2__helper">The server has not returned a usable generation profile.</p>}</div><div className="studio-v2__rail-block"><div className="studio-v2__rail-label">Bounded request</div><label className="field"><span>Variants</span><input type="number" min="1" max={provider?.maxVariants || 1} value={state.direction.variantCount} aria-label="Variant count" onChange={(event) => dispatch({ type: 'SET_DIRECTION', patch: { variantCount: Number(event.target.value) } })} /></label><label className="field"><span>Seed</span><input type="number" value={state.direction.seed} onChange={(event) => dispatch({ type: 'SET_DIRECTION', patch: { seed: Number(event.target.value) } })} /></label><div className="studio-v2__request-note"><ShieldCheck size={16} aria-hidden="true" /><span>One provider request. The server enforces the acceptance cap.</span></div></div>{unsupported && <div className="studio-v2__inline-warning"><Warning size={16} aria-hidden="true" /><span>This profile cannot preserve the Product Lock.</span></div>}<button type="button" className="button button--primary button--wide" disabled={!isGenerationReady(state) || unsupported} onClick={() => dispatch({ type: 'SET_STEP', step: 'generate' })}><MagicWand size={17} aria-hidden="true" />Review generation</button></aside></div></section>
}

function GenerationStage({ state, dispatch, onGenerate, onCancel, onRetry }) {
  const active = activeGeneration(state.generation.status)
  const error = state.generation.error || state.error
  const retryable = state.generation.status === 'failed' && error?.code !== 'PROVIDER_COMPLETION_UNKNOWN' && error?.retryable !== false
  const stages = [['preparing', 'Prepare the locked product'], ['waiting_provider', 'Submit to the provider'], ['rendering', 'Render the scene'], ['qa', 'Run preservation QA']]
  return <section className="studio-v2__stage" aria-labelledby="generate-title"><header className="studio-v2__stage-header"><div><p className="studio-v2__kicker">Generation</p><h2 id="generate-title">Run one bounded image request</h2><p>Your source, lock revision, and brief are captured before submission. The server enforces the one-request safety cap.</p></div><span className={'studio-v2__state-pill studio-v2__state-pill--' + state.generation.status}>{statusLabel(state.generation.status)}</span></header><div className="studio-v2__generation-layout"><div className="studio-v2__generation-brief"><div className="studio-v2__brief-card"><span>Brief snapshot</span><strong>{state.direction.category} · {state.direction.placement}</strong><p>{state.direction.prompt || 'No scene direction has been entered.'}</p></div><div className="studio-v2__generation-actions">{!active && state.generation.status !== 'uncertain' && state.generation.status !== 'done' && <button type="button" className="button button--primary button--large" onClick={onGenerate}><MagicWand size={18} aria-hidden="true" />Generate image</button>}{state.generation.status === 'done' && <button type="button" className="button button--primary button--large" onClick={() => dispatch({ type: 'SET_STEP', step: 'compare' })}><Eye size={18} aria-hidden="true" />Review result</button>}{active && <button type="button" className="button button--secondary" onClick={onCancel}><Stop size={17} aria-hidden="true" />Cancel request</button>}{retryable && <button type="button" className="button button--secondary" onClick={onRetry}><ArrowCounterClockwise size={17} aria-hidden="true" />Retry known failure</button>}</div>{error && <div className="studio-v2__error-box" role="alert"><Warning size={19} aria-hidden="true" /><div><strong>{error.code || 'Generation error'}</strong><p>{errorMessage(error)}</p></div></div>}{state.generation.status === 'uncertain' && <div className="studio-v2__uncertain-box" role="alert"><Info size={18} aria-hidden="true" /><div><strong>Completion uncertain</strong><p>The remote receipt is not confirmed. Retry and export are unavailable to prevent a duplicate charge.</p></div></div>}</div><div className="studio-v2__stage-list" aria-live="polite">{stages.map(([stage, label], index) => { const currentIndex = stages.findIndex((item) => item[0] === state.generation.stage); const complete = state.generation.status === 'done' || (currentIndex >= 0 && index < currentIndex); const current = state.generation.stage === stage; return <div className="studio-v2__stage-row" data-current={current} data-complete={complete} key={stage}><span>{complete ? <Check size={14} weight="bold" /> : current ? <SpinnerGap size={15} className="studio-v2__spin" aria-hidden="true" /> : index + 1}</span><div><strong>{label}</strong><small>{current ? state.generation.message || 'Working now' : complete ? 'Complete' : 'Waiting'}</small></div></div> })}</div></div></section>
}

function ResultStage({ state, dispatch, sourceUrl, variantUrl }) {
  const selected = state.variants.find((item) => item.id === state.selectedVariantId) || state.variants[0]
  const provenance = selected?.provenance || {}
  if (!selected) return <section className="studio-v2__stage" aria-labelledby="compare-title"><header className="studio-v2__stage-header"><div><p className="studio-v2__kicker">Review</p><h2 id="compare-title">No genuine result yet</h2><p>Return here only after the provider has returned an image.</p></div></header><div className="studio-v2__empty-result"><ImageSquare size={30} aria-hidden="true" /><strong>Generated image unavailable</strong></div></section>
  return <section className="studio-v2__stage" aria-labelledby="compare-title"><header className="studio-v2__stage-header"><div><p className="studio-v2__kicker">Review</p><h2 id="compare-title">Compare the real result</h2><p>Only provider-returned variants appear here. Missing artifacts stay unavailable.</p></div><span className={'studio-v2__state-pill ' + (selected.qa?.status === 'pass' ? 'is-good' : '')}>{selected.qa?.status === 'pass' ? 'QA passed' : 'Review required'}</span></header><div className="studio-v2__compare-layout"><div className="studio-v2__result-viewer"><div className="studio-v2__viewer-label"><span>{state.compareMode === 'source' ? 'Locked source' : selected.label}</span><span>{selected.qa?.status || 'QA not recorded'}</span></div>{state.compareMode === 'source' ? <img src={sourceUrl || state.lock.imageUrl} alt="Locked product source for comparison" /> : variantUrl ? <img src={variantUrl} alt={selected.label + ', generated campaign variant'} /> : <div className="studio-v2__variant-empty"><ImageSquare size={32} aria-hidden="true" /><span>Generated image is not available</span></div>}</div><aside className="studio-v2__result-rail" aria-label="Result evidence"><div className="studio-v2__segmented" role="tablist" aria-label="Comparison view"><button type="button" role="tab" aria-selected={state.compareMode === 'result'} onClick={() => dispatch({ type: 'SET_COMPARE_MODE', mode: 'result' })}>Result</button><button type="button" role="tab" aria-selected={state.compareMode === 'source'} onClick={() => dispatch({ type: 'SET_COMPARE_MODE', mode: 'source' })}>Source</button></div><dl className="studio-v2__provenance"><div><dt>Provider</dt><dd>{provenance.provider || 'Not recorded'}</dd></div><div><dt>Model</dt><dd>{provenance.model || 'Not recorded'}</dd></div><div><dt>Request ID</dt><dd><code>{provenance.requestId || 'Not recorded'}</code></dd></div><div><dt>Source hash</dt><dd><code>{provenance.sourceSha256 || 'Not recorded'}</code></dd></div><div><dt>Output hash</dt><dd><code>{provenance.outputSha256 || 'Not recorded'}</code></dd></div><div><dt>Recorded cost</dt><dd>{provenance.cost == null ? 'Not recorded' : money(provenance.cost)}</dd></div></dl><div className="studio-v2__qa-box"><div><ShieldCheck size={18} aria-hidden="true" /><strong>{selected.qa?.status === 'pass' ? 'QA passed' : 'QA not recorded or needs review'}</strong></div><p>{selected.qa?.status === 'pass' ? 'Protected pixels and the recorded QA checks passed.' : 'Export remains blocked until the server records a passing QA result.'}</p>{selected.qa?.failures?.length > 0 && <ul>{selected.qa.failures.map((failure) => <li key={failure}>{failure}</li>)}</ul>}</div><button type="button" className="button button--primary button--wide" disabled={selected.qa?.status !== 'pass'} onClick={() => dispatch({ type: 'SET_STEP', step: 'export' })}><DownloadSimple size={17} aria-hidden="true" />Prepare export</button></aside></div></section>
}

function ExportStage({ state, onExport, sourceUrl, variantUrl }) {
  const selected = state.variants.find((item) => item.id === state.selectedVariantId) || state.variants[0]
  const provenance = selected?.provenance || {}
  const ready = Boolean(state.generation.id && selected && provenance.provider && provenance.model && provenance.requestId && selected.qa?.status === 'pass' && state.generation.status === 'done')
  return <section className="studio-v2__stage" aria-labelledby="export-title"><header className="studio-v2__stage-header"><div><p className="studio-v2__kicker">Delivery</p><h2 id="export-title">Download the approved campaign</h2><p>The backend owns the bundle. The browser does not reconstruct missing artifacts.</p></div><span className={'studio-v2__state-pill ' + (ready ? 'is-good' : '')}>{ready ? 'Export ready' : 'Export blocked'}</span></header><div className="studio-v2__export-layout"><div className="studio-v2__export-preview">{variantUrl ? <img src={variantUrl} alt="Selected generated campaign variant" /> : <img src={sourceUrl} alt="Locked product source" />}</div><div className="studio-v2__export-rail"><div className="studio-v2__export-checklist"><div data-passed={isLockReady(state.lock)}><CheckCircle size={17} aria-hidden="true" /><span>Product Lock {isLockReady(state.lock) ? 'validated' : 'not validated'}</span></div><div data-passed={Boolean(provenance.provider && provenance.model && provenance.requestId)}><CheckCircle size={17} aria-hidden="true" /><span>Provenance {provenance.requestId ? 'recorded' : 'not recorded'}</span></div><div data-passed={selected?.qa?.status === 'pass'}><CheckCircle size={17} aria-hidden="true" /><span>QA {selected?.qa?.status === 'pass' ? 'passed' : 'not passed'}</span></div></div><button type="button" className="button button--primary button--large button--wide" onClick={onExport} disabled={!ready}><DownloadSimple size={18} aria-hidden="true" />Download validated ZIP</button><p className="studio-v2__helper"><Info size={14} aria-hidden="true" />Provider URLs and credentials never reach the browser.</p></div></div></section>
}

function EvidenceRail({ state, provider }) {
  const selected = state.variants.find((item) => item.id === state.selectedVariantId) || state.variants[0]
  return <aside className="studio-v2__evidence-rail" aria-label="Campaign evidence"><div className="studio-v2__evidence-heading"><div><p className="studio-v2__kicker">Record</p><h2>Evidence</h2></div><ShieldCheck size={20} aria-hidden="true" /></div><div className="studio-v2__evidence-row"><span>Source</span><strong>{state.source.productId ? 'Stored' : 'Not stored'}</strong></div><div className="studio-v2__evidence-row"><span>Product Lock</span><strong>{state.lock.id ? 'Revision ' + (state.lock.revision || 1) : 'Not created'}</strong></div><div className="studio-v2__evidence-row"><span>Provider</span><strong>{provider?.model || 'Not selected'}</strong></div><div className="studio-v2__evidence-row"><span>Generation</span><strong>{state.generation.id ? statusLabel(state.generation.status) : 'Not started'}</strong></div>{selected && <div className="studio-v2__evidence-result"><span>Selected result</span><strong>{selected.label}</strong><small>{selected.provenance?.requestId ? 'Request recorded' : 'Request not recorded'}</small></div>}<p className="studio-v2__helper">The record is populated from server responses. Empty values stay empty.</p></aside>
}

function CampaignStudioV2() {
  const { id: routeProductId } = useParams()
  const [state, dispatch] = useReducer(studioReducer, undefined, initialStudioState)
  const [file, setFile] = useState(null)
  const [preview, setPreview] = useState('')
  const [busy, setBusy] = useState(false)
  const [liveRegistry, setLiveRegistry] = useState(false)
  const provider = useMemo(() => state.providerProfiles.find((item) => item.id === state.direction.providerId), [state.providerProfiles, state.direction.providerId])
  const sourceBlob = usePrivateProductImage(state.source.productId)
  const maskBlob = usePrivateLockArtifact(state.lock.id, state.lock.maskUrl ? 'mask.png' : '')
  const cutoutBlob = usePrivateLockArtifact(state.lock.id, state.lock.cutoutUrl ? 'cutout.png' : '')
  const variantBlobs = usePrivateVariantImages(state.generation.id, state.variants)
  const sourceUrl = preview || sourceBlob || state.source.imageUrl || state.lock.imageUrl
  const maskUrl = maskBlob || state.lock.maskUrl
  const cutoutUrl = cutoutBlob || state.lock.cutoutUrl

  useEffect(() => {
    if (!file) {
      setPreview('')
      return undefined
    }
    const url = URL.createObjectURL(file)
    setPreview(url)
    return () => URL.revokeObjectURL(url)
  }, [file])

  useEffect(() => {
    if (!routeProductId) return undefined
    let mounted = true
    products.get(routeProductId).then(async (response) => {
      if (!mounted) return
      const product = response.data
      const image = product.image_url || '/api/v1/products/' + product.id + '/image'
      dispatch({ type: 'SET_SOURCE', source: { productId: product.id, name: product.name || '', brand: product.brand || '', category: product.category || 'skincare', imageUrl: image, fileName: 'product-' + product.id, sourceSha256: product.source_sha256 || null, width: product.source_width || null, height: product.source_height || null } })
      dispatch({ type: 'SET_DIRECTION', patch: { category: product.category || 'skincare' } })
      const locks = itemsOf(await studioV2.productLocks.list({ limit: 50 }), ['items', 'locks', 'revisions']).filter((item) => String(item.product_id || item.productId) === String(product.id))
      if (mounted && locks[0]) {
        dispatch({ type: 'SET_LOCK', lock: lockFrom(locks[0], { ...initialStudioState().lock, imageUrl: image, sourceImageUrl: image }) })
        dispatch({ type: 'SET_STEP', step: 'lock' })
      }
      const generations = itemsOf(await studioV2.generations.list({ limit: 50 }), ['items', 'generations']).filter((item) => String(item.product_id || item.productId) === String(product.id))
      const latestGeneration = generations[0]
      if (mounted && latestGeneration?.id) {
        const generationResponse = await studioV2.generations.get(latestGeneration.id)
        const generation = generationFrom(generationResponse, { id: latestGeneration.id, status: latestGeneration.status || 'idle' })
        dispatch({ type: 'SET_GENERATION', patch: generation })
        const variants = variantsFrom(generationResponse)
        if (variants.length) dispatch({ type: 'SET_VARIANTS', variants })
        if (generation.status === 'done' && variants.length) dispatch({ type: 'SET_STEP', step: 'compare' })
        else if (generation.status === 'uncertain' || generation.status === 'failed') dispatch({ type: 'SET_STEP', step: 'generate' })
      }
    }).catch(() => {
      if (mounted) dispatch({ type: 'SET_NOTICE', notice: 'The stored product could not be loaded.' })
    })
    return () => { mounted = false }
  }, [routeProductId])

  useEffect(() => {
    let mounted = true
    studioV2.providerProfiles.list().then((response) => {
      if (!mounted) return
      const profiles = providersFrom(response)
      if (profiles.length) {
        dispatch({ type: 'SET_PROVIDERS', providers: profiles })
        setLiveRegistry(true)
      }
    }).catch((error) => {
      if (mounted) dispatch({ type: 'SET_NOTICE', notice: 'Provider registry unavailable: ' + errorMessage(error) })
    })
    return () => { mounted = false }
  }, [])

  const generationId = state.generation.id
  const generationStatus = state.generation.status
  useEffect(() => {
    if (!generationId || !activeGeneration(generationStatus)) return undefined
    let mounted = true
    let timeout
    const poll = async () => {
      try {
        const response = await studioV2.generations.get(generationId)
        if (!mounted) return
        const generation = generationFrom(response, { id: generationId, status: generationStatus })
        dispatch({ type: 'SET_GENERATION', patch: generation })
        if (generation.status === 'done') {
          const variants = variantsFrom(response)
          if (variants.length) dispatch({ type: 'SET_VARIANTS', variants })
        }
        if (mounted && activeGeneration(generation.status)) timeout = window.setTimeout(poll, 1600)
      } catch (error) {
        if (mounted) dispatch({ type: 'SET_GENERATION', patch: { status: 'uncertain', error: error instanceof ApiError ? error : new ApiError({ message: errorMessage(error), cause: error }) } })
      }
    }
    poll()
    return () => { mounted = false; if (timeout) window.clearTimeout(timeout) }
  }, [generationId, generationStatus])

  const acceptFile = (value) => {
    if (!value) return
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(value.type)) return dispatch({ type: 'SET_NOTICE', notice: 'Choose a JPG, PNG, or WebP product image.' })
    if (value.size > 10 * 1024 * 1024) return dispatch({ type: 'SET_NOTICE', notice: 'The product image must be 10 MB or smaller.' })
    setFile(value)
    dispatch({ type: 'SET_SOURCE', source: { productId: null, fileName: value.name, imageUrl: '', sourceSha256: null } })
  }

  const createLock = async () => {
    if (!file && !state.source.productId) return dispatch({ type: 'SET_NOTICE', notice: 'Upload the real product source before creating a lock.' })
    if (!state.source.name.trim()) return dispatch({ type: 'SET_NOTICE', notice: 'Add a product name before storing the source.' })
    setBusy(true)
    try {
      let product = state.source
      if (!product.productId) {
        const form = new FormData()
        form.set('image', file)
        form.set('name', state.source.name.trim())
        form.set('category', state.source.category || 'skincare')
        if (state.source.brand.trim()) form.set('brand', state.source.brand.trim())
        const created = (await products.create(form)).data
        product = { productId: created.id, name: created.name, brand: created.brand || '', category: created.category, imageUrl: created.image_url || '/api/v1/products/' + created.id + '/image', fileName: file.name, sourceSha256: created.source_sha256, width: created.source_width, height: created.source_height }
        dispatch({ type: 'SET_SOURCE', source: product })
      }
      dispatch({ type: 'SET_DIRECTION', patch: { category: product.category || state.source.category || 'skincare' } })
      const response = await studioV2.productLocks.create({ product_id: product.productId, positive_points: state.lock.points.filter((item) => item.kind === 'positive').map(({ x, y }) => ({ x, y })), negative_points: state.lock.points.filter((item) => item.kind === 'negative').map(({ x, y }) => ({ x, y })) }, { idempotencyKey: createIdempotencyKey() })
      dispatch({ type: 'SET_LOCK', lock: lockFrom(response, { ...state.lock, imageUrl: product.imageUrl, sourceImageUrl: product.imageUrl }), notice: 'Real mask and cutout created. Review the evidence before validating the Product Lock.' })
      dispatch({ type: 'SET_STEP', step: 'lock' })
    } catch (error) {
      dispatch({ type: 'SET_NOTICE', notice: 'Product Lock was not saved: ' + errorMessage(error) })
    } finally {
      setBusy(false)
    }
  }

  const refineLock = async () => {
    if (!state.lock.id) return createLock()
    try {
      const response = await studioV2.productLocks.refine(state.lock.id, { target_box: state.lock.bbox, positive_points: state.lock.points.filter((item) => item.kind === 'positive').map(({ x, y }) => ({ x, y })), negative_points: state.lock.points.filter((item) => item.kind === 'negative').map(({ x, y }) => ({ x, y })) })
      dispatch({ type: 'SET_LOCK', lock: lockFrom(response, state.lock), notice: 'A new Product Lock revision is ready for review.' })
    } catch (error) {
      dispatch({ type: 'SET_NOTICE', notice: 'Corrections were not saved: ' + errorMessage(error) })
    }
  }

  const validateLock = async () => {
    if (!state.lock.id || !state.lock.maskUrl || !state.lock.cutoutUrl) return dispatch({ type: 'SET_NOTICE', notice: 'A real mask and cutout are required before validating the lock.' })
    try {
      const response = await studioV2.productLocks.validate(state.lock.id, {})
      dispatch({ type: 'SET_LOCK', lock: lockFrom(response, state.lock) })
      dispatch({ type: 'SET_LOCK_STATUS', status: 'validated', notice: 'Product Lock validated. The scene brief is now available.' })
      dispatch({ type: 'SET_STEP', step: 'direction' })
    } catch (error) {
      dispatch({ type: 'SET_NOTICE', notice: 'Product Lock validation was not recorded: ' + errorMessage(error) })
    }
  }

  const rejectLock = async () => {
    if (!state.lock.abstentionReason.trim()) return dispatch({ type: 'SET_NOTICE', notice: 'Choose a review reason before keeping this lock under review.' })
    try {
      const response = state.lock.id ? await studioV2.productLocks.reject(state.lock.id, { reason: state.lock.abstentionReason }) : null
      if (response) dispatch({ type: 'SET_LOCK', lock: lockFrom(response, state.lock) })
      dispatch({ type: 'SET_LOCK_STATUS', status: 'rejected', notice: 'This lock stays under review. No generation can start from it.' })
    } catch (error) {
      dispatch({ type: 'SET_NOTICE', notice: 'The review state was not recorded: ' + errorMessage(error) })
    }
  }

  const generate = async () => {
    if (!isGenerationReady(state) || !provider) return dispatch({ type: 'SET_NOTICE', notice: 'Validate the Product Lock, wait for the provider profile, and add creative direction before generating.' })
    if (!provider.capabilities.includes('mask-preservation')) return dispatch({ type: 'SET_NOTICE', notice: 'The selected provider profile cannot preserve the Product Lock.' })
    dispatch({ type: 'SET_GENERATION', patch: { id: null, status: 'queued', stage: 'preparing', message: 'Submitting the immutable brief.', error: null } })
    try {
      const response = await studioV2.generations.create({ contract_version: '2.0.0', product_lock_revision_id: state.lock.id, language: 'en', seed: state.direction.seed, audience: state.direction.audience || undefined, creative_direction: state.direction.prompt, scene_prompt: state.direction.prompt, target_box: state.lock.bbox, provider: provider.selection, variant_count: state.direction.variantCount, budget: { max_cost_micros: Math.round(state.direction.costCeiling * 1000000), max_attempts: 1 } }, { idempotencyKey: createIdempotencyKey() })
      const generation = generationFrom(response, state.generation)
      dispatch({ type: 'SET_GENERATION', patch: generation })
      const variants = variantsFrom(response)
      if (variants.length) dispatch({ type: 'SET_VARIANTS', variants })
    } catch (error) {
      const normalized = error instanceof ApiError ? error : new ApiError({ message: errorMessage(error), status: error?.status, code: error?.code, cause: error })
      dispatch({ type: 'SET_GENERATION', patch: { status: normalized.code === 'PROVIDER_COMPLETION_UNKNOWN' || normalized.status === 408 ? 'uncertain' : 'failed', error: normalized } })
    }
  }

  const cancel = async () => {
    if (!state.generation.id) return
    try {
      await studioV2.generations.cancel(state.generation.id)
      dispatch({ type: 'SET_GENERATION', patch: { status: 'canceled', message: 'Cancellation recorded.' } })
    } catch (error) {
      dispatch({ type: 'SET_NOTICE', notice: 'Cancellation was not confirmed: ' + errorMessage(error) })
    }
  }

  const exportBundle = async () => {
    if (!state.generation.id) return
    try {
      const response = await studioV2.generations.exportBundle(state.generation.id)
      const blob = response?.data instanceof Blob ? response.data : new Blob([response?.data || ''], { type: 'application/zip' })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = 'campaign-' + state.generation.id + '.zip'
      anchor.click()
      URL.revokeObjectURL(url)
      dispatch({ type: 'SET_NOTICE', notice: 'Validated ZIP download started.' })
    } catch (error) {
      dispatch({ type: 'SET_NOTICE', notice: 'Export was not available: ' + errorMessage(error) })
    }
  }

  const removeSource = () => {
    setFile(null)
    dispatch({ type: 'SET_SOURCE', source: { productId: null, imageUrl: '', fileName: '', sourceSha256: null } })
  }

  const selected = state.variants.find((item) => item.id === state.selectedVariantId) || state.variants[0]
  const selectedUrl = selected ? variantBlobs[selected.id] || selected.imageUrl : ''
  const stage = state.step === 'product'
    ? <SourceStage state={state} dispatch={dispatch} file={file} preview={preview} storedUrl={sourceBlob} busy={busy} onFile={acceptFile} onCreate={createLock} onRemove={removeSource} />
    : state.step === 'lock'
      ? <LockStage state={state} dispatch={dispatch} onRefine={refineLock} onValidate={validateLock} onReject={rejectLock} sourceUrl={sourceUrl} maskUrl={maskUrl} cutoutUrl={cutoutUrl} />
      : state.step === 'direction'
        ? <BriefStage state={state} dispatch={dispatch} provider={provider} />
        : state.step === 'generate'
          ? <GenerationStage state={state} dispatch={dispatch} onGenerate={generate} onCancel={cancel} onRetry={generate} />
          : state.step === 'compare'
            ? <ResultStage state={state} dispatch={dispatch} sourceUrl={sourceUrl} variantUrl={selectedUrl} />
            : <ExportStage state={state} onExport={exportBundle} sourceUrl={sourceUrl} variantUrl={selectedUrl} />

  return <div className="studio-v2" data-live-registry={liveRegistry}><div className="studio-v2__workspace-topline"><div className="studio-v2__workspace-title"><span className="studio-v2__workspace-mark" aria-hidden="true">CA</span><div><p className="studio-v2__kicker">Campaign Studio</p><h1>Make a product image</h1></div></div><div className="studio-v2__workspace-actions"><span className="studio-v2__privacy-note"><ShieldCheck size={15} aria-hidden="true" />Private workspace</span><Link className="button button--quiet button--small" to="/dashboard">Campaign history</Link></div></div><StageNav state={state} onStep={(next) => dispatch({ type: 'SET_STEP', step: next })} />{state.notice && <div className="studio-v2__notice" role="status"><Info size={17} aria-hidden="true" /><span>{state.notice}</span><button type="button" className="icon-button" aria-label="Dismiss notice" onClick={() => dispatch({ type: 'SET_NOTICE', notice: null })}><X size={16} aria-hidden="true" /></button></div>}<div className="studio-v2__main-grid"><section className="studio-v2__stage-panel" aria-label="Campaign Studio task stage">{stage}</section><EvidenceRail state={state} provider={provider} /></div></div>
}

export { CampaignStudioV2 }
export default CampaignStudioV2
