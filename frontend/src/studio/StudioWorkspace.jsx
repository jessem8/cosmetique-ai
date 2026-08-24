import { useEffect, useRef, useState } from 'react'
import { Info, ShieldCheck, X } from '@phosphor-icons/react'
import { Link } from 'react-router-dom'
import { products, studioV2 } from '../api/client.js'
import BrandMark from '../components/BrandMark.jsx'
import { createIdempotencyKey } from '../utils/idempotency.js'
import EngineStatus from './EngineStatus.jsx'
import LockPanel from './LockPanel.jsx'
import { STUDIO_COPY } from './copy.js'
import { normalizeLock, responseData } from './normalizers.js'
import SourcePanel from './SourcePanel.jsx'
import WorkflowRail from './WorkflowRail.jsx'

const emptySource = {
  productId: null,
  name: '',
  brand: '',
  category: 'skincare',
  imageUrl: '',
  fileName: '',
  sourceSha256: null,
  width: null,
  height: null,
}

const emptyLock = {
  id: null,
  status: 'idle',
  revision: 0,
  bbox: null,
  points: [],
  imageUrl: '',
  sourceImageUrl: '',
  maskUrl: '',
  cutoutUrl: '',
  metrics: {},
  modelProvenance: {},
  confidence: null,
}

const errorText = (error) => {
  if (error?.status === 429) return 'Le runtime GPU est temporairement limité. Aucun détourage incomplet n’a été accepté.'
  if (error?.status === 404) return 'Cette fonction n’est pas disponible sur le serveur.'
  if (error?.status >= 500) return 'Le runtime GPU n’a pas produit de détourage exploitable.'
  return error?.message || 'L’extraction n’a pas pu aboutir.'
}

const artifactName = (value, fallback) =>
  value ? value.split('/').pop().split('?')[0] : fallback

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
    setBlobUrl('')
    if (preview || (!productId && !lockId) || !artifact) return undefined

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

export default function StudioWorkspace() {
  const [step, setStep] = useState('source')
  const [source, setSource] = useState(emptySource)
  const [lock, setLock] = useState(emptyLock)
  const [engine, setEngine] = useState({ status: 'checking', message: '' })
  const [file, setFile] = useState(null)
  const [preview, setPreview] = useState('')
  const [maskVisible, setMaskVisible] = useState(true)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const previewRef = useRef('')
  const submissionRef = useRef(false)

  const sourceBlob = useProtectedArtifact(
    source.productId,
    null,
    source.productId ? 'image' : '',
    preview,
    'source',
  )
  const maskBlob = useProtectedArtifact(
    null,
    lock.id,
    artifactName(lock.maskUrl, 'mask.png'),
    '',
    'mask',
  )
  const cutoutBlob = useProtectedArtifact(
    null,
    lock.id,
    artifactName(lock.cutoutUrl, 'cutout.png'),
    '',
    'cutout',
  )

  const sourceUrl = preview || sourceBlob || source.imageUrl || lock.sourceImageUrl || ''
  const maskUrl = maskBlob || lock.maskUrl || ''
  const cutoutUrl = cutoutBlob || lock.cutoutUrl || ''
  const lockCreated = Boolean(lock.id)
  const lockReady = lock.status === 'validated' && Boolean(lock.maskUrl && lock.cutoutUrl)

  const refreshEngine = async () => {
    setEngine((current) => ({ ...current, status: 'checking', message: '' }))
    try {
      const response = await studioV2.engine.status()
      setEngine(normalizeEngine(response))
    } catch (error) {
      setEngine({ status: 'unavailable', message: errorText(error) })
    }
  }

  useEffect(() => {
    refreshEngine()
    const interval = window.setInterval(refreshEngine, 15_000)
    return () => window.clearInterval(interval)
  }, [])

  const changeSource = (patch) =>
    setSource((current) => ({ ...current, ...patch }))

  const acceptFile = (nextFile) => {
    if (!nextFile) return
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(nextFile.type)) {
      setNotice('Choisissez une image JPG, PNG ou WebP.')
      return
    }
    if (nextFile.size > 10 * 1024 * 1024) {
      setNotice('La photo doit peser 10 Mo ou moins.')
      return
    }
    if (previewRef.current) URL.revokeObjectURL(previewRef.current)
    previewRef.current = URL.createObjectURL(nextFile)
    setPreview(previewRef.current)
    setFile(nextFile)
    setSource((current) => ({
      ...current,
      productId: null,
      fileName: nextFile.name,
      imageUrl: '',
      sourceSha256: null,
    }))
    setLock(emptyLock)
    setStep('source')
    setNotice('Photo chargée. Lancez l’extraction pour créer le masque réel.')
  }

  const clearSource = () => {
    if (previewRef.current) URL.revokeObjectURL(previewRef.current)
    previewRef.current = ''
    setFile(null)
    setPreview('')
    setSource(emptySource)
    setLock(emptyLock)
    setStep('source')
    setNotice('Photo retirée.')
  }

  useEffect(() => () => {
    if (previewRef.current) URL.revokeObjectURL(previewRef.current)
  }, [])

  const createLock = async () => {
    if (submissionRef.current || busy) return
    if (!file && !source.productId) {
      setNotice('Choisissez la vraie photo produit avant de lancer l’extraction.')
      return
    }
    if (!source.name.trim()) {
      setNotice('Ajoutez un nom de produit avant de continuer.')
      return
    }

    submissionRef.current = true
    setBusy(true)
    try {
      let stored = source
      if (!stored.productId) {
        const form = new FormData()
        // Normalize the browser File into a Blob before handing it to Axios.
        // This keeps multipart uploads valid in browsers and in the Node test adapter.
        const upload = typeof file.arrayBuffer === 'function'
          ? new Blob([await file.arrayBuffer()], { type: file.type || 'application/octet-stream' })
          : file
        form.append('image', upload, file.name)
        form.set('name', source.name.trim())
        form.set('category', source.category)
        if (source.brand.trim()) form.set('brand', source.brand.trim())
        const response = await products.create(form)
        const raw = responseData(response)
        stored = {
          ...source,
          productId: raw.id,
          name: raw.name || source.name,
          brand: raw.brand || source.brand,
          category: raw.category || source.category,
          imageUrl: raw.image_url || `/api/v1/products/${raw.id}/image`,
          sourceSha256: raw.source_sha256 || null,
          width: raw.source_width || null,
          height: raw.source_height || null,
        }
        setSource(stored)
      }

      const response = await studioV2.productLocks.create({
        product_id: stored.productId,
        positive_points: lock.points
          .filter((point) => point.kind === 'positive')
          .map(({ x, y }) => ({ x, y })),
        negative_points: lock.points
          .filter((point) => point.kind === 'negative')
          .map(({ x, y }) => ({ x, y })),
      }, { idempotencyKey: createIdempotencyKey() })
      const nextLock = normalizeLock(response, {
        ...lock,
        imageUrl: stored.imageUrl,
        sourceImageUrl: stored.imageUrl,
      })
      setLock(nextLock)
      setStep('lock')
      setNotice('Extraction terminée. Vérifiez le masque et le détourage transparent.')
    } catch (error) {
      setLock(emptyLock)
      setStep('source')
      setNotice(errorText(error))
    } finally {
      submissionRef.current = false
      setBusy(false)
    }
  }

  const saveCorrections = async () => {
    if (!lock.id) return createLock()
    setBusy(true)
    try {
      const response = await studioV2.productLocks.refine(lock.id, {
        target_box: lock.bbox,
        positive_points: lock.points
          .filter((point) => point.kind === 'positive')
          .map(({ x, y }) => ({ x, y })),
        negative_points: lock.points
          .filter((point) => point.kind === 'negative')
          .map(({ x, y }) => ({ x, y })),
      })
      setLock(normalizeLock(response, lock))
      setNotice('Une nouvelle révision est prête. Vérifiez le masque avant de la valider.')
    } catch (error) {
      setNotice(`Les corrections n’ont pas été enregistrées. ${errorText(error)}`)
    } finally {
      setBusy(false)
    }
  }

  const validateLock = async () => {
    if (!lock.id || !lock.maskUrl || !lock.cutoutUrl) {
      setNotice('Un masque et un détourage réels sont nécessaires avant validation.')
      return
    }
    if (lock.status === 'validated') {
      setNotice('Extraction déjà validée. Le test s’arrête ici.')
      return
    }
    setBusy(true)
    try {
      const response = await studioV2.productLocks.validate(lock.id, {})
      setLock(normalizeLock(response, { ...lock, status: 'validated' }))
      setNotice('Extraction validée. Le test s’arrête ici, sans génération de décor.')
    } catch (error) {
      setNotice(`La validation n’a pas été enregistrée. ${errorText(error)}`)
    } finally {
      setBusy(false)
    }
  }

  const addCorrectionPoint = ({ x, y }) => {
    const point = {
      id: `issue-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      kind: 'negative',
      x,
      y,
    }
    setLock((current) => ({
      ...current,
      points: [...current.points, point],
      status: 'needs_review',
    }))
  }

  const stage = step === 'lock' && lockCreated ? (
    <LockPanel
      source={source}
      lock={lock}
      sourceUrl={sourceUrl}
      maskUrl={maskUrl}
      cutoutUrl={cutoutUrl}
      maskVisible={maskVisible}
      busy={busy}
      onBrushPoint={addCorrectionPoint}
      onSave={saveCorrections}
      onValidate={validateLock}
      onToggleMask={() => setMaskVisible((current) => !current)}
    />
  ) : (
    <SourcePanel
      source={source}
      file={file}
      preview={preview}
      busy={busy}
      onChange={changeSource}
      onFile={acceptFile}
      onCreate={createLock}
      onRemove={clearSource}
    />
  )

  return (
    <div className="atelier" data-engine={engine.status} data-scope="product-extraction">
      <div className="atelier__intro">
        <div>
          <BrandMark compact />
          <p className="atelier-kicker">{STUDIO_COPY.eyebrow}</p>
          <h1>{STUDIO_COPY.title}</h1>
          <p>{STUDIO_COPY.intro}</p>
        </div>
        <div className="atelier__intro-actions">
          <span><ShieldCheck size={17} aria-hidden="true" />{STUDIO_COPY.privateWorkspace}</span>
          <Link to="/dashboard">{STUDIO_COPY.campaignHistory}</Link>
        </div>
      </div>

      <WorkflowRail
        step={step}
        lockCreated={lockCreated}
        lockReady={lockReady}
        onStep={setStep}
      />

      {notice && (
        <div className="atelier-notice" role="status">
          <Info size={18} aria-hidden="true" />
          <span>{notice}</span>
          <button
            type="button"
            className="atelier-icon-button"
            aria-label="Fermer le message"
            onClick={() => setNotice('')}
          >
            <X size={17} aria-hidden="true" />
          </button>
        </div>
      )}

      <div className="atelier__main">
        <div className="atelier__stage-column">{stage}</div>
        <aside className="atelier__aside">
          <EngineStatus engine={engine} onRefresh={refreshEngine} />
        </aside>
      </div>
    </div>
  )
}
