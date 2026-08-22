import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Link, useParams } from 'react-router-dom'
import {
  ArrowLeft,
  Check,
  Copy,
  DownloadSimple,
  Eye,
  MagicWand,
  WarningCircle,
  X,
} from '@phosphor-icons/react'
import { generations, products } from '../api/client.js'

const PLATFORMS = [
  {
    id: 'instagram',
    label: 'Instagram',
    filename: 'instagram.jpg',
    dimensions: '1080 × 1080',
  },
  {
    id: 'facebook',
    label: 'Facebook',
    filename: 'facebook.jpg',
    dimensions: '1200 × 630',
  },
  {
    id: 'linkedin',
    label: 'LinkedIn',
    filename: 'linkedin.jpg',
    dimensions: '1200 × 627',
  },
]

const readableCopy = (platformCopy) => {
  if (!platformCopy?.text) return ''
  const hashtags = Array.isArray(platformCopy.hashtags)
    ? platformCopy.hashtags.join(' ')
    : ''
  return [platformCopy.text, hashtags].filter(Boolean).join('\n\n')
}

const artifactNames = (artifacts) => {
  if (Array.isArray(artifacts)) {
    return artifacts
      .map((asset) => (typeof asset === 'string' ? asset : asset?.name))
      .filter(Boolean)
  }
  if (artifacts && typeof artifacts === 'object') return Object.keys(artifacts)
  return []
}

function useCampaignAssets(generation) {
  const [attempt, setAttempt] = useState(0)
  const [state, setState] = useState({
    generationId: null,
    urls: {},
    error: null,
    isLoading: false,
  })
  const retry = useCallback(() => {
    setState((current) => ({
      ...current,
      error: null,
      isLoading: true,
    }))
    setAttempt((current) => current + 1)
  }, [])
  const generationArtifactNames = useMemo(
    () => artifactNames(generation?.artifacts),
    [generation?.artifacts]
  )

  useEffect(() => {
    if (generation?.status !== 'done') return undefined
    const controller = new AbortController()
    const ownedUrls = []
    let active = true
    const files = [
      ...PLATFORMS.map((platform) => platform.filename),
      'cutout.png',
      'mask.png',
      'background.jpg',
      ...generationArtifactNames.filter((name) =>
        name.endsWith('-enhanced.jpg')
      ),
    ]

    setState({
      generationId: generation.id,
      urls: {},
      error: null,
      isLoading: true,
    })

    Promise.all(
      files.map(async (filename) => {
        const response = await generations.getArtifact(
          generation.id,
          filename,
          { signal: controller.signal }
        )
        return [filename, response.data]
      })
    )
      .then((entries) => {
        if (!active) return
        const urls = Object.fromEntries(
          entries.map(([filename, blob]) => {
            const objectUrl = URL.createObjectURL(blob)
            ownedUrls.push(objectUrl)
            return [filename, objectUrl]
          })
        )
        setState({
          generationId: generation.id,
          urls,
          error: null,
          isLoading: false,
        })
      })
      .catch((requestError) => {
        if (
          !active ||
          controller.signal.aborted ||
          requestError.code === 'REQUEST_CANCELLED'
        ) {
          return
        }
        controller.abort()
        setState({
          generationId: generation.id,
          urls: {},
          error: requestError,
          isLoading: false,
        })
      })

    return () => {
      active = false
      controller.abort()
      ownedUrls.forEach((url) => URL.revokeObjectURL(url))
    }
  }, [attempt, generation?.id, generation?.status, generationArtifactNames])

  return {
    urls: state.urls,
    error: state.error,
    isLoading:
      generation?.status === 'done' &&
      (state.generationId !== generation.id || state.isLoading),
    retry,
  }
}

function useOriginalImage(generation) {
  const [attempt, setAttempt] = useState(0)
  const [state, setState] = useState({
    generationId: null,
    url: null,
    error: null,
    isLoading: false,
  })
  const retry = useCallback(() => {
    setState((current) => ({
      ...current,
      error: null,
      isLoading: true,
    }))
    setAttempt((current) => current + 1)
  }, [])

  useEffect(() => {
    if (generation?.status !== 'done') return undefined

    if (!generation.product_id) {
      setState({
        generationId: generation.id,
        url: null,
        error: new Error(
          'La campagne terminée ne référence aucune photo originale.'
        ),
        isLoading: false,
      })
      return undefined
    }

    const controller = new AbortController()
    let objectUrl = null
    setState({
      generationId: generation.id,
      url: null,
      error: null,
      isLoading: true,
    })

    products
      .getImage(generation.product_id, { signal: controller.signal })
      .then((response) => {
        if (controller.signal.aborted) return
        objectUrl = URL.createObjectURL(response.data)
        setState({
          generationId: generation.id,
          url: objectUrl,
          error: null,
          isLoading: false,
        })
      })
      .catch((requestError) => {
        if (
          controller.signal.aborted ||
          requestError.code === 'REQUEST_CANCELLED'
        ) {
          return
        }
        setState({
          generationId: generation.id,
          url: null,
          error: requestError,
          isLoading: false,
        })
      })

    return () => {
      controller.abort()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [
    attempt,
    generation?.id,
    generation?.product_id,
    generation?.status,
  ])

  return {
    url: state.url,
    error: state.error,
    isLoading:
      generation?.status === 'done' &&
      (state.generationId !== generation.id || state.isLoading),
    retry,
  }
}

function EvidenceDrawer({
  open,
  onClose,
  originalUrl,
  assetUrls,
  platform,
  isLoading,
  error,
  onRetry,
}) {
  const closeRef = useRef(null)
  const drawerRef = useRef(null)

  useEffect(() => {
    if (!open) return undefined
    const previous = document.activeElement
    const inertTargets = [...document.body.children].filter(
      (element) => !element.hasAttribute('data-evidence-layer')
    )
    const inertState = inertTargets.map((element) => ({
      element,
      wasInert: element.hasAttribute('inert'),
    }))
    inertTargets.forEach((element) => element.setAttribute('inert', ''))
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    closeRef.current?.focus()
    const handleKey = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onClose()
        return
      }
      if (event.key !== 'Tab') return
      const focusable = [
        ...(drawerRef.current?.querySelectorAll(
          'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])'
        ) || []),
      ]
      if (focusable.length === 0) {
        event.preventDefault()
        return
      }
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (focusable.length === 1) {
        event.preventDefault()
        first.focus()
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', handleKey)
    return () => {
      document.removeEventListener('keydown', handleKey)
      inertState.forEach(({ element, wasInert }) => {
        if (!wasInert) element.removeAttribute('inert')
      })
      document.body.style.overflow = previousOverflow
      previous?.focus?.()
    }
  }, [onClose, open])

  if (!open) return null

  const evidence = [
    ['Original', originalUrl, 'Photo originale du produit'],
    ['Masque', assetUrls['mask.png'], 'Masque validé du produit'],
    ['Découpe', assetUrls['cutout.png'], 'Produit détouré'],
    ['Décor', assetUrls['background.jpg'], 'Décor généré sans le produit'],
    [
      'Composition',
      assetUrls[platform.filename],
      `Composition finale ${platform.label}`,
    ],
  ]

  return createPortal(
    <div
      className="drawer-backdrop"
      data-evidence-layer=""
      onMouseDown={onClose}
    >
      <div
        ref={drawerRef}
        className="evidence-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="evidence-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="evidence-drawer__header">
          <div>
            <p className="eyebrow">Traçabilité</p>
            <h2 id="evidence-title">Preuves de composition</h2>
          </div>
          <button
            ref={closeRef}
            type="button"
            className="icon-button"
            aria-label="Fermer les preuves"
            onClick={onClose}
          >
            <X size={20} aria-hidden="true" />
          </button>
        </header>
        <p className="evidence-drawer__intro">
          Le produit détouré reste séparé du décor généré jusqu’à la composition
          finale.
        </p>
        {error && (
          <div className="inline-alert inline-alert--error" role="alert">
            <div>
              <strong>Preuves indisponibles</strong>
              <p>{error.message}</p>
            </div>
            <button
              type="button"
              className="button button--secondary button--small"
              onClick={onRetry}
            >
              Réessayer les preuves
            </button>
          </div>
        )}
        <div className="evidence-grid">
          {evidence.map(([label, url, alt]) => (
            <figure className="evidence-item" key={label}>
              <div className="evidence-item__media">
                {url ? (
                  <img src={url} alt={alt} />
                ) : isLoading ? (
                  <>
                    <div className="skeleton skeleton--media" />
                    <span className="visually-hidden">
                      Chargement de {label.toLowerCase()}
                    </span>
                  </>
                ) : (
                  <div className="asset-failure asset-failure--compact">
                    <WarningCircle
                      size={22}
                      weight="light"
                      aria-hidden="true"
                    />
                    <span>Fichier indisponible</span>
                  </div>
                )}
              </div>
              <figcaption>{label}</figcaption>
            </figure>
          ))}
        </div>
      </div>
    </div>,
    document.body
  )
}

function Result() {
  const { id } = useParams()
  const [generation, setGeneration] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [activePlatform, setActivePlatform] = useState('instagram')
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [copied, setCopied] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const [captioning, setCaptioning] = useState(false)
  const [enhancing, setEnhancing] = useState(false)
  const [showEnhanced, setShowEnhanced] = useState(false)
  const tabRefs = useRef([])
  const assets = useCampaignAssets(generation)
  const original = useOriginalImage(generation)
  const retryAssets = assets.retry
  const retryOriginal = original.retry

  const platform =
    PLATFORMS.find((item) => item.id === activePlatform) || PLATFORMS[0]
  const platformCopy = generation?.copy?.[activePlatform] || null
  const enhancedFilename = `${platform.id}-enhanced.jpg`
  const hasEnhanced = Boolean(assets.urls[enhancedFilename])
  const displayedFilename = showEnhanced && hasEnhanced ? enhancedFilename : platform.filename
  const copyLanguage = generation?.language === 'en' ? 'en' : 'fr'
  const evidenceError = original.error || assets.error
  const evidenceLoading = original.isLoading || assets.isLoading
  const copyText = useMemo(
    () => readableCopy(platformCopy),
    [platformCopy]
  )
  const retryEvidence = useCallback(() => {
    retryOriginal()
    retryAssets()
  }, [retryAssets, retryOriginal])

  useEffect(() => {
    const controller = new AbortController()
    generations
      .get(id, { signal: controller.signal })
      .then((response) => setGeneration(response.data))
      .catch((requestError) => {
        if (requestError.code !== 'REQUEST_CANCELLED') setError(requestError)
      })
      .finally(() => setLoading(false))
    return () => controller.abort()
  }, [id])

  useEffect(() => {
    setShowEnhanced(false)
    setCopied(false)
  }, [activePlatform])

  const selectPlatformByKey = (event, index) => {
    const direction = {
      ArrowRight: 1,
      ArrowDown: 1,
      ArrowLeft: -1,
      ArrowUp: -1,
    }[event.key]
    if (!direction) return
    event.preventDefault()
    const next = (index + direction + PLATFORMS.length) % PLATFORMS.length
    setActivePlatform(PLATFORMS[next].id)
    tabRefs.current[next]?.focus()
  }

  const copyCampaign = async () => {
    if (!copyText) return
    try {
      await navigator.clipboard.writeText(copyText)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1800)
    } catch {
      setError(new Error('Le texte n’a pas pu être copié.'))
    }
  }

  const downloadBundle = async () => {
    setDownloading(true)
    setError(null)
    try {
      const response = await generations.getBundle(id)
      const url = URL.createObjectURL(response.data)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `cosmetique-ai-${id}.zip`
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      URL.revokeObjectURL(url)
    } catch (requestError) {
      setError(requestError)
    } finally {
      setDownloading(false)
    }
  }

  const downloadVisual = async () => {
    setDownloading(true)
    setError(null)
    try {
      const response = await generations.getArtifact(id, displayedFilename)
      const url = URL.createObjectURL(response.data)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `cosmetique-ai-${platform.id}${showEnhanced && hasEnhanced ? '-enhanced' : ''}.jpg`
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      URL.revokeObjectURL(url)
    } catch (requestError) {
      setError(requestError)
    } finally {
      setDownloading(false)
    }
  }

  const generateCaption = async () => {
    setCaptioning(true)
    setError(null)
    try {
      const response = await generations.generateCaption(id, platform.id)
      setGeneration(response.data)
    } catch (requestError) {
      setError(requestError)
    } finally {
      setCaptioning(false)
    }
  }

  const enhanceVisual = async () => {
    setEnhancing(true)
    setError(null)
    try {
      const response = await generations.enhance(id, platform.id)
      setGeneration(response.data)
      setShowEnhanced(true)
    } catch (requestError) {
      setError(requestError)
    } finally {
      setEnhancing(false)
    }
  }

  if (loading) {
    return (
      <div className="workspace-page workspace-page--wide">
        <div className="result-layout">
          <div className="skeleton skeleton--poster" />
          <div className="skeleton skeleton--timeline" />
        </div>
      </div>
    )
  }

  if (error && !generation) {
    return (
      <div className="workspace-page">
        <section className="empty-state empty-state--error">
          <h1>Résultat indisponible</h1>
          <p>{error.message}</p>
          <Link className="button button--primary" to="/dashboard">
            Retour à l’historique
          </Link>
        </section>
      </div>
    )
  }

  if (generation?.status !== 'done') {
    return (
      <div className="workspace-page">
        <section className="empty-state">
          <h1>La campagne n’est pas terminée.</h1>
          <p>Le résultat s’ouvre uniquement après validation du dossier complet.</p>
          <Link className="button button--primary" to={`/generations/${id}`}>
            Voir l’état de la campagne
          </Link>
        </section>
      </div>
    )
  }

  return (
    <div className="workspace-page workspace-page--wide">
      <header className="result-heading">
        <div>
          <Link className="text-link" to="/dashboard">
            <ArrowLeft size={16} aria-hidden="true" />
            Retour à l’historique
          </Link>
          <p className="eyebrow">Campagne terminée</p>
          <h1>Trois formats. Une même direction.</h1>
        </div>
        <div className="result-heading__actions">
          <button
            type="button"
            className="button button--secondary"
            onClick={() => setDrawerOpen(true)}
          >
            <Eye size={18} aria-hidden="true" />
            Voir les preuves
          </button>
          <button
            type="button"
            className="button button--primary"
            disabled={downloading}
            onClick={downloadBundle}
          >
            <DownloadSimple size={18} aria-hidden="true" />
            {downloading
              ? 'Préparation du dossier'
              : 'Télécharger le dossier ZIP'}
          </button>
        </div>
      </header>

      {error && (
        <div className="inline-alert inline-alert--error" role="alert">
          <p>{error.message}</p>
        </div>
      )}

      {evidenceError && (
        <div className="inline-alert inline-alert--error" role="alert">
          <div>
            <strong>Preuves indisponibles</strong>
            <p>{evidenceError.message}</p>
          </div>
          <button
            type="button"
            className="button button--secondary button--small"
            onClick={retryEvidence}
          >
            Réessayer les preuves
          </button>
        </div>
      )}

      <div className="result-layout">
        <section className="platform-studio" aria-labelledby="visual-title">
          <div className="platform-studio__heading">
            <div>
              <h2 id="visual-title">Visuels de campagne</h2>
              <span>{platform.dimensions} px</span>
            </div>
            <div className="platform-tabs" role="tablist" aria-label="Plateformes">
              {PLATFORMS.map((item, index) => (
                <button
                  key={item.id}
                  ref={(element) => {
                    tabRefs.current[index] = element
                  }}
                  type="button"
                  role="tab"
                  id={`tab-${item.id}`}
                  aria-controls={`panel-${item.id}`}
                  aria-selected={activePlatform === item.id}
                  tabIndex={activePlatform === item.id ? 0 : -1}
                  onClick={() => setActivePlatform(item.id)}
                  onKeyDown={(event) => selectPlatformByKey(event, index)}
                >
                  {item.label}
                </button>
              ))}
            </div>
          </div>
          <div
            id={`panel-${platform.id}`}
            className="platform-viewer"
            role="tabpanel"
            aria-labelledby={`tab-${platform.id}`}
            tabIndex="0"
          >
            {assets.urls[displayedFilename] ? (
              <img
                className="platform-viewer__image"
                src={assets.urls[displayedFilename]}
                alt={`${showEnhanced && hasEnhanced ? 'Finition publicitaire' : 'Visuel'} ${platform.label} de la campagne`}
              />
            ) : assets.isLoading ? (
              <div role="status" aria-label={`Chargement du visuel ${platform.label}`}>
                <div className="skeleton skeleton--poster" />
              </div>
            ) : (
              <div className="asset-failure">
                <WarningCircle size={30} weight="light" aria-hidden="true" />
                <div>
                  <h3>Visuel {platform.label} indisponible</h3>
                  <p>
                    Aucun fichier partiel n’est affiché comme résultat terminé.
                  </p>
                </div>
                <button
                  type="button"
                  className="button button--secondary"
                  onClick={retryEvidence}
                >
                  Réessayer les preuves
                </button>
              </div>
              )}
          </div>
          <div className="platform-studio__actions" aria-live="polite">
            {hasEnhanced && (
              <div className="platform-tabs platform-tabs--version" role="group" aria-label="Version du visuel">
                <button
                  type="button"
                  aria-pressed={!showEnhanced}
                  onClick={() => setShowEnhanced(false)}
                >
                  Original
                </button>
                <button
                  type="button"
                  aria-pressed={showEnhanced}
                  onClick={() => setShowEnhanced(true)}
                >
                  Finition publicitaire
                </button>
              </div>
            )}
            {!hasEnhanced && (
              <button
                type="button"
                className="button button--secondary button--small"
                disabled={enhancing}
                onClick={enhanceVisual}
              >
                <MagicWand size={17} aria-hidden="true" />
                {enhancing ? 'Création de la finition' : 'Ajouter une finition publicitaire'}
              </button>
            )}
            <button
              type="button"
              className="button button--quiet button--small"
              disabled={downloading || !assets.urls[displayedFilename]}
              onClick={downloadVisual}
            >
              <DownloadSimple size={17} aria-hidden="true" />
              Télécharger ce visuel
            </button>
          </div>
        </section>

        <aside className="copy-panel" aria-labelledby="copy-title">
          <div className="copy-panel__heading">
            <div>
              <p className="eyebrow">
                {generation.language === 'en' ? 'Texte anglais' : 'Texte français'}
              </p>
              <h2 id="copy-title">Texte de campagne</h2>
            </div>
            <button
              type="button"
              className="button button--quiet button--small"
              disabled={!copyText}
              onClick={copyCampaign}
              aria-label={`Copier le texte ${platform.label}`}
            >
              {copied ? (
                <Check size={17} aria-hidden="true" />
              ) : (
                <Copy size={17} aria-hidden="true" />
              )}
              {copied ? 'Copié' : 'Copier'}
            </button>
          </div>
          <div className="copy-panel__actions" aria-live="polite">
            <button
              type="button"
              className="button button--secondary button--small"
              disabled={captioning || platformCopy?.generated}
              onClick={generateCaption}
            >
              <MagicWand size={17} aria-hidden="true" />
              {captioning
                ? 'Génération de la légende'
                : platformCopy?.generated
                  ? 'Légende Qwen prête'
                  : `Générer la légende ${platform.label}`}
            </button>
            {!platformCopy?.generated && (
              <span className="copy-panel__hint">Une version sûre est affichée en attendant.</span>
            )}
          </div>
          {platformCopy?.text && (
            <div className="copy-block">
              <span>Publication {platform.label}</span>
              <p lang={copyLanguage}>{platformCopy.text}</p>
            </div>
          )}
          {platformCopy?.hashtags?.length > 0 && (
            <div className="copy-block">
              <span>Mots-dièse</span>
              <p lang={copyLanguage}>{platformCopy.hashtags.join(' ')}</p>
            </div>
          )}
          {platformCopy?.claims?.length > 0 && (
            <div className="copy-block">
              <span>Références vérifiées</span>
              <ul className="copy-claims">
                {platformCopy.claims.map((claim, index) => (
                  <li key={`${claim.evidence_id}-${index}`}>
                    <span lang={copyLanguage}>{claim.rendered_text}</span>
                    <code>{claim.evidence_id}</code>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {!copyText && (
            <p className="copy-panel__empty">
              Aucun texte validé n’est disponible dans ce dossier.
            </p>
          )}
          <div className="copy-panel__proof">
            <Check size={17} aria-hidden="true" />
            <p>Texte contraint par les informations vérifiées du brief.</p>
          </div>
        </aside>
      </div>

      <EvidenceDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        originalUrl={original.url}
        assetUrls={assets.urls}
        platform={platform}
        isLoading={evidenceLoading}
        error={evidenceError}
        onRetry={retryEvidence}
      />
    </div>
  )
}

export default Result
