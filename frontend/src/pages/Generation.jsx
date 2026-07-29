import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  ArrowClockwise,
  ArrowLeft,
  CheckCircle,
  CloudSlash,
  WarningCircle,
} from '@phosphor-icons/react'
import { generations, products } from '../api/client.js'
import CandidateSelector from '../components/CandidateSelector.jsx'
import GenerationTimeline from '../components/GenerationTimeline.jsx'
import { useGenerationPolling } from '../hooks/useGenerationPolling.js'
import { createIdempotencyKey } from '../utils/idempotency.js'

const ERROR_PRESENTATION = {
  AI_SERVICE_UNAVAILABLE: {
    title: 'Studio IA indisponible',
    body: 'La session de calcul ne répond pas. Relancez la campagne lorsque le service est prêt.',
    icon: CloudSlash,
  },
  AI_RUNTIME_LOST: {
    title: 'Session IA interrompue',
    body: 'La session GPU a changé ou s’est arrêtée. Aucun résultat dégradé n’a été enregistré.',
    icon: CloudSlash,
  },
  TARGET_NOT_FOUND: {
    title: 'Produit introuvable',
    body: 'Le produit n’a pas pu être isolé avec assez de confiance. Essayez une photo plus nette.',
    icon: WarningCircle,
  },
  EXTRACTION_FAILED: {
    title: 'Extraction impossible',
    body: 'Le contour du produit ne satisfait pas les contrôles de qualité.',
    icon: WarningCircle,
  },
  MASK_QUALITY_FAILED: {
    title: 'Contour insuffisant',
    body: 'Le masque ne préserve pas encore le produit avec la précision requise.',
    icon: WarningCircle,
  },
  CLAIM_SAFETY_FAILED: {
    title: 'Texte non conforme aux preuves',
    body: 'Le texte proposé ne respecte pas strictement les informations vérifiées.',
    icon: WarningCircle,
  },
}

function Generation() {
  const { id } = useParams()
  const navigate = useNavigate()
  const {
    generation,
    error: transportError,
    isLoading,
    isReconnecting,
    refresh,
  } = useGenerationPolling(id)
  const [originalUrl, setOriginalUrl] = useState(null)
  const [originalImageError, setOriginalImageError] = useState(null)
  const [originalImageLoading, setOriginalImageLoading] = useState(true)
  const [imageAttempt, setImageAttempt] = useState(0)
  const [selectionError, setSelectionError] = useState(null)
  const [selecting, setSelecting] = useState(false)
  const selectionRequestRef = useRef({ fingerprint: null, key: null })

  useEffect(() => {
    if (generation?.status === 'done') {
      navigate(`/result/${generation.id}`, { replace: true })
    }
  }, [generation, navigate])

  useEffect(() => {
    if (
      generation?.status !== 'error' ||
      generation?.error?.code !== 'TARGET_AMBIGUOUS' ||
      !generation.product_id
    ) {
      setOriginalUrl(null)
      setOriginalImageError(null)
      setOriginalImageLoading(true)
      return undefined
    }

    const controller = new AbortController()
    let objectUrl = null
    setOriginalUrl(null)
    setOriginalImageError(null)
    setOriginalImageLoading(true)

    products
      .getImage(generation.product_id, { signal: controller.signal })
      .then((response) => {
        if (controller.signal.aborted) return
        objectUrl = URL.createObjectURL(response.data)
        setOriginalUrl(objectUrl)
      })
      .catch((requestError) => {
        if (
          !controller.signal.aborted &&
          requestError.code !== 'REQUEST_CANCELLED'
        ) {
          setOriginalImageError(requestError)
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setOriginalImageLoading(false)
      })

    return () => {
      controller.abort()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [
    generation?.error?.code,
    generation?.id,
    generation?.product_id,
    generation?.status,
    imageAttempt,
  ])

  const retryOriginalImage = () => {
    setOriginalImageError(null)
    setOriginalImageLoading(true)
    setImageAttempt((current) => current + 1)
  }

  const handleCandidate = async (targetHint) => {
    setSelecting(true)
    setSelectionError(null)
    const fingerprint = JSON.stringify({
      source_generation_id: generation.id,
      target_hint: targetHint,
    })
    if (selectionRequestRef.current.fingerprint !== fingerprint) {
      selectionRequestRef.current = {
        fingerprint,
        key: createIdempotencyKey(),
      }
    }
    try {
      const response = await generations.create(
        generation.product_id,
        {
          language: generation.language,
          seed: generation.seed,
          source_generation_id: generation.id,
          target_hint: targetHint,
        },
        { idempotencyKey: selectionRequestRef.current.key }
      )
      const nextId = response.data.id || response.data.generation_id
      navigate(`/generations/${nextId}`, { replace: true })
    } catch (requestError) {
      setSelectionError(requestError)
    } finally {
      setSelecting(false)
    }
  }

  if (isLoading && !generation) {
    return (
      <div className="workspace-page">
        <div className="status-shell" aria-label="Chargement de la campagne">
          <div className="skeleton skeleton--title" />
          <div className="skeleton skeleton--body" />
          <div className="skeleton skeleton--timeline" />
        </div>
      </div>
    )
  }

  if (!generation && transportError) {
    return (
      <div className="workspace-page">
        <section className="empty-state empty-state--error">
          <CloudSlash size={36} weight="light" aria-hidden="true" />
          <h1>Connexion à la campagne impossible</h1>
          <p>{transportError.message}</p>
          <button type="button" className="button button--primary" onClick={refresh}>
            <ArrowClockwise size={18} aria-hidden="true" />
            Réessayer
          </button>
        </section>
      </div>
    )
  }

  if (
    generation?.status === 'error' &&
    generation.error?.code === 'TARGET_AMBIGUOUS'
  ) {
    return (
      <div className="workspace-page workspace-page--wide">
        <header className="page-heading">
          <div>
            <p className="eyebrow">Sélection requise</p>
            <h1>Confirmez le bon produit.</h1>
          </div>
          <Link className="text-link" to="/dashboard">
            <ArrowLeft size={16} aria-hidden="true" />
            Retour à l’historique
          </Link>
        </header>
        {originalUrl ? (
          <CandidateSelector
            imageUrl={originalUrl}
            candidates={generation.ambiguity?.candidates || []}
            onConfirm={handleCandidate}
            isSubmitting={selecting}
          />
        ) : originalImageLoading ? (
          <div
            className="studio-panel"
            role="status"
            aria-label="Chargement de la photo originale"
          >
            <div className="skeleton skeleton--media" />
          </div>
        ) : (
          <section
            className="asset-failure"
            role="alert"
            aria-labelledby="original-image-error-title"
          >
            <WarningCircle size={30} weight="light" aria-hidden="true" />
            <div>
              <h2 id="original-image-error-title">
                Photo originale indisponible
              </h2>
              <p>
                {generation.ambiguity?.candidates?.length === 1
                  ? 'Le cadre détecté reste disponible et sera affiché dès que la photo répond.'
                  : `${generation.ambiguity?.candidates?.length || 0} cadres détectés restent disponibles et seront affichés dès que la photo répond.`}
              </p>
              {originalImageError?.message && (
                <p>{originalImageError.message}</p>
              )}
            </div>
            <button
              type="button"
              className="button button--secondary"
              onClick={retryOriginalImage}
            >
              <ArrowClockwise size={18} aria-hidden="true" />
              Réessayer la photo
            </button>
          </section>
        )}
        {selectionError && (
          <div className="inline-alert inline-alert--error" role="alert">
            <p>{selectionError.message}</p>
          </div>
        )}
      </div>
    )
  }

  if (generation?.status === 'error') {
    const presentation =
      ERROR_PRESENTATION[generation.error?.code] || {
        title: 'La campagne a été arrêtée',
        body: generation.error?.message || 'La génération n’a pas abouti.',
        icon: WarningCircle,
      }
    const ErrorIcon = presentation.icon

    return (
      <div className="workspace-page">
        <section className="empty-state empty-state--error">
          <ErrorIcon size={38} weight="light" aria-hidden="true" />
          <p className="eyebrow">Échec explicite</p>
          <h1>{presentation.title}</h1>
          <p>{presentation.body}</p>
          <div className="empty-state__actions">
            <Link className="button button--primary" to="/new">
              Créer une nouvelle campagne
            </Link>
            <Link className="button button--quiet" to="/dashboard">
              Voir l’historique
            </Link>
          </div>
        </section>
      </div>
    )
  }

  return (
    <div className="workspace-page">
      <header className="page-heading">
        <div>
          <p className="eyebrow">
            {generation?.status === 'pending' ? 'En attente' : 'En création'}
          </p>
          <h1>Votre campagne prend forme.</h1>
        </div>
        <Link className="text-link" to="/dashboard">
          <ArrowLeft size={16} aria-hidden="true" />
          Retour à l’historique
        </Link>
      </header>

      {isReconnecting && (
        <div className="inline-alert" role="status" aria-live="polite">
          <CloudSlash size={20} aria-hidden="true" />
          <div>
            <strong>Reconnexion en cours</strong>
            <p>Le dernier état confirmé reste affiché.</p>
          </div>
        </div>
      )}

      <section className="status-shell" aria-live="polite">
        <div className="status-shell__intro">
          <div className="status-orbit" aria-hidden="true">
            <span>CA</span>
          </div>
          <div>
            <h2>
              {generation?.status === 'pending'
                ? 'Campagne enregistrée'
                : 'Traitement sur le studio GPU'}
            </h2>
            <p>
              Chaque étape apparaît uniquement après confirmation du service.
            </p>
          </div>
        </div>
        <GenerationTimeline
          status={generation?.status}
          stage={generation?.stage}
          completedStages={generation?.completed_stages}
        />
        <div className="status-shell__truth">
          <CheckCircle size={18} weight="light" aria-hidden="true" />
          <span>Aucun résultat incomplet ne sera présenté comme terminé.</span>
        </div>
      </section>
    </div>
  )
}

export { ERROR_PRESENTATION }
export default Generation
