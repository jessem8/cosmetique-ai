import { useEffect, useRef, useState } from 'react'
import { Info, ShieldCheck, X } from '@phosphor-icons/react'
import { Link, useParams } from 'react-router-dom'
import { ApiError, products, studioV2 } from '../api/client.js'
import { createIdempotencyKey } from '../utils/idempotency.js'
import BatchPanel from './BatchPanel.jsx'
import DirectionPanel from './DirectionPanel.jsx'
import EngineStatus from './EngineStatus.jsx'
import GenerationPanel from './GenerationPanel.jsx'
import LockPanel from './LockPanel.jsx'
import ReviewPanel from './ReviewPanel.jsx'
import SourcePanel from './SourcePanel.jsx'
import { STUDIO_COPY } from './copy.js'
import { isActiveGeneration, normalizeGeneration, normalizeLock, normalizeProvider, normalizeVariants, responseData, responseItems } from './normalizers.js'
import WorkflowRail from './WorkflowRail.jsx'

const emptySource = { productId: null, name: '', brand: '', category: 'skincare', imageUrl: '', fileName: '', sourceSha256: null, width: null, height: null }
const emptyLock = { id: null, status: 'needs_review', revision: 0, candidateId: null, bbox: { x: 0.28, y: 0.18, width: 0.44, height: 0.64 }, points: [], imageUrl: '', sourceImageUrl: '', maskUrl: '', cutoutUrl: '', candidates: [], metrics: {}, modelProvenance: {}, confidence: null, abstentionReason: '' }
const emptyDirection = { mode: 'automatic', audience: '', placement: 'square', prompt: '', seed: 2808, variantCount: 1, providerId: '', costCeiling: 2 }

const errorText = (error) => {
  if (error?.code === 'PROVIDER_COMPLETION_UNKNOWN') return 'Le résultat n’est pas confirmé. L’export reste bloqué jusqu’à réconciliation.'
  if (error?.status === 429) return 'Le profil de rendu est temporairement limité. Votre verrouillage est conservé.'
  if (error?.status === 404) return 'Cette fonction n’est pas encore disponible sur le serveur.'
  if (error?.status >= 500) return 'Le service est indisponible. Aucun résultat local n’a été inventé.'
  return error?.message || 'La demande n’a pas pu aboutir.'
}

const artifactName = (value, fallback) => value ? value.split('/').pop().split('?')[0] : fallback

const normalizeEngine = (response) => {
  const raw = responseData(response)?.engine || responseData(response) || {}
  return {
    ...raw,
    status: raw.status || (raw.ready ? 'ready' : 'unavailable'),
    message: raw.message || raw.reason || '',
    runtime: raw.runtime || raw.runtime_id || raw.runtime_name || null,
    gpu: raw.gpu || raw.gpu_name || null,
    models: raw.models || raw.loaded_models || [],
  }
}

function useProtectedArtifact(productId, lockId, artifact, preview, kind) {
  const [blobUrl, setBlobUrl] = useState('')
  useEffect(() => {
    if (preview || (!productId && !lockId) || !artifact) {
      setBlobUrl('')
      return undefined
    }
    const controller = new AbortController()
    let objectUrl = ''
    const request = kind === 'source'
      ? products.getImage(productId, { signal: controller.signal })
      : studioV2.productLocks.artifact(lockId, artifact, { signal: controller.signal })
    request.then((response) => {
      if (controller.signal.aborted) return
      objectUrl = URL.createObjectURL(response.data)
      setBlobUrl(objectUrl)
    }).catch(() => {})
    return () => {
      controller.abort()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [artifact, kind, lockId, preview, productId])
  return blobUrl
}

function useVariantImage(generationId, variant) {
  const [blobUrl, setBlobUrl] = useState('')
  useEffect(() => {
    if (!generationId || !variant?.id) {
      setBlobUrl('')
      return undefined
    }
    const controller = new AbortController()
    let objectUrl = ''
    studioV2.generations.variantImage(generationId, variant.id, { signal: controller.signal }).then((response) => {
      if (controller.signal.aborted) return
      objectUrl = URL.createObjectURL(response.data)
      setBlobUrl(objectUrl)
    }).catch(() => {})
    return () => {
      controller.abort()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [generationId, variant])
  return blobUrl || variant?.imageUrl || ''
}

export default function StudioWorkspace() {
  const { id: campaignId } = useParams()
  const [step, setStep] = useState('source')
  const [source, setSource] = useState(emptySource)
  const [lock, setLock] = useState(emptyLock)
  const [direction, setDirection] = useState(emptyDirection)
  const [engine, setEngine] = useState({ status: 'checking', message: '' })
  const [providers, setProviders] = useState([])
  const [batches, setBatches] = useState([])
  const [generation, setGeneration] = useState({ id: null, status: 'idle', stage: null, message: '', error: null })
  const [variants, setVariants] = useState([])
  const [selectedVariantId, setSelectedVariantId] = useState(null)
  const [file, setFile] = useState(null)
  const [preview, setPreview] = useState('')
  const [tool, setTool] = useState('select')
  const [maskVisible, setMaskVisible] = useState(true)
  const [busy, setBusy] = useState(false)
  const [batchLoading, setBatchLoading] = useState(false)
  const [notice, setNotice] = useState('')
  const previewRef = useRef('')

  const sourceBlob = useProtectedArtifact(source.productId, null, source.productId ? 'image' : '', preview, 'source')
  const maskBlob = useProtectedArtifact(null, lock.id, artifactName(lock.maskUrl, 'mask.png'), '', 'mask')
  const sourceUrl = preview || sourceBlob || source.imageUrl || lock.sourceImageUrl || ''
  const maskUrl = maskBlob || lock.maskUrl || ''
  const selectedVariant = variants.find((variant) => variant.id === selectedVariantId) || variants[0]
  const variantUrl = useVariantImage(generation.id, selectedVariant)
  const provider = providers.find((item) => item.id === direction.providerId) || providers[0]
  const lockReady = lock.status === 'validated' && Boolean(lock.id && lock.maskUrl && lock.cutoutUrl)
  const directionReady = lockReady && Boolean(direction.prompt.trim())
  const generationReady = generation.status === 'done' && variants.length > 0

  const refreshEngine = async () => {
    setEngine((current) => ({ ...current, status: 'checking', message: '' }))
    try {
      const response = await studioV2.engine.status()
      setEngine(normalizeEngine(response))
    } catch (error) {
      setEngine({ status: 'unavailable', message: errorText(error) })
    }
  }

  const refreshBatches = async () => {
    setBatchLoading(true)
    try {
      const response = await studioV2.batches.list({ limit: 10 })
      setBatches(responseItems(response, ['batches', 'items']))
    } catch (error) {
      if (error?.status !== 404) setNotice(errorText(error))
    } finally {
      setBatchLoading(false)
    }
  }

  useEffect(() => {
    refreshEngine()
    studioV2.providerProfiles.list().then((response) => {
      const profiles = responseItems(response, ['providers', 'profiles']).map(normalizeProvider)
      setProviders(profiles)
      if (profiles[0]) setDirection((current) => ({ ...current, providerId: current.providerId || profiles[0].id, costCeiling: profiles[0].costCeiling || current.costCeiling, variantCount: Math.min(current.variantCount, profiles[0].maxVariants || 1) }))
    }).catch(() => setProviders([]))
    refreshBatches()
  }, [])

  useEffect(() => {
    if (!campaignId) return undefined
    let active = true
    studioV2.generations.list({ limit: 1 }).then((response) => {
      if (!active) return
      const item = responseItems(response, ['generations', 'items']).find((candidate) => candidate.campaign_id === campaignId)
      if (item) setGeneration((current) => normalizeGeneration(item, current))
    }).catch(() => {})
    return () => { active = false }
  }, [campaignId])

  useEffect(() => {
    if (!generation.id || !isActiveGeneration(generation.status)) return undefined
    let active = true
    let timeout
    const poll = async () => {
      try {
        const response = await studioV2.generations.get(generation.id)
        if (!active) return
        const next = normalizeGeneration(response, {})
        setGeneration(next)
        if (next.status === 'done') {
          const nextVariants = normalizeVariants(response)
          if (nextVariants.length) {
            setVariants(nextVariants)
            setSelectedVariantId((current) => current || nextVariants[0].id)
          } else {
            const variantResponse = await studioV2.generations.variants(generation.id)
            const fetched = normalizeVariants(variantResponse)
            setVariants(fetched)
            setSelectedVariantId((current) => current || fetched[0]?.id || null)
          }
        } else if (isActiveGeneration(next.status)) {
          timeout = window.setTimeout(poll, 1800)
        }
      } catch (error) {
        if (active) setGeneration((current) => ({ ...current, status: 'uncertain', error }))
      }
    }
    poll()
    return () => { active = false; if (timeout) window.clearTimeout(timeout) }
  }, [generation.id, generation.status])

  const changeSource = (patch) => setSource((current) => ({ ...current, ...patch }))
  const changeDirection = (patch) => setDirection((current) => ({ ...current, ...patch }))

  const acceptFile = (nextFile) => {
    if (!nextFile) return
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(nextFile.type)) return setNotice('Choisissez une image JPG, PNG ou WebP.')
    if (nextFile.size > 10 * 1024 * 1024) return setNotice('La photo doit peser 10 Mo ou moins.')
    if (previewRef.current) URL.revokeObjectURL(previewRef.current)
    previewRef.current = URL.createObjectURL(nextFile)
    setPreview(previewRef.current)
    setFile(nextFile)
    setSource((current) => ({ ...current, productId: null, fileName: nextFile.name, imageUrl: '', sourceSha256: null }))
    setLock(emptyLock)
    setNotice('Photo chargée. Enregistrez-la pour créer le Product Lock.')
  }

  useEffect(() => () => { if (previewRef.current) URL.revokeObjectURL(previewRef.current) }, [])

  const createLock = async () => {
    if (!file && !source.productId) return setNotice('Choisissez la vraie photo produit avant de créer le verrouillage.')
    if (!source.name.trim()) return setNotice('Ajoutez un nom de produit avant de continuer.')
    setBusy(true)
    try {
      let stored = source
      if (!stored.productId) {
        const form = new FormData()
        form.set('image', file)
        form.set('name', source.name.trim())
        form.set('category', source.category)
        if (source.brand.trim()) form.set('brand', source.brand.trim())
        const response = await products.create(form)
        const raw = responseData(response)
        stored = { ...source, productId: raw.id, name: raw.name || source.name, brand: raw.brand || source.brand, category: raw.category || source.category, imageUrl: raw.image_url || `/api/v1/products/${raw.id}/image`, sourceSha256: raw.source_sha256 || null, width: raw.source_width || null, height: raw.source_height || null }
        setSource(stored)
      }
      const response = await studioV2.productLocks.create({ product_id: stored.productId, language: 'fr', positive_points: lock.points.filter((point) => point.kind === 'positive').map(({ x, y }) => ({ x, y })), negative_points: lock.points.filter((point) => point.kind === 'negative').map(({ x, y }) => ({ x, y })) }, { idempotencyKey: createIdempotencyKey() })
      const nextLock = normalizeLock(response, { ...lock, imageUrl: stored.imageUrl, sourceImageUrl: stored.imageUrl })
      setLock(nextLock)
      setDirection((current) => ({ ...current, category: stored.category }))
      setStep('lock')
      setNotice('Product Lock créé. Vérifiez le masque avant de le valider.')
    } catch (error) {
      setNotice(`Le Product Lock n’a pas été enregistré. ${errorText(error)}`)
    } finally {
      setBusy(false)
    }
  }

  const saveCorrections = async () => {
    if (!lock.id) return createLock()
    setBusy(true)
    try {
      const response = await studioV2.productLocks.refine(lock.id, { target_box: lock.bbox, positive_points: lock.points.filter((point) => point.kind === 'positive').map(({ x, y }) => ({ x, y })), negative_points: lock.points.filter((point) => point.kind === 'negative').map(({ x, y }) => ({ x, y })) })
      setLock(normalizeLock(response, lock))
      setNotice('Une nouvelle révision du Product Lock est prête à vérifier.')
    } catch (error) {
      setNotice(`Les corrections n’ont pas été enregistrées. ${errorText(error)}`)
    } finally {
      setBusy(false)
    }
  }

  const validateLock = async () => {
    if (!lock.id || !lock.maskUrl || !lock.cutoutUrl) return setNotice('Un masque et un détourage réels sont nécessaires avant validation.')
    setBusy(true)
    try {
      const response = await studioV2.productLocks.validate(lock.id, { language: 'fr' })
      setLock(normalizeLock(response, { ...lock, status: 'validated' }))
      setStep('direction')
      setNotice('Product Lock validé. Vous pouvez préparer le décor.')
    } catch (error) {
      setNotice(`La validation n’a pas été enregistrée. ${errorText(error)}`)
    } finally {
      setBusy(false)
    }
  }

  const rejectLock = async () => {
    if (!lock.abstentionReason.trim()) return setNotice('Ajoutez un motif avant d’envoyer le verrouillage en revue.')
    try {
      if (lock.id) await studioV2.productLocks.reject(lock.id, { reason: lock.abstentionReason, language: 'fr' })
      setLock((current) => ({ ...current, status: 'rejected' }))
      setNotice('Le Product Lock reste en revue. Aucune génération ne peut partir de cette version.')
    } catch (error) {
      setNotice(`La revue n’a pas été enregistrée. ${errorText(error)}`)
    }
  }

  const chooseToolOrCandidate = (value) => {
    if (typeof value === 'string') return setTool(value)
    const candidate = value
    const bbox = candidate.box || candidate.bbox || candidate.target_box
    setLock((current) => ({ ...current, candidateId: candidate.id || current.candidateId, bbox: bbox || current.bbox, status: 'needs_review' }))
    setTool('select')
  }

  const addCorrectionPoint = (event) => {
    if (!['positive', 'negative'].includes(tool)) return
    const rect = event.currentTarget.getBoundingClientRect()
    if (!rect.width || !rect.height) return
    const point = { id: `${tool}-${Date.now()}`, kind: tool, x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)), y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)) }
    setLock((current) => ({ ...current, points: [...current.points, point], status: 'needs_review' }))
  }

  const previewDirection = async () => {
    setBusy(true)
    const localPrompt = `Décor ${source.category || 'beauté'} contemporain, lumière naturelle douce, composition aérée, matières minérales, espace calme autour de ${source.name || 'la référence produit'}.`
    try {
      const response = await studioV2.directions.preview({ product_lock_revision_id: lock.id, mode: direction.mode, language: 'fr', category: source.category, product_name: source.name, brand: source.brand, prompt: direction.mode === 'custom' ? direction.prompt : undefined })
      const raw = responseData(response)
      setDirection((current) => ({ ...current, prompt: raw.prompt || raw.scene_prompt || localPrompt }))
      setNotice('Direction française préparée à partir du Product Lock.')
    } catch (error) {
      setDirection((current) => ({ ...current, prompt: current.mode === 'automatic' ? localPrompt : current.prompt }))
      setNotice(error?.status === 404 ? 'Le serveur ne propose pas encore l’aperçu. La direction automatique reste disponible à partir du produit verrouillé.' : errorText(error))
    } finally {
      setBusy(false)
    }
  }

  const generate = async () => {
    if (!engine.status || engine.status !== 'ready') return setNotice('Colab doit être confirmé avant toute génération.')
    if (!lockReady || !direction.prompt.trim() || !provider) return setNotice('Validez le Product Lock, préparez la direction et vérifiez le profil de rendu.')
    setBusy(true)
    setGeneration({ id: null, status: 'queued', stage: 'preparing', message: 'Demande envoyée au moteur Colab.', error: null })
    try {
      const response = await studioV2.generations.create({ contract_version: '2.0.0', product_lock_revision_id: lock.id, language: 'fr', direction_mode: direction.mode, creative_direction: direction.prompt, scene_prompt: direction.prompt, placement: direction.placement, audience: direction.audience || undefined, seed: direction.seed, target_box: lock.bbox, provider: provider.selection, variant_count: direction.variantCount, budget: { max_cost_micros: Math.round(direction.costCeiling * 1000000), max_attempts: 1 } }, { idempotencyKey: createIdempotencyKey() })
      const nextGeneration = normalizeGeneration(response, generation)
      const nextVariants = normalizeVariants(response)
      setGeneration(nextGeneration)
      setVariants(nextVariants)
      setSelectedVariantId(nextVariants[0]?.id || null)
      setNotice(nextGeneration.status === 'uncertain' ? 'Le résultat reste à réconcilier.' : 'Génération envoyée à Colab.')
    } catch (error) {
      const normalized = error instanceof ApiError ? error : new ApiError({ message: errorText(error), status: error?.status, code: error?.code, cause: error })
      setGeneration({ id: null, status: normalized.code === 'PROVIDER_COMPLETION_UNKNOWN' || normalized.status === 408 ? 'uncertain' : 'failed', stage: null, message: '', error: normalized })
      setNotice(errorText(normalized))
    } finally {
      setBusy(false)
    }
  }

  const cancel = async () => {
    if (!generation.id) return
    try {
      await studioV2.generations.cancel(generation.id)
      setGeneration((current) => ({ ...current, status: 'canceled', message: 'Demande annulée.' }))
    } catch (error) {
      setNotice(errorText(error))
    }
  }

  const exportBundle = async () => {
    if (!generation.id) return
    try {
      const response = await studioV2.generations.exportBundle(generation.id)
      const blob = response?.data instanceof Blob ? response.data : new Blob([response?.data || ''], { type: 'application/zip' })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `campagne-${generation.id}.zip`
      anchor.click()
      URL.revokeObjectURL(url)
      setNotice('Téléchargement du ZIP lancé.')
    } catch (error) {
      setNotice(`L’export n’est pas disponible. ${errorText(error)}`)
    }
  }

  const createBatch = async () => {
    setBatchLoading(true)
    try {
      const response = await studioV2.batches.create({ language: 'fr', source_scope: 'canonical', regression_scope: 'augmented', acceptance_scope: 'acceptance' }, { idempotencyKey: createIdempotencyKey() })
      const batch = responseData(response)
      setBatches((current) => [batch, ...current.filter((item) => item.id !== batch.id)])
      setNotice('Traitement du dossier préparé. Chaque fichier recevra un état terminal.')
    } catch (error) {
      setNotice(errorText(error))
    } finally {
      setBatchLoading(false)
    }
  }

  let stage = <SourcePanel source={source} file={file} preview={preview} busy={busy} onChange={changeSource} onFile={acceptFile} onCreate={createLock} onRemove={() => { setFile(null); setPreview(''); changeSource({ productId: null, imageUrl: '', fileName: '' }) }} />
  if (step === 'lock') stage = <LockPanel source={source} lock={lock} sourceUrl={sourceUrl} maskUrl={maskUrl} tool={tool} maskVisible={maskVisible} busy={busy} onTool={setTool} onCanvasClick={addCorrectionPoint} onSave={saveCorrections} onValidate={validateLock} onReject={rejectLock} onReason={(abstentionReason) => setLock((current) => ({ ...current, abstentionReason }))} onToggleMask={() => setMaskVisible((current) => !current)} onCandidate={chooseToolOrCandidate} />
  if (step === 'direction') stage = <DirectionPanel direction={direction} source={source} lockReady={lockReady} busy={busy} onChange={changeDirection} onPreview={previewDirection} onContinue={() => setStep('generation')} />
  if (step === 'generation') stage = <GenerationPanel direction={direction} generation={generation} provider={provider} engine={engine} busy={busy} onBack={() => setStep('direction')} onGenerate={generate} onCancel={cancel} onChange={changeDirection} />
  if (step === 'review') stage = <ReviewPanel sourceUrl={sourceUrl} variantUrl={variantUrl} variant={selectedVariant} variants={variants} selectedId={selectedVariantId} onSelect={setSelectedVariantId} onBack={() => setStep('generation')} onExport={exportBundle} />

  return (
    <div className="atelier" data-engine={engine.status}>
      <div className="atelier__intro"><div><p className="atelier-kicker">{STUDIO_COPY.eyebrow}</p><h1>{STUDIO_COPY.title}</h1><p>{STUDIO_COPY.intro}</p></div><div className="atelier__intro-actions"><span><ShieldCheck size={17} aria-hidden="true" />{STUDIO_COPY.privateWorkspace}</span><Link to="/dashboard">{STUDIO_COPY.campaignHistory}</Link></div></div>
      <WorkflowRail step={step} sourceReady={Boolean(source.productId || file)} lockReady={lockReady} directionReady={directionReady} generationReady={generationReady} onStep={setStep} />
      {notice && <div className="atelier-notice" role="status"><Info size={18} aria-hidden="true" /><span>{notice}</span><button type="button" className="atelier-icon-button" aria-label="Fermer le message" onClick={() => setNotice('')}><X size={17} aria-hidden="true" /></button></div>}
      <div className="atelier__main"><div className="atelier__stage-column">{stage}</div><aside className="atelier__aside"><EngineStatus engine={engine} onRefresh={refreshEngine} /><BatchPanel batches={batches} loading={batchLoading} engineReady={engine.status === 'ready'} onCreate={createBatch} onRefresh={refreshBatches} /></aside></div>
    </div>
  )
}
